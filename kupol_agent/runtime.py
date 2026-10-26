"""Continuous local RAG runtime: one reader, bounded workers, serialized writes."""
from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid
from urllib.parse import urlsplit

from .chat import DeepSeekChat
from .control import (ControlError, MACHINES, available_controls, canonical_request, prepare_upload, safe_filename,
                      validate_manual, verify_effect, normalize_files_response, _fresh)
from .credentials import load_team_keys
from .engine import Agent, Config, COMMANDS, diagnose, number, quality
from .history import load_manifest
from .llm import DeepSeekPlanner, PlannerError
from .locking import ExecutionLock
from .memory import CaseStore
from .model_policy import ModelPolicy
from .monitoring import TemporalMonitor
from .retrieval import DocumentIndex, HybridRetriever
from .transport import LiveTransport, TransportError
from . import __version__

_ROOT = Path(__file__).resolve().parents[1]
_SENSITIVE = re.compile(r"^(?:key|.*_key|token|.*_token|authorization|password|secret)$", re.I)


def _now():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _address(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Введите актуальный адрес сети Купола")
    value = value.strip()
    if "://" not in value:
        value = "ws://" + value
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"ws", "wss"} and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.fragment and not parsed.query and parsed.port != 0
                 and not any(c.isspace() for c in value))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Нужен адрес вида IP:порт или ws://адрес:порт/путь")
    return value


def _fault_signature(faults):
    # Multiple lines/sensors can share one node and fault class; retain the
    # signal identity so an explanation for line 5 is not reused for line 2.
    return tuple(sorted((str(fault.get("code", "")), str(fault.get("target", "")),
                         tuple(sorted(str(item.get("id", "")) for item in fault.get("evidence", []) if isinstance(item, dict))))
                        for fault in faults if isinstance(fault, dict)))


def _merge_faults(*groups):
    result, seen = [], set()
    for group in groups:
        for fault in group:
            key = (fault.get("code"),fault.get("target"),tuple(sorted(str(item.get("id",""))
                   for item in fault.get("evidence",[]) if isinstance(item,dict))))
            if key not in seen:
                result.append(fault)
                seen.add(key)
    return result


class _GuidedPlanner:
    def __init__(self, runtime, planner):
        self.runtime, self.planner, self.model = runtime, planner, planner.model

    def choose(self, report, candidates, context):
        runtime = self.runtime
        epoch = runtime._manual_epoch
        runtime.policy.ensure_ready()
        report = {**report, "OPERATOR_GUIDANCE_TRUSTED": deepcopy(list(runtime._directives))}
        context = runtime._rerank_sync(str(report.get("code", "")) + " " + str(report.get("message", "")), context, self.planner)
        result = self.planner.choose(report, candidates, context)
        runtime.policy.validate_response(result.get("served_model"))
        if runtime._manual_epoch == epoch:
            runtime._planned_epoch = epoch
        return result


class _AutoTransport:
    def __init__(self, runtime, epoch, session_id, timestamp):
        self.runtime, self.epoch, self.session_id, self.timestamp = runtime, epoch, session_id, timestamp

    @property
    def role(self):
        return self.runtime._connection.get("role", "guest")

    @property
    def catalog(self):
        return self.runtime.catalog

    @property
    def latest_frame(self):
        return self.runtime._frame

    @property
    def allow_control(self):
        r = self.runtime
        return (r.autonomous and r._manual_pending == 0 and self.epoch == r._manual_epoch
                and self.session_id == r._session_id and not r._hazard_latched
                and r._connection.get("state") == "connected" and self.role == "team"
                and r._autonomy_confirmation_current()
                and (not r._directives or r._planned_epoch == self.epoch))

    async def send(self, request):
        r = self.runtime
        async with r._command_lock:
            allowed, reason = self.can_control(request)
            if not allowed:
                raise TransportError(reason)
            frame = r._require_team(request.get("node_id"))
            if frame.get("timestamp") != self.timestamp:
                raise TransportError("Newer telemetry requires a new automatic decision")
            validate_manual(request, r.catalog, frame)
            transport = r._transport
            transport.allow_control = True
            transport.control_guard = self._pre_send_guard
            try:
                return await transport.send(deepcopy(request))
            finally:
                transport.allow_control = False
                transport.control_guard = None

    def _pre_send_guard(self, request):
        # The original request was strictly validated before send(). Only the
        # ID appended by the transport is removed for this second validation.
        canonical = {key:value for key,value in request.items() if key != "request_id"}
        allowed, _ = self.can_control(canonical)
        if not allowed:
            return False
        transport = self.runtime._transport
        frame = getattr(transport,"latest_frame",None) or self.runtime._frame
        if not isinstance(frame,dict) or frame.get("timestamp") != self.timestamp:
            return False
        if self.runtime._autonomy_error(frame=frame):
            return False
        data = frame.get("data")
        nodes = data.get("nodes") if isinstance(data,dict) else None
        if not isinstance(nodes,dict) or self.runtime._agent is None:
            return False
        context = {**deepcopy(self.runtime.context),"_frame_role":frame.get("role"),
            "_received_monotonic":frame.get("_received_monotonic")}
        context["_critical_incident"] = any(fault.get("severity") == "critical"
            for fault in diagnose(nodes,frame.get("timestamp"),context,self.runtime.config))
        allowed,_ = self.runtime._agent.gate([canonical],{},nodes,frame.get("timestamp"),context,transport)
        if not allowed:
            return False
        validate_manual(canonical,self.runtime.catalog,frame)
        return True

    def can_control(self, request):
        if not self.allow_control:
            return False, "AUTOMATIC_PLAN_SUPERSEDED_PAUSED_OR_REVOKED"
        if (request.get("node_id"),request.get("action")) not in COMMANDS:
            return False, "NOT_REVIEWED_AUTOMATIC_ACTION"
        error = self.runtime._autonomy_error()
        if error:
            return False,error
        if any(item.get("node_id") == request.get("node_id") for item in self.runtime._overrides):
            return False, "PERSISTENT_OPERATOR_OVERRIDE"
        if any(fault.get("severity") == "critical" for fault in self.runtime._temporal_result.get("faults", [])):
            return False, "CRITICAL_TEMPORAL_OBSERVATION_HAS_PRIORITY"
        return True, "ALLOWED"


