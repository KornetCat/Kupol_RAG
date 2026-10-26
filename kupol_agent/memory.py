"""Persistent case memory. An accepted command is not a verified repair."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid


OUTCOMES = frozenset({"VERIFIED_SUCCESS", "FAILED", "UNKNOWN", "BLOCKED", "DRY_RUN", "RESOLVED_WITHOUT_ACTION"})
MODES = frozenset({"execute", "simulate", "replay", "observe", "dry_run"})
OBSERVED_TREND = "OBSERVED_TREND_UNCONFIRMED"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("A finite numeric timestamp is required")
    return float(value)


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A nonempty string is required")
    return value


class CaseStore:
    def __init__(self, db_path):
        self.path = str(db_path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
        CREATE TABLE IF NOT EXISTS incidents(
          id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, fault_code TEXT NOT NULL,
          subsystems TEXT NOT NULL, evidence TEXT NOT NULL, environment TEXT NOT NULL,
          occurred_at REAL NOT NULL, updated_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'OPEN');
        CREATE UNIQUE INDEX IF NOT EXISTS one_open_incident
          ON incidents(environment,fingerprint) WHERE state='OPEN';
        CREATE TABLE IF NOT EXISTS attempts(
          id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(id),
          runbook_id TEXT NOT NULL, runbook_version TEXT NOT NULL, environment TEXT NOT NULL,
          mode TEXT NOT NULL, actions TEXT NOT NULL, before_timestamp REAL NOT NULL,
          intent_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL DEFAULT 'PENDING');
        CREATE TABLE IF NOT EXISTS attempt_events(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL REFERENCES attempts(id),
          kind TEXT NOT NULL, payload TEXT NOT NULL, at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS outcomes(
          attempt_id TEXT PRIMARY KEY REFERENCES attempts(id), outcome TEXT NOT NULL,
          evidence TEXT NOT NULL, at REAL NOT NULL, learning_eligible INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS memory_environment ON attempts(environment,runbook_id,runbook_version);
        CREATE TABLE IF NOT EXISTS trend_observations(
          id TEXT PRIMARY KEY, environment TEXT NOT NULL, fingerprint TEXT NOT NULL,
          observation_code TEXT NOT NULL, target TEXT NOT NULL, signal TEXT NOT NULL,
          first_at REAL NOT NULL, last_at REAL NOT NULL, recorded_at REAL NOT NULL,
          observation_count INTEGER NOT NULL, first_values TEXT NOT NULL,
          latest_values TEXT NOT NULL, context TEXT NOT NULL, outcome TEXT NOT NULL,
          UNIQUE(environment,fingerprint));
        """)
        self._closed = False

    def record_observation(self, environment, observation, at, *, cooldown_s=300):
        """Persist a bounded fan-current trend, never a failure/repair or command.

        Only a reviewed numeric signal and small operating context are accepted;
        raw frames, model text, operator instructions and actions have no field.
        """
        _text(environment)
        at = _time(at)
        if (not isinstance(observation, dict) or set(observation) != {"code", "target", "signal", "values", "context"}
                or observation.get("code") != "INVERTER_FAN_CURRENT_RISING"
                or observation.get("target") != "solar_inverter_01"
                or observation.get("signal") != "solar_inverter_01.fan_current_a"):
            raise ValueError("Unsupported observational signal or fields")
        values, context = observation["values"], observation["context"]
        if (not isinstance(values, list) or not 3 <= len(values) <= 8
                or not all(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100 for value in values)
                or not all(right > left for left, right in zip(values, values[1:]))):
            raise ValueError("Observation needs a bounded increasing numeric series")
        numeric_limits = {"fan_rpm":(0,1000000), "inverter_temp_c":(-273.15,1000), "load_power_w":(0,1000000000)}
        if (not isinstance(context, dict) or set(context) - ({"fan_mode", "fan_state"} | numeric_limits.keys())
                or not isinstance(context.get("fan_mode"),str) or context.get("fan_mode") not in {"ON", "AUTO"}
                or context.get("fan_state") != "ON"):
            raise ValueError("Unsupported observational context")
        for key, value in context.items():
            if key in numeric_limits and (type(value) not in (int,float) or not math.isfinite(value)
                    or not numeric_limits[key][0] <= value <= numeric_limits[key][1]):
                raise ValueError("Observation context must contain finite reviewed numeric fields")
        if type(cooldown_s) not in (int,float) or not math.isfinite(cooldown_s) or cooldown_s < 0:
            raise ValueError("Observation cooldown must be finite and nonnegative")
        fingerprint = hashlib.sha256(_json([observation["code"],observation["target"],observation["signal"]]).encode()).hexdigest()
        row = self._db.execute("SELECT * FROM trend_observations WHERE environment=? AND fingerprint=?",
                               (environment,fingerprint)).fetchone()
        recorded_at = time.time()
        if row is not None and recorded_at - row["recorded_at"] < cooldown_s:
            return None
        identity = row["id"] if row is not None else uuid.uuid4().hex
        with self._db:
            if row is None:
                self._db.execute("INSERT INTO trend_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identity,environment,fingerprint,observation["code"],observation["target"],observation["signal"],
                     at,at,recorded_at,1,_json(values),_json(values),_json(context),OBSERVED_TREND))
            else:
                self._db.execute("""UPDATE trend_observations SET last_at=?,recorded_at=?,observation_count=observation_count+1,
                    latest_values=?,context=? WHERE id=?""",(at,recorded_at,_json(values),_json(context),identity))
        return "observation:" + identity

    def search_observations(self, query, environment, limit=2):
        """Return environment-local observations as non-authoritative RAG data."""
        from .retrieval import rank_texts
        if type(limit) is not int or limit <= 0:
            return []
        rows = self._db.execute("SELECT * FROM trend_observations WHERE environment=? ORDER BY recorded_at DESC,id LIMIT 100",
                                (environment,)).fetchall()
        observations = []
        for row in rows:
            context = json.loads(row["context"])
            first, latest = json.loads(row["first_values"]), json.loads(row["latest_values"])
            text = (f"Вентилятор инвертора: ток рос при одинаковом режиме. "
                    f"Локальное наблюдение отклонения; факт поломки не установлен. Код {row['observation_code']}. "
                    f"Узел {row['target']}, параметр {row['signal']}. Первое окно: {_json(first)}; "
                    f"последнее сохранённое окно: {_json(latest)}. Контекст: {_json(context)}. "
                    f"Время телеметрии первого/последнего окна: {row['first_at']}/{row['last_at']}; "
                    f"сохранённых окон: {row['observation_count']}. Исход: {OBSERVED_TREND}. "
                    "Тренд сам по себе не доказывает неисправность или её причину. Ремонт и эффективность действий "
                    "не проверялись; команд и полномочий на управление запись не содержит. "
                    "Сопоставить с текущими свежими показаниями, не считать прошлое наблюдение текущей аварией.")
            observations.append({"id":"observation:"+row["id"],"kind":"observation","category":"observed_trend",
                "source":f"sqlite:{self.path}#observation={row['id']}","section":"Ранее замеченный тренд тока вентилятора",
                "text":text,"observation_code":row["observation_code"],"target":row["target"],"signal":row["signal"],
                "environment":row["environment"],"first_at":row["first_at"],"last_at":row["last_at"],
                "observation_count":row["observation_count"],"subsystems":["thermal","energy"],
                "node_ids":[row["target"]],"signal_paths":[row["signal"]],"outcome":OBSERVED_TREND,
                "trust":False,"learning_eligible":False,"instruction_authority":False,"repair_causality":"UNPROVEN",
                "provenance":{"type":"observed_trend","environment":row["environment"],
                    "verification":OBSERVED_TREND,"first_at":row["first_at"],"last_at":row["last_at"],
                    "actual_execution":False,"repair_verified":False}})
        return rank_texts(observations,query,min(limit,2))

    def open_incident(self, fingerprint, fault_code, subsystem_ids: list, evidence: list,
                      environment: str, occurred_at: float) -> str:
        _text(fingerprint); _text(fault_code); _text(environment)
        at = _time(occurred_at)
        if not isinstance(subsystem_ids, list) or not all(isinstance(v, str) for v in subsystem_ids):
            raise ValueError("subsystem_ids must be a list of strings")
        if not isinstance(evidence, list):
            raise ValueError("evidence must be a list")
        systems, proof = _json(sorted(set(subsystem_ids))), _json(evidence)
        with self._db:
            # INSERT OR IGNORE plus the partial unique index also protects concurrent callers.
            incident_id = uuid.uuid4().hex
            self._db.execute("INSERT OR IGNORE INTO incidents VALUES(?,?,?,?,?,?,?,?,?)",
                (incident_id, fingerprint, fault_code, systems, proof, environment, at, at, "OPEN"))
            row = self._db.execute("SELECT id,fault_code FROM incidents WHERE environment=? AND fingerprint=? AND state='OPEN'",
                                   (environment, fingerprint)).fetchone()
            if row["fault_code"] != fault_code:
                raise ValueError("The active fingerprint belongs to a different fault code")
            self._db.execute("UPDATE incidents SET evidence=?,subsystems=?,updated_at=MAX(updated_at,?) WHERE id=?",
                             (proof, systems, at, row["id"]))
            return row["id"]

    def begin_attempt(self, incident_id, runbook_id, runbook_version, environment, mode,
                      actions: list, before_timestamp: float) -> str:
        for value in (incident_id, runbook_id, runbook_version, environment):
            _text(value)
        if mode not in MODES or not isinstance(actions, list):
            raise ValueError("Unknown mode or actions is not a list")
        before = _time(before_timestamp)
        action_json = _json(actions)
        incident = self._db.execute("SELECT environment FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if incident is None or incident["environment"] != environment:
            raise ValueError("Incident not found in the requested environment")
        key = hashlib.sha256(_json([incident_id, runbook_id, runbook_version, environment, mode,
                                   actions, before]).encode()).hexdigest()
        with self._db:
            attempt_id = uuid.uuid4().hex
            self._db.execute("INSERT OR IGNORE INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?)",
                (attempt_id, incident_id, runbook_id, runbook_version, environment, mode,
                 action_json, before, key, "PENDING"))
            row = self._db.execute("SELECT id FROM attempts WHERE intent_key=?", (key,)).fetchone()
            return row["id"]

    def end_observed_incident(self, fingerprint, environment, at):
        """A condition ceased to be reported; no causal repair success implied."""
        at = _time(at)
        row = self._db.execute("SELECT id FROM incidents WHERE environment=? AND fingerprint=? AND state='OPEN'",
                               (environment,fingerprint)).fetchone()
        if row is None:
            return None
        with self._db:
            self._db.execute("UPDATE incidents SET state='CONDITION_CLEARED_UNATTRIBUTED',updated_at=MAX(updated_at,?) WHERE id=?",
                             (at,row["id"]))
        return row["id"]

    def append_attempt_event(self, attempt_id, kind, payload: dict, at: float):
        _text(kind); at = _time(at)
        if not isinstance(payload, dict):
            raise ValueError("Event payload must be a dictionary")
        with self._db:
            if not self._db.execute("SELECT 1 FROM attempts WHERE id=?", (attempt_id,)).fetchone():
                raise ValueError("Unknown attempt")
            self._db.execute("INSERT INTO attempt_events(attempt_id,kind,payload,at) VALUES(?,?,?,?)",
                             (attempt_id, kind, _json(payload), at))

    def finish_attempt(self, attempt_id, outcome, evidence: dict, at: float):
        at = _time(at)
        if outcome not in OUTCOMES or not isinstance(evidence, dict):
            raise ValueError("Unknown outcome or evidence is not a dictionary")
        row = self._db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown attempt")
        proof = _json(evidence)
        existing = self._db.execute("SELECT outcome,evidence FROM outcomes WHERE attempt_id=?", (attempt_id,)).fetchone()
        if existing:
            if existing["outcome"] == outcome and existing["evidence"] == proof:
                return  # Safe replay of the same terminal result.
            raise ValueError("Attempt already has a different terminal outcome")
        executed = row["mode"] in {"execute", "simulate"} and evidence.get("actual_execution") is True
        fresh = evidence.get("fresh_observations")
        post = evidence.get("post_timestamp")
        success_proven = (executed and evidence.get("effect_confirmed") is True
                          and type(fresh) is int and fresh >= 2
                          and type(post) in (int, float) and math.isfinite(post)
                          and post > row["before_timestamp"])
        if outcome == "VERIFIED_SUCCESS" and not success_proven:
            raise ValueError("VERIFIED_SUCCESS requires actual execution and fresh confirmed effect")
        eligible = outcome == "VERIFIED_SUCCESS" or (outcome == "FAILED" and executed)
        with self._db:
            self._db.execute("INSERT INTO outcomes VALUES(?,?,?,?,?)", (attempt_id, outcome, proof, at, int(eligible)))
            self._db.execute("UPDATE attempts SET state=? WHERE id=?", (outcome, attempt_id))
            self._db.execute("INSERT INTO attempt_events(attempt_id,kind,payload,at) VALUES(?,?,?,?)",
                             (attempt_id, "outcome", _json({"outcome": outcome, "evidence": evidence}), at))
            if outcome in {"VERIFIED_SUCCESS", "RESOLVED_WITHOUT_ACTION"}:
                self._db.execute("UPDATE incidents SET state=?,updated_at=MAX(updated_at,?) WHERE id=?",
                                 ("RESOLVED" if outcome == "VERIFIED_SUCCESS" else outcome, at, row["incident_id"]))

    def stats(self, fault_code, runbook_id, runbook_version, environment) -> dict:
        rows = self._db.execute("""SELECT o.outcome,o.learning_eligible FROM outcomes o
          JOIN attempts a ON a.id=o.attempt_id JOIN incidents i ON i.id=a.incident_id
          WHERE i.fault_code=? AND a.runbook_id=? AND a.runbook_version=? AND a.environment=?""",
          (fault_code, runbook_id, runbook_version, environment)).fetchall()
        successes = sum(r["outcome"] == "VERIFIED_SUCCESS" and bool(r["learning_eligible"]) for r in rows)
        failures = sum(r["outcome"] == "FAILED" and bool(r["learning_eligible"]) for r in rows)
        unknown = sum(r["outcome"] == "UNKNOWN" for r in rows)
        return {"successes": successes, "failures": failures, "unknown": unknown,
                "verified_attempts": successes + failures,
                "score": successes / (successes + failures + 2) if successes else 0.0}

    def search_cases(self, query, environment, limit=5) -> list[dict]:
        from .retrieval import rank_texts
        rows = self._db.execute("""SELECT a.*,i.fault_code,i.subsystems,i.evidence AS incident_evidence,
          o.outcome,o.evidence AS result_evidence,o.at,o.learning_eligible
          FROM attempts a JOIN incidents i ON i.id=a.incident_id JOIN outcomes o ON o.attempt_id=a.id
          WHERE a.environment=? ORDER BY o.at DESC,a.id""", (environment,)).fetchall()
        cases = []
        for row in rows:
            subsystems = json.loads(row["subsystems"])
            proof, actions = json.loads(row["result_evidence"]), json.loads(row["actions"])
            text = (f"Отказ {row['fault_code']}. Подсистемы {', '.join(subsystems)}. "
                    f"Сценарий {row['runbook_id']} версия {row['runbook_version']}. "
                    f"Результат {row['outcome']}. Действия {_json(actions)}. Доказательства {_json(proof)}.")
            cases.append({"id": f"case:{row['id']}", "kind": "case", "source": f"sqlite:{self.path}#attempt={row['id']}",
                "text": text, "fault_code": row["fault_code"], "runbook_id": row["runbook_id"],
                "runbook_version": row["runbook_version"], "version": row["runbook_version"],
                "outcome": row["outcome"], "environment": row["environment"], "mode": row["mode"],
                "subsystems": subsystems, "evidence": proof, "actions": actions,
                "trust": bool(row["learning_eligible"]), "instruction_authority": False,
                "provenance": {"type": "agent_case", "incident_id": row["incident_id"],
                    "attempt_id": row["id"], "environment": row["environment"], "mode": row["mode"],
                    "outcome_at": row["at"], "verification": "stored_outcome", "runbook_version": row["runbook_version"]}})
        return rank_texts(cases, query, limit)

    def recover_pending(self, environment) -> int:
        pending = self._db.execute("SELECT id,before_timestamp FROM attempts WHERE environment=? AND state='PENDING'",
                                   (environment,)).fetchall()
        with self._db:
            for row in pending:
                self.finish_attempt(row["id"], "UNKNOWN", {"actual_execution": False,
                    "effect_confirmed": False, "reason": "restart_requires_state_reconciliation"}, row["before_timestamp"])
        return len(pending)

    def recent_incidents(self, limit=20) -> list:
        if type(limit) is not int or limit <= 0:
            return []
        rows = self._db.execute("SELECT * FROM incidents ORDER BY updated_at DESC,id LIMIT ?", (limit,)).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["subsystems"] = json.loads(data["subsystems"])
            data["evidence"] = json.loads(data["evidence"])
            result.append(data)
        return result

    def attempts_for_incident(self, incident_id) -> list[dict]:
        rows = self._db.execute("""SELECT id,state,runbook_id,runbook_version,mode,environment,
          before_timestamp,actions FROM attempts WHERE incident_id=? ORDER BY before_timestamp,id""",
          (incident_id,)).fetchall()
        return [{"id": row["id"], "state": row["state"], "runbook_id": row["runbook_id"],
                 "version": row["runbook_version"], "runbook_version": row["runbook_version"],
                 "mode": row["mode"], "environment": row["environment"],
                 "before_timestamp": row["before_timestamp"], "actions": json.loads(row["actions"])}
                for row in rows]

    def close(self):
        if not self._closed:
            self._db.close()
            self._closed = True