class _RerankTransport:
    def __init__(self, runtime, planner):
        self.runtime, self.planner = runtime, planner
        self.base_url, self.model = planner.base_url, planner.model
        self.policy_cache_key = (runtime.profile, runtime.policy.base_url, runtime.policy.expected_served)

    def _safe(self, value):
        return self.runtime._redact(self.planner._safe(value))

    def _request(self, payload):
        runtime = self.runtime
        runtime.policy.ensure_ready()
        started = time.monotonic()
        try:
            response = self.planner._request(runtime._redact(payload))
            runtime.policy.validate_response(response.get("model"))
            usage = response.get("usage", {})
            runtime._audit("llm_call", model=self.model, served_model=response.get("model"), purpose="rag_rerank",
                latency_ms=round((time.monotonic()-started)*1000), prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"), ok=True, error=None, summary="Упорядочены существующие фрагменты RAG")
            return response
        except Exception as exc:
            runtime._audit("llm_call", model=self.model, purpose="rag_rerank", latency_ms=round((time.monotonic()-started)*1000),
                           ok=False, error=type(exc).__name__, summary=None)
            raise


class DomeRuntime:
    def __init__(self, config_path=None, context_path=None, keys_archive=None, profile="training",
                 data_dir=Path("data"), transport_factory=LiveTransport, planner_factory=DeepSeekPlanner,
                 chat_factory=DeepSeekChat, agent_factory=Agent, retriever=None, *,
                 reconnect_delay=3, verification_timeout=12, policy_factory=ModelPolicy):
        if profile not in {"training", "competition"}:
            raise ValueError("Неизвестный профиль")
        self.profile, self.policy_factory = profile, policy_factory
        self.policy = policy_factory(profile)
        self.data_dir = Path(data_dir)
        self.config = Config.from_json(config_path or os.getenv("AGENT_CONFIG_FILE"))
        # A saved numeric configuration is not consent to execute in a new
        # live session. Approval is granted only by configure_autonomy().
        self.config = replace(self.config,approved_live=False)
        self._load_autonomy_numbers()
        context_path = context_path or os.getenv("AGENT_CONTEXT_FILE")
        self.context = json.loads(Path(context_path).read_text(encoding="utf-8-sig")) if context_path else {}
        if not isinstance(self.context, dict):
            raise ValueError("Контекст оператора должен быть JSON-объектом")
        self._base_context = deepcopy(self.context)
        self._work_context_override = None
        self.keys_archive = Path(keys_archive) if keys_archive else _ROOT / "Dome_RAG-main.zip"
        self.transport_factory, self.planner_factory, self.chat_factory = transport_factory, planner_factory, chat_factory
        self.agent_factory, self.retriever = agent_factory, retriever
        self._provided_retriever = retriever
        self.reconnect_delay, self.verification_timeout = reconnect_delay, verification_timeout
        self.autonomous = False
        self._autonomy_confirmation = None
        self._autonomy_previous_flags = None
        self.team = 4
        self._dome_key = self._deepseek_key = ""
        self._secrets = set()
        self._connection = {"state":"disconnected", "address":"", "role":None, "team":None, "last_error":None}
        self._frame = None
        self._frame_session_id = None
        self._received_at = None
        self._transport = None
        self._session_id = 0
        self._connect_task = self._worker_task = self._watchdog_task = self._analysis_task = None
        self._agent = self._store = self._planner = self._chat_client = None
        self._closed = self._started = False
        self._command_lock, self._connection_lock = asyncio.Lock(), asyncio.Lock()
        self._changed = asyncio.Condition()
        self._analysis_queue = asyncio.Queue(maxsize=1)
        self._subscribers = set()
        self._messages, self._events = deque(maxlen=80), deque(maxlen=120)
        self._diagnostics = {"faults":[], "outcomes":[]}
        self._engine_faults = []
        self._reported_incidents = {}
        self._temporal = TemporalMonitor(max_age_s=self.config.max_age_s)
        self._temporal_result = {"faults":[], "metrics":{}, "changed":False}
        self._directives, self._overrides = deque(maxlen=40), []
        self._manual_epoch = self._planned_epoch = 0
        self._manual_pending = 0
        self._directive_pending = 0
        self._hazard_latched = False
        self._hazard_reason = None
        self._last_advisory = 0.0
        self._last_advisory_signature = None
        self._history = self._summarize_history(load_manifest())
        self._run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid.uuid4().hex[:6]
        self._seq = 0
        self._log = None
        self._lease = None
        self._audit_lock = threading.Lock()
        self._rerank_lock = threading.Lock()
        self._load_directives()

    @property
    def catalog(self):
        catalog = getattr(self._transport, "catalog", {})
        return catalog if isinstance(catalog, dict) else {}

    @property
    def environment(self):
        # The user identified this host as the virtual Dome. Keep its memory
        # separate without persisting an IP address or restricting real hosts.
        host = urlsplit(self._connection.get("address","")).hostname
        kind = "virtual" if host == "janus-server.a.2na.am" else "live"
        return f"{kind}:{self.profile}:team{self.team}"

    def _redact(self, value):
        if isinstance(value, dict):
            if isinstance(value.get("content"), str) and "file_name" in value:
                content = value["content"].encode("utf-8")
                value = {**value, "content": {"bytes":len(content), "sha256":hashlib.sha256(content).hexdigest()}}
            return {str(k):"[СКРЫТО]" if _SENSITIVE.fullmatch(str(k)) else self._redact(v) for k, v in value.items()
                    if k != "_received_monotonic"}
        if isinstance(value, (tuple, list, deque)):
            return [self._redact(v) for v in value]
        if isinstance(value, str):
            for secret in self._secrets | {self._dome_key, self._deepseek_key}:
                if secret:
                    value = value.replace(secret, "[СКРЫТО]")
            return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[СКРЫТО]", value)
        if type(value) is float and not math.isfinite(value):
            return None
        return value

    def _audit(self, kind, **fields):
        with self._audit_lock:
            self._seq += 1
            sequence = self._seq
            record = self._redact({"ts":_now(), "run_id":self._run_id, "seq":sequence, "kind":kind, **fields})
            if self._log:
                self._log.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                self._log.flush()
            return sequence

    def _event(self, kind, message, **fields):
        event = self._redact({"kind":kind, "message":message, "ts":_now(), **fields})
        self._events.append(event)
        self._publish("event", event)

    def _publish(self, kind, data):
        item = {"type":kind, "data":self._redact(data)}
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(item)

    def _broadcast(self):
        self._publish("state", self.snapshot())

    def subscribe(self):
        queue = asyncio.Queue(maxsize=16)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
        self._subscribers.discard(queue)

    @staticmethod
    def _summarize_history(manifest):
        return {k:manifest.get(k) for k in ("schema_version", "source_repository", "source_revision", "date_range", "counts", "classes", "days", "warnings")}

    def history_summary(self):
        return deepcopy(self._history)

    def snapshot(self):
        telemetry = deepcopy(self._frame) if isinstance(self._frame, dict) else None
        if telemetry is not None:
            received = telemetry.pop("_received_monotonic", None)
            telemetry["age_s"] = round(max(0, time.monotonic() - received), 1) if number(received) else None
            telemetry["received_at"] = self._received_at
        return self._redact({"connection":{**self._connection,"session_id":self._session_id}, "profile":self.profile, "autonomous":self.autonomous,
            "model":self.policy.model, "model_policy":self.policy.public_status(),
            "credentials":{"dome":bool(self._dome_key), "deepseek":bool(self._deepseek_key)},
            "telemetry":telemetry, "catalog":self.catalog, "manual_controls":available_controls(self.catalog),
            "diagnostics":self._diagnostics,
            "work_context":{"required_fume":self.context.get("required_fume") is True,
                            "declared":self._work_context_override is not None},
            "autonomy_settings":{"min_airflow_pct":self.config.min_airflow_pct,
                "min_supply_flow_m3_h":self.config.min_supply_flow_m3_h,
                "min_cooling_flow_l_min":self.config.min_cooling_flow_l_min,
                "confirmed":self._autonomy_confirmation_current()},
            "history":self._history, "events":list(self._events), "messages":list(self._messages),
            "safety":{"hazard_latched":self._hazard_latched, "reason":self._hazard_reason,
                      "emergency_stop":self.context.get("emergency_stop"), "autonomy_ready":self._autonomy_error() is None,
                      "autonomy_reason":self._autonomy_error()},
            "directives":{"items":list(self._directives), "overrides":self._overrides,
                          "pending":self._directive_pending > 0,
                          "pending_kind":"directive" if self._directive_pending else None,
                          "operation_pending":self._manual_pending > 0}})

    def _load_directives(self):
        path = self.data_dir / "directives.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("schema_version") in {1, 2}:
                self._directives.extend({**item, **({"legacy":True} if item.get("message_kind") != "directive" else {})}
                    for item in value.get("items", [])[-40:]
                    if isinstance(item, dict) and isinstance(item.get("text"), str))
                self._overrides = [item for item in value.get("overrides", [])[:128] if isinstance(item, dict) and isinstance(item.get("node_id"), str)]
                for item in self._directives:
                    context = item.get("context")
                    if (item.get("context_kind") == "work" and isinstance(context,dict)
                            and set(context) == {"required_fume"} and type(context["required_fume"]) is bool):
                        self._work_context_override = context["required_fume"]
                        self.context["required_fume"] = context["required_fume"]
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        try:
            value = json.loads((self.data_dir / "safety.json").read_text(encoding="utf-8"))
            self._hazard_latched = value.get("hazard_latched") is True
            self._hazard_reason = value.get("reason") if self._hazard_latched else None
        except (OSError, ValueError, AttributeError):
            pass

    def _persist_directives(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        value = self._redact({"schema_version":2, "items":list(self._directives), "overrides":self._overrides})
        # Network addresses are session configuration, not persistent instructions.
        for item in value["items"]:
            item["text"] = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b", "[адрес текущего сеанса]", item["text"])
        target = self.data_dir / "directives.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)

    def _keys(self, payload=None):
        payload = payload or {}
        self.team = payload.get("team", self.team)
        if type(self.team) is not int or not 1 <= self.team <= 1000:
            raise ValueError("Некорректный номер команды")
        keys = {}
        if self.keys_archive.is_file():
            keys = load_team_keys(self.keys_archive, self.team)
        self._secrets.update(v for v in keys.values() if isinstance(v, str) and v)
        self._dome_key = os.getenv("DOME_KEY") or payload.get("dome_key") or self._dome_key or keys.get("dome_key", "")
        self._deepseek_key = os.getenv("DEEPSEEK_API_KEY") or payload.get("deepseek_key") or self._deepseek_key or keys.get("deepseek_api_key", "")
        for value in (self._dome_key, self._deepseek_key):
            if not isinstance(value, str) or "\n" in value or "\r" in value:
                raise ValueError("Некорректный формат ключа")
            if value:
                self._secrets.add(value)
        options = dict(api_key=self._deepseek_key, model=self.policy.model, base_url=self.policy.base_url,
                       allow_custom_endpoint=self.policy.allow_custom_endpoint)
        self._planner = self.planner_factory(**options)
        self._chat_client = self.chat_factory(**options)

    async def start(self):
        if self._started:
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._lease = ExecutionLock(self.data_dir / "cases.execute.lock")
            self._lease.__enter__()
            # A retry after failed startup owns a new audit file, not an append
            # to a half-created run or an exclusive-open collision.
            self._run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid.uuid4().hex[:6]
            self._seq = 0
            log_dir = Path(os.getenv("AGENT_LOG_DIR") or self.config.log_dir or self.data_dir / "logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log = (log_dir / f"agent_{self._run_id}.jsonl").open("x", encoding="utf-8", buffering=1)
            self._store = CaseStore(self.data_dir / "cases.sqlite")
            if self.retriever is None:
                self.retriever = HybridRetriever(DocumentIndex([Path(__file__).parent / "knowledge"]), self._store)
            self._keys()
            self._audit("start", agent="kupol-web-rag", version=__version__, models=[self.policy.model],
                        config={"profile":self.profile, "autonomous":False})
            self._store.recover_pending(self.environment)
            self._worker_task = asyncio.create_task(self._analysis_worker())
            self._watchdog_task = asyncio.create_task(self._watchdog())
            self._started = True
            if os.getenv("DOME_URL"):
                await self.connect({"address":os.environ["DOME_URL"], "autonomous":os.getenv("AGENT_AUTONOMOUS") == "1"})
        except BaseException:
            await self._rollback_startup()
            raise

    async def _rollback_startup(self):
        tasks = [task for task in (self._connect_task, self._analysis_task, self._worker_task, self._watchdog_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._connect_task = self._analysis_task = self._worker_task = self._watchdog_task = None
        self._transport = None
        self._drain_analysis()
        try:
            if self._store:
                self._store.close()
        finally:
            self._store = None
            self.retriever = self._provided_retriever
            try:
                if self._log:
                    self._log.close()
            finally:
                self._log = None
                if self._lease:
                    self._lease.__exit__(None, None, None)
                self._lease = None
                self._started = False
                self._connection.update(state="disconnected", role=None, team=None)

    async def close(self):
        if self._closed:
            return
        self._closed = True
        await self.disconnect()
        for task in (self._worker_task, self._watchdog_task):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (self._worker_task, self._watchdog_task) if t), return_exceptions=True)
        if self._agent:
            await self._agent.shutdown()
            self._agent = None
        self._audit("stop", reason="runtime_shutdown")
        if self._store:
            self._store.close()
        if self._log:
            self._log.close()
            self._log = None
        if self._lease:
            self._lease.__exit__(None, None, None)
            self._lease = None

    async def connect(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Ожидаются параметры подключения")
        address = _address(payload.get("address") or os.getenv("DOME_URL", ""))
        profile = payload.get("profile", self.profile)
        if profile not in {"training", "competition"} or type(payload.get("autonomous", False)) is not bool:
            raise ValueError("Некорректный профиль или режим автономности")
        async with self._connection_lock:
            await self._disconnect_inner()
            self.profile, self.policy = profile, self.policy_factory(profile)
            self._keys(payload)
            if not self._dome_key:
                raise ValueError("Задайте ключ Купола команды в окружении, архиве или поле подключения")
            self._connection["address"] = address
            recovered = self._store.recover_pending(self.environment)
            if recovered:
                self._audit("observation", summary="В новом профиле восстановлены незавершённые попытки как UNKNOWN",
                            metrics={"recovered_attempts":recovered}, anomalies=["RESTART_REQUIRES_RECONCILIATION"])
            self._frame = None
            self._temporal.reset()
            self._connection.update(state="connecting", address=address, role=None, team=None, last_error=None)
            self._connect_task = asyncio.create_task(self._network_loop(address, payload.get("autonomous", False)))
            self._broadcast()
        return self.snapshot()

    async def disconnect(self):
        async with self._connection_lock:
            await self._disconnect_inner()
        return self.snapshot()

    async def _disconnect_inner(self):
        self._revoke_autonomy_confirmation()
        self._manual_epoch += 1
        self._session_id += 1
        self._frame_session_id = None
        self._connection.update(state="disconnected", role=None, team=None)
        if self._transport is not None:
            self._transport.allow_control = False
        self._drain_analysis()
        await self._preempt_analysis()
        if self._connect_task:
            self._connect_task.cancel()
            await asyncio.gather(self._connect_task, return_exceptions=True)
            self._connect_task = None
        self._transport = None
        self._drain_analysis()
        self._audit("connection", event="disconnected", role=None, team=None, details="operator_or_shutdown")
        self._broadcast()

    def _drain_analysis(self):
        while not self._analysis_queue.empty():
            try:
                self._analysis_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _transport_log(self, kind, **fields):
        if kind == "incoming":
            payload = fields.get("payload")
            if not isinstance(payload, dict):
                return
            if payload.get("type") == "telemetry":
                self._fast_safety(payload)
            elif payload.get("type") == "session_revoked":
                self._revoke_autonomy_confirmation()
                if self._transport:
                    self._transport.allow_control = False
                self._connection.update(role="guest", state="waiting", last_error="Сессия отозвана организатором")
                self._audit("connection", event="revoked", role="guest", team=self.team, details=payload.get("reason"))
            elif payload.get("type") == "meeting_message":
                self._audit("message", direction="in", sender=payload.get("sender"), text=payload.get("text"))
            return
        if kind == "connection":
            self._audit(kind, **fields)
        elif kind in {"transport_queue_drop", "error"}:
            self._audit(kind, **fields)

    def _fast_safety(self, frame):
        nodes = frame.get("data", {}).get("nodes", {}) if isinstance(frame.get("data"), dict) else {}
        if not isinstance(nodes, dict):
            return
        smoke = any(isinstance(nodes.get(n), dict) and (nodes[n].get("smoke_detected") is True
                    or nodes[n].get("alarm_state") == "ALARM") for n in ("smoke_detector_01", "smoke_detector_02"))
        plc = nodes.get("dome_automation_01", {})
        interlocks = plc.get("interlocks", {}) if isinstance(plc, dict) else {}
        il = interlocks.get("IL_SMOKE", {}) if isinstance(interlocks, dict) else {}
        if smoke or (isinstance(il, dict) and il.get("state") == "TRIPPED") or self.context.get("emergency_stop") is True:
            self.autonomous = False
            if self._transport:
                self._transport.allow_control = False
            if not self._hazard_latched:
                self._hazard_latched, self._hazard_reason = True, "SMOKE_OR_EMERGENCY_STOP"
                self._audit("decision", situation="Дым или аварийная остановка", cause=self._hazard_reason,
                    confidence=1.0, evidence=[], plan=["Прекратить опасные операции, предупредить людей, уведомить ответственного"], source="rules")
                self._event("critical", "Дым/аварийная остановка: управление заблокировано, действуйте по инструктажу")
                (self.data_dir / "safety.json").write_text(json.dumps({"hazard_latched":True, "reason":self._hazard_reason, "at":_now()}), encoding="utf-8")
                self._broadcast()

    async def _network_loop(self, address, requested_auto):
        delay = self.reconnect_delay
        while not self._closed:
            refresh = None
            try:
                self._revoke_autonomy_confirmation()
                self._session_id += 1
                self._frame = None
                self._received_at = None
                self._frame_session_id = None
                self._temporal.reset()
                self._engine_faults = []
                self._diagnostics = {"faults":[], "outcomes":[]}
                self._drain_analysis()
                self._connection["state"] = "connecting"
                transport = self.transport_factory(address, self._dome_key, logger=self._transport_log,
                                                   max_rate=4, allow_control=False)
                self._transport = transport
                async with transport:
                    if transport.role == "team" and str(transport.team) != str(self.team):
                        raise TransportError("Авторизованная команда не соответствует выбранной")
                    self._connection.update(state="connected" if transport.role == "team" else "waiting",
                        role=transport.role, team=transport.team, last_error=None, connected_at=_now())
                    self._event("connection", "Подключено к Куполу" if transport.role == "team" else "Гостевой режим: ожидание активной смены")
                    ref = self._audit("decision", situation="Подключение", cause="read_current_catalog", confidence=1,
                        evidence=[], plan=[{"type":"describe_nodes"}], source="rules")
                    self._audit("action", ref=ref, request={"type":"describe_nodes"}, response={"success":True,"nodes":len(transport.catalog)})
                    self._broadcast()
                    if transport.role != "team":
                        await asyncio.sleep(max(3, self.reconnect_delay))
                        continue
                    delay = self.reconnect_delay
                    self._make_agent()
                    if requested_auto:
                        self._event("warning","После подключения подтвердите критерии и безопасное состояние в форме автономности; исполнение не включено")
                        requested_auto = False
                    refresh = asyncio.create_task(self._refresh_catalog(transport))
                    async for frame in transport.frames():
                        await self._consume(frame)
                    raise TransportError("Поток Купола закрыт")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connection.update(state="reconnecting", last_error=self._redact(str(exc)))
                self._audit("connection", event="disconnected", role=self._connection.get("role"), team=self.team, details=type(exc).__name__)
                self._event("connection", "Связь прервана; повторное подключение с паузой", error=str(exc), retry_after_s=delay)
            finally:
                self._revoke_autonomy_confirmation()
                if refresh:
                    refresh.cancel()
                    await asyncio.gather(refresh, return_exceptions=True)
                if self._transport:
                    self._transport.allow_control = False
                self._transport = None
                self._frame_session_id = None
                if self._connection.get("state") == "connected":
                    self._connection.update(state="reconnecting", role=None, team=None)
                self._drain_analysis()
                await self._preempt_analysis()
                self._drain_analysis()
                self._broadcast()
            await asyncio.sleep(max(.01, delay))
            delay = min(30, max(self.reconnect_delay, delay * 2))

    async def _refresh_catalog(self, transport):
        while True:
            await asyncio.sleep(60)
            async with self._command_lock:
                if transport is not self._transport:
                    return
                ref = self._audit("decision", situation="Обновление каталога", cause="periodic_rights_check", confidence=1,
                    evidence=[], plan=[{"type":"describe_nodes"}], source="rules")
                response = await transport.send({"type":"describe_nodes"})
                self._audit("action", ref=ref, request={"type":"describe_nodes"}, response={"success":response.get("success"),"nodes":len(transport.catalog)})
                self._broadcast()

    def _make_agent(self):
        if self._agent is not None:
            self._invalidate_plans()
        config = replace(self.config, log_dir=None)
        self._agent = self.agent_factory(self._store, self.retriever, planner=_GuidedPlanner(self, self._planner),
                                        config=config, environment=self.environment, mode="execute" if self.autonomous else "observe")
        self._agent._emit = self._audit

    async def _consume(self, frame):
        if not isinstance(frame, dict):
            return
        frame = deepcopy(frame)
        frame.setdefault("_received_monotonic", time.monotonic())
        self._fast_safety(frame)
        if (isinstance(self._frame, dict) and number(self._frame.get("timestamp"))
                and number(frame.get("timestamp")) and frame["timestamp"] <= self._frame["timestamp"]):
            return
        self._frame, self._received_at = frame, _now()
        self._frame_session_id = self._session_id
        self._temporal_result = self._temporal.observe(frame)
        fast_faults = self._temporal_result["faults"]
        self._remember_precursor(frame, fast_faults)
        self._remember_reported_generator(frame,fast_faults)
        self._diagnostics["temporal_faults"] = fast_faults
        self._diagnostics["faults"] = _merge_faults(self._engine_faults,fast_faults)
        if self._temporal_result["changed"]:
            for fault in fast_faults:
                self._event(fault["severity"], fault["message"], code=fault["code"], target=fault["target"])
        if self._analysis_queue.full():
            self._analysis_queue.get_nowait()
        self._analysis_queue.put_nowait((self._session_id, frame, deepcopy(self._temporal_result)))
        async with self._changed:
            self._changed.notify_all()
        self._broadcast()

    def _remember_reported_generator(self, frame, faults):
        if self._store is None or frame.get("role") != "team":
            return
        data = frame.get("data")
        nodes = data.get("nodes") if isinstance(data,dict) else None
        if not isinstance(nodes,dict):
            return
        node = nodes.get("dizel_1",{})
        stamp = frame.get("timestamp")
        try:
            self._require_team("dizel_1")
        except ControlError:
            return
        if quality(node,stamp,self.config) != "OK":
            return
        code,target = "GENERATOR_START_FAILURE","dizel_1"
        fingerprint = hashlib.sha256(f"{code}:{target}".encode()).hexdigest()
        fault = next((item for item in faults if item.get("code")==code and item.get("target")==target),None)
        if fault is not None:
            identity = self._store.open_incident(fingerprint,code,fault["subsystems"],self._redact(fault["evidence"]),self.environment,stamp)
            if self._reported_incidents.get(code) != identity:
                self._audit("observation",summary=fault["message"],anomalies=[code],metrics={"state":"FAULT","fault_code":"START_FAILURE"},
                    incident_id=identity,source="reported_device_fault",outcome="OBSERVED_DEVICE_FAULT")
            self._reported_incidents[code] = identity
        elif node.get("state") in ("OFF","ON","PREHEAT","STARTING","RUNNING","IDLE") and node.get("fault_code") in (None,""):
            identity = self._store.end_observed_incident(fingerprint,self.environment,stamp)
            self._reported_incidents.pop(code,None)
            self._engine_faults = [item for item in self._engine_faults if item.get("code") != code]
            if identity:
                self._audit("observation",summary="Признак отказа запуска больше не наблюдается; причинность восстановления не установлена",
                    anomalies=[],metrics={"state":node.get("state")},incident_id=identity,source="reported_device_fault",
                    outcome="CONDITION_CLEARED_UNATTRIBUTED")

    def _remember_precursor(self, frame, faults):
        """Store reviewed numeric evidence separately from faults/repair cases."""
        if self._store is None:
            return
        data = frame.get("data")
        nodes = data.get("nodes") if isinstance(data,dict) else None
        if not isinstance(nodes,dict):
            return
        node = nodes.get("solar_inverter_01",{})
        stamp = frame.get("timestamp")
        updated = node.get("updated_at") if isinstance(node,dict) else None
        received = frame.get("_received_monotonic")
        if (not isinstance(node,dict) or node.get("online") is not True or node.get("status") != "OK"
                or not number(stamp) or not number(updated) or not -.001 <= stamp-updated <= self.config.max_age_s
                or not number(received) or not 0 <= time.monotonic()-received <= self.config.max_age_s
                or node.get("fan_mode") not in ("ON","AUTO") or node.get("fan_state") != "ON"):
            return
        fault = next((item for item in faults if item.get("code") == "INVERTER_FAN_CURRENT_RISING"
                      and item.get("target") == "solar_inverter_01"), None)
        if fault is None:
            return
        evidence = next((item for item in fault.get("evidence",[]) if isinstance(item,dict)
                         and item.get("id") == "temporal:fan_current_trend"), {})
        context = {"fan_mode":node["fan_mode"],"fan_state":node["fan_state"]}
        context.update({key:node[key] for key in ("fan_rpm","inverter_temp_c","load_power_w") if number(node.get(key))})
        observation = {"code":"INVERTER_FAN_CURRENT_RISING", "target":"solar_inverter_01",
            "signal":"solar_inverter_01.fan_current_a", "values":evidence.get("value"), "context":context}
        try:
            identity = self._store.record_observation(self.environment, observation, stamp)
        except ValueError:
            # Unusable telemetry is not promoted into persistent experience.
            self._audit("error", where="precursor_memory", error="RejectedBoundedObservation")
            return
        if identity:
            self._audit("observation", summary="Сохранён неподтверждённый тренд тока вентилятора; это не поломка или ремонт",
                anomalies=[observation["code"]], metrics={"values":observation["values"],"context":context},
                source="temporal_observation_memory", memory_id=identity, outcome="OBSERVED_TREND_UNCONFIRMED")

    def _retrieve(self, query, frame=None, faults=None):
        try:
            return self.retriever.retrieve(query, self.environment, 8, telemetry=frame or self._frame, faults=faults or [])
        except TypeError:
            return self.retriever.retrieve(query, self.environment, 8)

    def _rerank_sync(self, query, hits, planner):
        needs = getattr(self.retriever, "needs_rerank", None)
        rerank = getattr(self.retriever, "rerank", None)
        if not callable(needs) or not callable(rerank) or not needs(hits):
            return hits
        self.policy.ensure_ready()
        # Optional ranking must never queue a new operator instruction behind
        # an already running automatic model request. The local RAG order is
        # valid evidence while the previous optional ranking finishes.
        if not self._rerank_lock.acquire(blocking=False):
            self._audit("decision", situation="Дополнительный отбор источников занят", cause="operator_not_queued_behind_optional_rerank",
                        confidence=1, evidence=[], plan=[], source="rules")
            return hits
        try:
            return rerank(query, self._redact(hits), _RerankTransport(self, planner))
        finally:
            self._rerank_lock.release()

    async def _analysis_worker(self):
        while True:
            queued_session, frame, temporal = await self._analysis_queue.get()
            if (not self._agent or self._manual_pending or self._connection.get("role") != "team"
                    or self._connection.get("state") != "connected" or queued_session != self._session_id
                    or self._frame_session_id != queued_session):
                continue
            epoch, session = self._manual_epoch, self._session_id
            self._agent.mode = "execute" if self.autonomous else "observe"
            record = {**frame, "context":deepcopy(self.context), "catalog":deepcopy(self.catalog)}
            proxy = _AutoTransport(self, epoch, session, frame.get("timestamp"))
            self._analysis_task = asyncio.create_task(self._agent.process(record, transport=proxy))
            try:
                report = await self._analysis_task
                if epoch != self._manual_epoch or session != self._session_id:
                    continue
                self._engine_faults = report.get("faults", [])
                previous_advisory = self._diagnostics.get("advisory")
                current_signature = _fault_signature(temporal["faults"])
                self._diagnostics = {**report, "temporal_faults":temporal["faults"],
                                     "faults":_merge_faults(report.get("faults", []),temporal["faults"])}
                if previous_advisory and current_signature == self._last_advisory_signature:
                    self._diagnostics["advisory"] = previous_advisory
                self._broadcast()
                if temporal["faults"] and not self._hazard_latched:
                    await self._advisory(temporal["faults"], epoch)
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            except Exception as exc:
                self._audit("error", where="analysis_worker", error=type(exc).__name__)
                self._event("error", "Диагностический проход не завершён", error=str(exc))
            finally:
                self._analysis_task = None

    async def _advisory(self, faults, epoch):
        signature = _fault_signature(faults)
        period = 60 if signature == self._last_advisory_signature else 30
        if time.monotonic() - self._last_advisory < period:
            return
        if not self._deepseek_key or self._manual_pending or epoch != self._manual_epoch:
            return
        self._last_advisory = time.monotonic()
        message = "Каковы вероятные причины и безопасные проверки текущих признаков? Только диагностика, без команд: " + "; ".join(f["message"] for f in faults)[:2500]
        try:
            result, sources = await self._reply(message, faults=faults)
            if epoch == self._manual_epoch:
                self._last_advisory_signature = signature
                self._diagnostics["advisory"] = {"answer":result["answer"], "sources":sources, "at":_now()}
                self._broadcast()
        except Exception as exc:
            self._last_advisory_signature = None
            self._audit("error", where="crisis_advisory", error=type(exc).__name__, details=self._redact(str(exc))[:600])
            self._event("warning", "RAG-ответ DeepSeek пока не принят; наблюдение продолжается", error=self._redact(str(exc))[:600])

    async def _watchdog(self):
        stale_reported = False
        while True:
            await asyncio.sleep(1)
            stamp = self._frame.get("_received_monotonic") if isinstance(self._frame, dict) else None
            stale = stamp is None or time.monotonic() - stamp > self.config.max_age_s
            if stale and self._transport:
                self._transport.allow_control = False
                if not stale_reported:
                    self._event("warning", "Свежая телеметрия отсутствует; исполнение приостановлено")
                    stale_reported = True
            elif not stale:
                stale_reported = False
            if self._connection["state"] != "disconnected":
                self._broadcast()

    def _load_autonomy_numbers(self):
        try:
            values = json.loads((self.data_dir/"autonomy_settings.json").read_text(encoding="utf-8"))
            expected = {"min_airflow_pct","min_supply_flow_m3_h","min_cooling_flow_l_min"}
            if isinstance(values,dict) and set(values) == expected:
                self.config = replace(self.config,**values,approved_live=False)
        except (OSError,ValueError,TypeError):
            pass

    def _autonomy_binding(self):
        return (self._session_id,self.profile,self.team,id(self._transport))

    def _autonomy_confirmation_current(self):
        return (self._autonomy_confirmation is not None and self._autonomy_confirmation == self._autonomy_binding()
                and self._connection.get("state") == "connected" and self._connection.get("role") == "team"
                and str(self._connection.get("team")) == str(self.team))

    def _revoke_autonomy_confirmation(self):
        self.autonomous = False
        self._autonomy_confirmation = None
        self.config = replace(self.config,approved_live=False)
        previous = self._autonomy_previous_flags
        if previous is not None:
            for name,(present,value) in previous.items():
                if name == "emergency_stop" and self.context.get(name) is True:
                    continue
                if present:
                    self.context[name] = value
                else:
                    self.context.pop(name,None)
        self._autonomy_previous_flags = None
        if self._agent:
            self._agent.mode = "observe"
            self._agent.config = replace(self._agent.config,approved_live=False)

    def _safe_autonomy_telemetry_error(self, frame=None):
        if self._hazard_latched or self.context.get("emergency_stop") is True:
            return "Аварийная фиксация или явная аварийная остановка запрещает автоматическое управление"
        try:
            current = self._require_team()
        except ControlError as error:
            return str(error)
        frame = current if frame is None else frame
        if not isinstance(frame,dict) or frame.get("role") != "team":
            return "Нет телеметрии активной команды"
        received = frame.get("_received_monotonic")
        if not number(received) or not 0 <= time.monotonic()-received <= self.config.max_age_s:
            return "Телеметрия для проверки безопасности устарела"
        data = frame.get("data")
        stamp,nodes = frame.get("timestamp"),data.get("nodes") if isinstance(data,dict) else None
        if not isinstance(nodes,dict):
            return "Неизвестен формат телеметрии безопасности"
        for name in ("smoke_detector_01","smoke_detector_02"):
            node = nodes.get(name,{})
            if quality(node,stamp,self.config) != "OK" or node.get("smoke_detected") is not False:
                return f"Нужны свежие достоверные данные без дыма: {name}"
            if node.get("alarm_state") in ("ALARM","UNKNOWN","FAULT"):
                return f"Состояние дымовой сигнализации не подтверждено безопасным: {name}"
        plc = nodes.get("dome_automation_01",{})
        interlocks = plc.get("interlocks") if isinstance(plc,dict) else None
        if (quality(plc,stamp,self.config) != "OK" or not isinstance(interlocks,dict) or not interlocks
                or any(not isinstance(item,dict) or item.get("state") != "ARMED" for item in interlocks.values())):
            return "Нужна свежая автоматика с известными взведёнными защитами ARMED"
        return None

    async def configure_autonomy(self, payload):
        expected = {"min_airflow_pct","min_supply_flow_m3_h","min_cooling_flow_l_min","safety_confirmed"}
        if not isinstance(payload,dict) or set(payload) != expected or payload.get("safety_confirmed") is not True:
            raise ValueError("Заполните три минимальных потока и явно подтвердите безопасное состояние")
        numbers = {name:payload[name] for name in expected-{"safety_confirmed"}}
        if any(not number(value) or value <= 0 for value in numbers.values()) or numbers["min_airflow_pct"] > 100:
            raise ValueError("Минимальные потоки должны быть положительными конечными числами; поток вытяжки не выше 100 %")
        if self.team != 4 or str(self._connection.get("team")) != "4":
            raise ValueError("Настройка доступна только активной команде 4")
        error = self._safe_autonomy_telemetry_error()
        if error:
            raise ValueError(error)
        binding = self._autonomy_binding()
        candidate = replace(self.config,**numbers,approved_live=False)
        self._revoke_autonomy_confirmation()
        self._manual_epoch += 1
        epoch = self._manual_epoch
        await self._preempt_analysis()
        if self._manual_epoch != epoch or self._autonomy_binding() != binding or self._safe_autonomy_telemetry_error():
            raise ValueError("Сессия или безопасное состояние изменились; подтвердите настройки заново")
        self.data_dir.mkdir(parents=True,exist_ok=True)
        target = self.data_dir/"autonomy_settings.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(numbers,ensure_ascii=False,allow_nan=False,indent=2)+"\n",encoding="utf-8")
        temporary.replace(target)
        self._autonomy_previous_flags = {name:(name in self.context,self.context.get(name)) for name in ("trusted","emergency_stop")}
        self.context.update(trusted=True,emergency_stop=False)
        self.config = replace(candidate,approved_live=True)
        self._autonomy_confirmation = binding
        self._make_agent()
        self._audit("decision",situation="Оператор задал минимальные потоки и подтвердил безопасное состояние текущей сессии",
            cause="operator_autonomy_configuration",confidence=1,evidence=[],plan=[],source="manual_configuration",criteria=numbers)
        self._broadcast()
        return self.snapshot()

    def _autonomy_error(self, *, frame=None):
        if self._hazard_latched:
            return "Аварийная фиксация активна; автоматическое управление заблокировано"
        error = self._safe_autonomy_telemetry_error(frame)
        if error:
            return error
        if not self._autonomy_confirmation_current():
            return "Задайте минимальные потоки и подтвердите безопасное состояние в форме автономности для текущей сессии"
        if not self.config.approved_live or self.context.get("trusted") is not True or self.context.get("emergency_stop") is not False:
            return "Нужны утверждённая конфигурация и явный доверенный контекст безопасного состояния"
        if any(getattr(self.config, field) is None for field in ("min_airflow_pct", "min_supply_flow_m3_h", "min_cooling_flow_l_min")):
            return "Не заданы подтверждённые минимальные потоки для автоматических сценариев"
        if not self._deepseek_key:
            return "Не задан ключ DeepSeek"
        error = self.policy.readiness_error()
        if error:
            return error
        if self._connection.get("role") != "team" or self._connection.get("state") != "connected":
            return "Нет активной командной сессии Купола"
        return None

    async def set_autonomy(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Режим автономности должен быть true/false")
        if enabled and self._autonomy_error():
            raise ValueError(self._autonomy_error())
        self.autonomous = enabled
        if not enabled:
            self._manual_epoch += 1
            await self._preempt_analysis()
        if self._agent:
            self._agent.mode = "execute" if enabled else "observe"
        self._audit("decision", situation="Изменение режима", cause="operator_mode_selection", confidence=1,
                    evidence=[], plan=[], source="rules", autonomous=enabled)
        self._event("mode", "Автоматические разрешённые сценарии включены" if enabled else "Режим наблюдения и ручных команд")
        self._broadcast()
        return self.snapshot()

    def _invalidate_plans(self):
        self._diagnostics.pop("advisory", None)
        self._last_advisory_signature = None
        if self._agent:
            self._agent.plan_cache.clear()
            self._agent.planned.clear()
            if hasattr(self._agent, "_plan_cached_at"):
                self._agent._plan_cached_at.clear()

    async def _preempt_analysis(self):
        task = self._analysis_task
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._invalidate_plans()
        if self._agent:
            for pending in list(self._agent.active.values()):
                self._agent._finish(pending, "UNKNOWN", self._agent.last_timestamp or 0, "operator_or_connection_preempted_no_retry")

    async def _manual_begin(self, text, *, message_kind="request"):
        self._manual_epoch += 1
        epoch = self._manual_epoch
        self._manual_pending += 1
        if self._transport:
            self._transport.allow_control = False
        if message_kind == "directive":
            self._directive_pending += 1
            self._directives.append({"text":self._redact(text), "at":_now(), "message_kind":"directive"})
            self._persist_directives()
        await self._preempt_analysis()
        self._broadcast()
        return epoch

    async def clear_directives(self):
        self._manual_epoch += 1
        await self._preempt_analysis()
        self._directives.clear()
        self._overrides.clear()
        self._work_context_override = None
        emergency_stop = self.context.get("emergency_stop") is True
        self.context = deepcopy(self._base_context)
        if emergency_stop:
            self.context["emergency_stop"] = True
        elif self._autonomy_confirmation_current():
            self.context.update(trusted=True,emergency_stop=False)
        self._persist_directives()
        self._event("directives", "Сохранённые указания и ручные приоритеты очищены")
        self._broadcast()
        return self.snapshot()

    async def set_work_context(self, required_fume):
        if type(required_fume) is not bool:
            raise ValueError("Параметр required_fume должен быть true/false")
        text = ("Для текущих работ требуется вытяжка" if required_fume else
                "Для текущих работ не заявлена необходимость вытяжки")
        await self._manual_begin(text,message_kind="request")
        try:
            kept = [item for item in self._directives if item.get("context_kind") != "work"]
            self._directives.clear()
            self._directives.extend(kept)
            self._directives.append({"text":text,"at":_now(),"message_kind":"directive",
                "context_kind":"work","context":{"required_fume":required_fume}})
            self._work_context_override = required_fume
            self.context["required_fume"] = required_fume
            self._persist_directives()
            self._audit("decision",situation=text,cause="operator_work_context",confidence=1,
                evidence=[],plan=[],source="manual_context")
            return self.snapshot()
        finally:
            self._manual_pending = max(0,self._manual_pending-1)
            self._broadcast()

    def _require_team(self, node_id=None):
        if (self._transport is None or self._connection.get("state") != "connected"
                or self._connection.get("role") != "team" or str(self._connection.get("team")) != str(self.team)):
            raise ControlError("Нет активной сессии команды Купола")
        frame = self._frame
        if not isinstance(frame, dict) or frame.get("role") != "team":
            raise ControlError("Телеметрия активной команды ещё не получена")
        if self._frame_session_id != self._session_id:
            raise ControlError("Для новой сессии ещё не получена свежая телеметрия")
        received = frame.get("_received_monotonic")
        if not number(received) or not 0 <= time.monotonic() - received <= self.config.max_age_s:
            raise ControlError("Текущая телеметрия устарела")
        if node_id is not None:
            _fresh(frame, node_id, max_age=self.config.max_age_s)
        return frame

    async def _reply(self, message, *, faults=None, message_kind="request"):
        self.policy.ensure_ready()
        if not self._deepseek_key:
            raise PlannerError("Не задан ключ DeepSeek")
        query = message + " " + " ".join(str(f.get("code", "")) + " " + str(f.get("target", "")) for f in faults or [])
        retrieved = self._retrieve(query, faults=faults)
        retrieved = await asyncio.to_thread(self._rerank_sync, query, retrieved, self._chat_client)
        source_rows = [{k:item[k] for k in ("id", "kind", "source", "section", "outcome", "score", "category",
                                            "source_id", "episode_id", "fault_code", "observation_code", "target", "signal",
                                            "first_at", "last_at", "observation_count", "provenance", "repair_causality") if k in item}
                       | {"excerpt":str(item.get("text", ""))[:700], "instruction_authority":False} for item in retrieved]
        started = time.monotonic()
        try:
            model_frame = deepcopy(self._frame or {})
            receipt = model_frame.get("_received_monotonic")
            model_frame["local_receipt_age_s"] = max(0, time.monotonic() - receipt) if number(receipt) else None
            model_frame["diagnostic_faults"] = [{"code":fault.get("code"), "target":fault.get("target")}
                                                for fault in (faults or [])[:20] if isinstance(fault, dict)]
            # DeepSeekChat applies the mandatory node/field selector before
            # serialization; the complete local snapshot is never exported.
            # One-off commands/questions remain visible in the UI and audit,
            # but are never recycled into later model context or authority.
            result = await asyncio.to_thread(self._chat_client.reply, message, [],
                self._redact(model_frame), deepcopy(self.catalog), self._redact(retrieved),
                operator_guidance=self._redact(list(self._directives)), message_kind=message_kind)
            self.policy.validate_response(result.get("served_model"))
            usage = result.get("usage", {})
            self._audit("llm_call", model=self.policy.model, served_model=result.get("served_model"), purpose="rag_chat_or_crisis",
                message_kind=message_kind,
                prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
                latency_ms=result.get("latency_ms", round((time.monotonic()-started)*1000)), ok=True, error=None,
                telemetry_selection=result.get("telemetry_selection"),
                response_metadata=result.get("response_metadata", {}), summary=result.get("answer", ""))
        except Exception as exc:
            self._audit("llm_call", model=self.policy.model, purpose="rag_chat_or_crisis", ok=False,
                message_kind=message_kind,
                latency_ms=round((time.monotonic()-started)*1000), error=type(exc).__name__,
                details=self._redact(str(exc))[:600],
                validation_stage=getattr(exc, "validation_stage", None),
                validation_fields=getattr(exc, "validation_fields", None),
                attempts=getattr(exc, "attempt_metadata", []), usage=getattr(exc, "usage", {}), summary=None)
            raise
        used = set(result.get("sources", []))
        result["retrieved_sources"] = source_rows
        return result, [source for source in source_rows if source["id"] in used]

    async def chat(self, message, *, message_kind="request", _expected_job=None):
        if not isinstance(message, str) or not message.strip() or len(message) > 6000:
            raise ValueError("Введите сообщение длиной до 6000 символов")
        if not isinstance(message_kind, str) or message_kind not in {"request", "directive"}:
            raise ValueError("Тип сообщения должен быть request или directive")
        if message_kind == "directive" and _expected_job is not None:
            raise ValueError("Постоянное указание не запускает задание")
        epoch = await self._manual_begin(message, message_kind=message_kind)
        user = self._redact({"role":"user", "content":message, "message_kind":message_kind, "at":_now()})
        self._messages.append(user)
        self._publish("chat", user)
        self._audit("message", direction="in", sender="local_operator", text=message, message_kind=message_kind)
        try:
            faults = self._diagnostics.get("faults", [])
            result, sources = await self._reply(message, faults=faults, message_kind=message_kind)
            commands = result.get("commands", [])
            if message_kind == "directive" and commands:
                raise ControlError("Постоянное указание сохранено; команды из этого сообщения не выполняются")
            if _expected_job is not None and (not commands or any(command != _expected_job for command in commands)):
                raise ControlError("DeepSeek не выбрал ровно запуск выбранного файла на выбранном устройстве")
            executed = []
            for request in commands:
                if epoch != self._manual_epoch:
                    executed.append({"status":"SUPERSEDED", "request":request})
                    break
                executed.append(await self._manual_request(request, epoch, sources))
            answer = self._redact({"role":"assistant", "content":result["answer"], "sources":sources,
                "retrieved_sources":result.get("retrieved_sources", []),
                "commands":executed, "message_kind":message_kind, "usage":result.get("usage", {}),
                "model":result.get("served_model", self.policy.model), "at":_now()})
            self._messages.append(answer)
            self._publish("chat", answer)
            self._audit("message", direction="out", to="local_operator", text=answer["content"], message_kind=message_kind)
            return answer
        except Exception as exc:
            self._event("error", "Запрос чата не завершён", error=str(exc))
            raise
        finally:
            self._manual_pending = max(0, self._manual_pending - 1)
            if message_kind == "directive":
                self._directive_pending = max(0, self._directive_pending - 1)
            self._broadcast()

    async def start_job(self, node_id, file_name):
        if node_id not in MACHINES:
            raise ControlError("Неизвестный станок")
        file_name = safe_filename(file_name)
        listing = await self.files(node_id)
        selected = next((item for item in listing["files"] if item["name"] == file_name), None)
        if selected is None or selected.get("valid") is False:
            raise ControlError("Выбранный файл отсутствует в подтверждённом списке или помечен сервером как невалидный")
        request = {"type":"control", "node_id":node_id, "action":"start_job", "value":file_name}
        return await self.chat(f"Запусти задачу на {node_id}: файл {json.dumps(file_name, ensure_ascii=False)}. Нужна только эта команда start_job, без других действий.", _expected_job=request)

    async def manual_control(self, request):
        """Execute one explicitly selected reviewed API command without an LLM."""
        canonical = canonical_request(request,self.catalog,allow_upload=False)
        epoch = await self._manual_begin("Прямое ручное управление",message_kind="request")
        try:
            return await self._manual_request(canonical,epoch,source="manual_api")
        finally:
            self._manual_pending = max(0,self._manual_pending-1)
            self._broadcast()

    async def _manual_request(self, request, epoch, sources=None, *, source="rag"):
        if request.get("action") == "start_job":
            listing = await self.files(request.get("node_id"))
            selected = next((item for item in listing["files"] if item["name"] == request.get("value")), None)
            if selected is None or selected.get("valid") is False:
                raise ControlError("Выбранный файл не подтверждён сервером или имеет valid=false")
        async with self._command_lock:
            if epoch != self._manual_epoch:
                raise ControlError("Более новое указание пользователя заменило этот план")
            if self._hazard_latched:
                raise ControlError("Аварийная фиксация запрещает программное переключение оборудования")
            before = deepcopy(self._require_team(request.get("node_id")))
            canonical = validate_manual(request, self.catalog, before)
            ref = self._audit("decision", situation="Явное указание оператора",
                cause="manual_api_request" if source == "manual_api" else "manual_rag_plan", confidence=1,
                evidence=[], plan=[canonical], source=source, rag_refs=[s["id"] for s in sources or []])
            self._audit("send_intent", ref=ref, request=canonical, manual_epoch=epoch)
            transport = self._transport
            transport.allow_control = True
            try:
                response = await transport.send(canonical)
            except BaseException as exc:
                action_ref = self._audit("action", ref=ref, request=canonical,
                    response={"success":None, "error":type(exc).__name__, "outcome":"UNKNOWN"})
                self._audit("verification", ref=action_ref, expected="known API response and new state", observed=type(exc).__name__, ok=False, outcome="UNKNOWN")
                if not isinstance(exc, Exception):
                    raise
                return self._redact({"request":canonical, "response":{"success":None,"error":type(exc).__name__},
                    "status":"UNKNOWN", "verified":False, "stable_repair_verified":False,
                    "message":"Результат передачи неизвестен; команда автоматически не повторяется"})
            finally:
                transport.allow_control = False
            action_ref = self._audit("action", ref=ref, request=canonical, response=response)
        if not isinstance(response, dict) or response.get("success") is not True:
            result = {"request":canonical, "response":response, "status":"REJECTED", "verified":False}
            self._audit("verification", ref=action_ref, expected="requested state", observed=response, ok=False)
            return self._redact(result)
        acknowledged = time.monotonic()
        deadline = acknowledged + self.verification_timeout
        observed, verified, limitation = None, False, None
        try:
            while time.monotonic() < deadline and epoch == self._manual_epoch:
                frame = self._frame
                if isinstance(frame, dict) and number(frame.get("_received_monotonic")) and frame["_received_monotonic"] > acknowledged:
                    effect = verify_effect(canonical, before, frame)
                    observed = self._redact(frame.get("data", {}).get("nodes", {}).get(canonical["node_id"], {}))
                    if effect is True:
                        verified = True
                        break
                    if effect is None:
                        limitation = "NO_TELEMETRY_PREDICATE"
                        break
                async with self._changed:
                    await asyncio.wait_for(self._changed.wait(), max(.01, deadline-time.monotonic()))
        except asyncio.TimeoutError:
            pass
        status = "STATE_CONFIRMED" if verified else "SUPERSEDED_AFTER_SEND" if epoch != self._manual_epoch else "ACKNOWLEDGED_ONLY"
        self._audit("verification", ref=action_ref, expected="new telemetry matches requested state", observed=observed,
                    ok=verified, outcome=status, limitation=limitation, stable_repair_verified=False)
        return self._redact({"request":canonical, "response":response, "status":status,
                            "verified":verified, "stable_repair_verified":False, "observed":observed})

    async def _query(self, request):
        async with self._command_lock:
            self._require_team(request.get("node_id"))
            ref = self._audit("decision", situation="Запрос данных устройства", cause="operator_read", confidence=1,
                evidence=[], plan=[request], source="rules")
            response = await self._transport.send(request)
            self._audit("action", ref=ref, request=request, response=response)
            if not isinstance(response, dict) or response.get("success") is not True:
                raise ControlError(str(response.get("error", "API отклонил запрос") if isinstance(response, dict) else "Некорректный ответ API"))
            return response

    async def files(self, node_id):
        if node_id not in MACHINES:
            raise ControlError("Выберите 3D-принтер или фрезер")
        request = {"type":"query", "node_id":node_id, "query":"list_files"}
        if node_id == "cnc_01":
            request["params"] = {"validate":True}
        response = await self._query(request)
        files = normalize_files_response(response, node_id)
        return self._redact({"node_id":node_id, "files":files, "source":"api"})

    async def upload(self, node_id, filename, content, *, organizer_prepared=False):
        if organizer_prepared is not True:
            raise ControlError("Подтвердите, что загружаете подготовленный организаторами G-code")
        request = prepare_upload(node_id, filename, content)
        epoch = await self._manual_begin(f"Передать файл {filename} на {node_id}; не запускать задачу автоматически")
        try:
            canonical_request(request, self.catalog, allow_upload=True)
            self._require_team(node_id)
            self.policy.ensure_ready()
            if not self._deepseek_key:
                raise PlannerError("Не задан ключ DeepSeek")
            review_method = getattr(self._chat_client, "review_upload", None)
            if not callable(review_method):
                raise ControlError("Проверка передачи файла через RAG DeepSeek недоступна")
            metadata = {"node_id":node_id, "file_name":filename, "bytes":len(content),
                        "sha256":hashlib.sha256(content).hexdigest(), "organizer_prepared":True}
            query = f"Передача подготовленного организаторами G-code upload_file {node_id} {filename}; каталог, безопасность, без запуска"
            retrieved = self._retrieve(query)
            retrieved = await asyncio.to_thread(self._rerank_sync, query, retrieved, self._chat_client)
            started = time.monotonic()
            try:
                review = await asyncio.to_thread(review_method, metadata, self._redact(self._frame),
                    deepcopy(self.catalog), self._redact(retrieved), operator_guidance=self._redact(list(self._directives)))
                if review.get("approved") is True:
                    self.policy.validate_response(review.get("served_model"))
                usage = review.get("usage", {})
                self._audit("llm_call", model=self.policy.model, served_model=review.get("served_model"),
                    purpose="upload_metadata_review", latency_ms=review.get("latency_ms",round((time.monotonic()-started)*1000)),
                    prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
                    ok=True, error=None, summary=review.get("reason",review.get("answer")))
            except Exception as exc:
                self._audit("llm_call", model=self.policy.model, purpose="upload_metadata_review", ok=False,
                    latency_ms=round((time.monotonic()-started)*1000), error=type(exc).__name__, summary=None)
                raise
            if review.get("approved") is not True:
                raise ControlError(str(review.get("reason") or review.get("answer") or "DeepSeek не согласовал передачу файла"))
            if epoch != self._manual_epoch:
                raise ControlError("Более новое указание пользователя заменило передачу файла")
            ids = set(review.get("sources", []))
            sources = [{k:item[k] for k in ("id","source","section") if k in item} for item in retrieved if item.get("id") in ids]
            # Only this literal UI operation sends the original bytes to the Dome.
            # DeepSeek receives approved metadata, never G-code content.
            result = await self._manual_request(request, epoch, sources)
            listing = {"files":[]}
            if result.get("response", {}).get("success") is True:
                try:
                    listing = await self.files(node_id)
                except Exception as exc:
                    # The upload may already be complete. Preserve its ACK and
                    # explain that only the subsequent read failed; never make
                    # an HTTP error look like permission to resend the file.
                    result.update(status="ACKNOWLEDGED_ONLY", verified=False, list_error=self._redact(str(exc)))
            listed = any(item["name"] == filename for item in listing["files"])
            result.update(file_name=filename, listed=listed, file_sha256=hashlib.sha256(content).hexdigest(), file_bytes=len(content),
                          automatic_job_started=False, review={"approved":True,"reason":review.get("reason",review.get("answer")),"sources":sources})
            self._event("upload", "Файл обнаружен в списке устройства" if listed else "Передача файла не подтверждена списком устройства", node_id=node_id, file_name=filename)
            return self._redact(result)
        finally:
            self._manual_pending = max(0, self._manual_pending - 1)
            self._broadcast()
