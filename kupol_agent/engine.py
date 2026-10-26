"""Causal diagnosis, reviewed runbooks and verified case learning.

Training thresholds are engineering examples, never physical safety limits.
Only five explicit command pairs can be emitted. Observe/replay never execute.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, fields
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

from . import __version__

RUNBOOKS = frozenset({"check_telemetry", "restore_fume_power", "start_fume", "inspect_fume_mechanical",
    "start_supply", "increase_fresh_air", "check_co2_sources", "restore_energy", "cool_inverter",
    "resume_production", "start_cooling", "inspect_cooling", "switch_water_filter", "emergency_procedure",
    "inspect_generator_start_failure"})
COMMANDS = frozenset({("fume_extraction_01", "turn_on"), ("supply_ventilation_01", "turn_on"),
    ("equipment_cooling_01", "turn_on"), ("supply_ventilation_01", "set_mode"),
    ("supply_ventilation_01", "set_speed")})
_SECRET = re.compile(r"^(?:key|.*_key|token|.*_token|authorization|password|secret)$", re.I)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def safe(value):
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if _SECRET.fullmatch(str(k)) else safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    if isinstance(value, str):
        for name in ("DOME_KEY", "DEEPSEEK_API_KEY"):
            key = os.getenv(name)
            if key:
                value = value.replace(key, "[REDACTED]")
        return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", value)
    return value


@dataclass
class Config:
    approved_live: bool = False
    max_age_s: float = 15.0
    max_measurement_delay_s: float = 5.0
    min_airflow_pct: float | None = None
    min_cooling_flow_l_min: float | None = None
    min_supply_flow_m3_h: float | None = None
    source_origins: dict | None = None
    verification_window_s: float = 8.0
    verification_observations: int = 2
    verification_timeout_s: float = 40.0
    co2_threshold_ppm: float = 2000.0
    log_dir: str | None = None
    runbook_version: str = "1.0"

    def __post_init__(self):
        if type(self.approved_live) is not bool:
            raise ValueError("approved_live must be boolean")
        for name in ("max_age_s", "max_measurement_delay_s", "verification_window_s", "verification_timeout_s", "co2_threshold_ppm"):
            value = getattr(self, name)
            if not number(value) or value <= 0:
                raise ValueError(f"Invalid {name}")
        if self.verification_window_s < 8 or self.verification_observations < 2 or type(self.verification_observations) is not int:
            raise ValueError("Verification requires >=2 observations and >=8 seconds")
        for name in ("min_airflow_pct", "min_cooling_flow_l_min", "min_supply_flow_m3_h"):
            value = getattr(self, name)
            if value is not None and (not number(value) or value <= 0):
                raise ValueError(f"Invalid {name}")
        if self.min_airflow_pct is not None and self.min_airflow_pct > 100:
            raise ValueError("Invalid min_airflow_pct")
        if not isinstance(self.runbook_version, str) or not self.runbook_version:
            raise ValueError("Missing runbook_version")
        if self.source_origins is not None and (not isinstance(self.source_origins, dict)
                or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in self.source_origins.items())):
            raise ValueError("source_origins must map node IDs to physical origins")

    @classmethod
    def from_json(cls, path_or_none):
        if path_or_none is None:
            return cls()
        values = json.loads(Path(path_or_none).read_text(encoding="utf-8-sig"))
        if not isinstance(values, dict) or set(values) - {f.name for f in fields(cls)}:
            raise ValueError("Unknown configuration fields")
        return cls(**values)


def _node(nodes, name):
    value = nodes.get(name)
    return value if isinstance(value, dict) else {}


def quality(node, now, config):
    if not isinstance(node, dict) or node.get("online") is not True:
        return "OFFLINE_OR_MISSING"
    if node.get("status") != "OK":
        return "STATUS_UNKNOWN"
    updated = node.get("updated_at")
    # Node timestamps are rounded to milliseconds; frame timestamps retain microseconds.
    if not number(updated) or not number(now) or not -0.001 <= now - updated <= config.max_age_s:
        return "STALE_OR_INVALID_TIME"
    return "OK"


def line(nodes, index):
    lines = _node(nodes, "smart_panel_01").get("lines")
    return next((v for v in lines if isinstance(v, dict) and v.get("line") == index), {}) if isinstance(lines, list) else {}


def flatten(nodes):
    values = {}
    def visit(value, name):
        values[name] = value
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, name + "." + key)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, f"{name}[{i}]")
    for node_id, node in nodes.items():
        if isinstance(node, dict):
            for param, value in node.items():
                visit(value, f"{node_id}.{param}")
    return values


def _evidence(nodes, *paths):
    values = flatten(nodes)
    return [{"id": "obs:" + p, "node": p.split(".", 1)[0], "param": p.split(".", 1)[-1],
             "value": safe(values.get(p)), "present": p in values} for p in paths]


def _fault(code, target, message, systems, runbooks, evidence, severity="warning"):
    return {"code": code, "fault_code": code, "target": target, "message": message,
            "subsystems": systems, "evidence": evidence, "severity": severity,
            "candidates": [{"runbook_id": r, "version": "1.0", "eligible": True,
                            "description": r, "priority": 1.0 if i == 0 else 0.3} for i, r in enumerate(runbooks)]}


def generator_start_failure(nodes, now, config):
    """Reported start failure warrants diagnosis, never an automatic restart."""
    generator = _node(nodes,"dizel_1")
    if (quality(generator,now,config) != "OK" or generator.get("state") != "FAULT"
            or generator.get("fault_code") != "START_FAILURE"):
        return None
    panel = _node(nodes,"smart_panel_01")
    power_lost = quality(panel,now,config) == "OK" and panel.get("bus_powered") is False
    reason = generator.get("fault_reason")
    reason = reason if isinstance(reason,str) and re.fullmatch(r"[A-Z0-9_]{1,64}",reason) else "UNKNOWN"
    message = ("Дизель сообщает START_FAILURE: запуск не завершён. "
               "CRANK обозначает этап прокрутки, а не доказанную неисправность стартера. "
               "Проверить питание/АКБ, топливо, попытки запуска и блокировки; не перезапускать автоматически.")
    proof = _evidence(nodes,"dizel_1.state","dizel_1.fault_code","dizel_1.start_attempts",
        "dizel_1.lockout_remaining_s","battery_01.soc_pct","battery_01.voltage_v",
        "fuel_tank_01.level_pct","fuel_tank_01.water_content_ppm","solar_inverter_01.power_deficit_w",
        "smart_panel_01.bus_powered")
    proof.append({"id":"obs:dizel_1.fault_reason","node":"dizel_1","param":"fault_reason","value":reason,"present":True})
    fault = _fault("GENERATOR_START_FAILURE","dizel_1",message,["energy","resources","power_distribution"],
        ["inspect_generator_start_failure"],proof,"critical" if power_lost else "warning")
    checks = ["Сверить питание и состояние АКБ", "Проверить уровень/качество топлива и разрешённую подачу",
        "Сверить start_attempts, lockout_remaining_s и журнал автоматики", "Привлечь уполномоченного оператора; не сбрасывать защиты и не повторять запуск вслепую"]
    fault["checks"] = checks
    fault["candidates"][0].update(description="Диагностика отказа запуска дизеля и эскалация без команд",checks=checks)
    return fault


def diagnose(nodes, now, context, config):
    faults = []
    add = faults.append
    plc = _node(nodes, "dome_automation_01")
    interlocks = plc.get("interlocks") if isinstance(plc.get("interlocks"), dict) else {}
    critical = [name for name in ("smoke_detector_01", "smoke_detector_02")
                if _node(nodes, name).get("smoke_detected") is True or _node(nodes, name).get("alarm_state") == "ALARM"]
    if context.get("emergency_stop") is True or critical or any(
        isinstance(v, dict) and v.get("state") == "TRIPPED" and k == "IL_SMOKE" for k, v in interlocks.items()):
        add(_fault("EMERGENCY", "dome_automation_01", "Аварийная процедура имеет приоритет; защиты не квитировать",
                   ["safety", "air", "production"], ["emergency_procedure"],
                   _evidence(nodes, "smoke_detector_01.smoke_detected", "dome_automation_01.interlocks"), "critical"))
    monitored = [(n, d) for n, d in nodes.items() if n != "meeting_01"]
    bad = [(n, quality(d, now, config)) for n, d in monitored if quality(d, now, config) != "OK"]
    if not nodes or (monitored and len(bad) >= max(2, len(monitored) / 2)):
        add(_fault("MASS_TELEMETRY_LOSS", "network_link_01", "Проверить общую связь и питание, не объявлять отказы всех узлов",
                   ["communications", "telemetry_quality", "power_distribution"], ["check_telemetry"],
                   [{"id": "obs:quality", "value": bad}], "critical"))
    elif bad:
        add(_fault("DATA_QUALITY", bad[0][0], "Часть показаний непригодна для управления", ["telemetry_quality"],
                   ["check_telemetry"], [{"id": "obs:quality", "value": bad}]))
    fume = _node(nodes, "fume_extraction_01")
    required = context.get("required_fume") is True or context.get("operator_activity") in {"SOLDERING", "CHEMICALS"}
    power = line(nodes, 1)
    panel_fresh = quality(_node(nodes, "smart_panel_01"), now, config) == "OK"
    if required and panel_fresh and power.get("state") != "ON":
        add(_fault("FUME_POWER_LOSS", "fume_extraction_01", "Сначала питание общей линии 1, а не двигатель вытяжки",
                   ["power_distribution", "air", "production", "safety"], ["restore_fume_power"],
                   _evidence(nodes, "smart_panel_01.lines", "fume_extraction_01.online"), "critical"))
    elif required and quality(fume, now, config) == "OK":
        if fume.get("enabled") is False:
            add(_fault("FUME_STOPPED", "fume_extraction_01", "Нужная для операции вытяжка выключена",
                       ["air", "production", "power_distribution"], ["start_fume", "inspect_fume_mechanical"],
                       _evidence(nodes, "fume_extraction_01.enabled", "fume_extraction_01.alarm", "smart_panel_01.lines")))
        elif fume.get("enabled") is True and (fume.get("alarm") is True or fume.get("motor_state") != "OK"
                or not number(fume.get("fan_rpm")) or fume.get("fan_rpm", 0) <= 0
                or not number(fume.get("airflow_pct")) or fume.get("airflow_pct", 0) <= 0):
            add(_fault("FUME_MECHANICAL", "fume_extraction_01", "Питание и запуск не доказывают поток; нужен осмотр",
                       ["air", "thermal", "production"], ["inspect_fume_mechanical"],
                       _evidence(nodes, "fume_extraction_01.enabled", "fume_extraction_01.fan_rpm", "fume_extraction_01.airflow_pct"), "critical"))
    sensors = [(name, _node(nodes, name)) for name in ("climate_sensor_01", "climate_sensor_02", "climate_sensor_03")]
    high = [(n, d) for n, d in sensors if quality(d, now, config) == "OK" and number(d.get("co2_ppm")) and d["co2_ppm"] > config.co2_threshold_ppm]
    if high:
        synchronous = [(n, d) for n, d in sensors if quality(d, now, config) == "OK" and number(d.get("co2_ppm"))
                       and number(d.get("reading_delay_s", 0)) and 0 <= d.get("reading_delay_s", 0) <= config.max_measurement_delay_s]
        origins = config.source_origins or context.get("source_origins", {})
        origins = origins if isinstance(origins, dict) else {}
        physical = {origins[n] for n, _ in synchronous if isinstance(origins.get(n), str) and origins[n]}
        corroborated = len(physical) >= 2 and all(d["co2_ppm"] > config.co2_threshold_ppm for _, d in synchronous)
        code = "CO2_CONFIRMED" if corroborated else "CO2_UNCONFIRMED"
        add(_fault(code, "supply_ventilation_01", "CO2 требует проверки времени источников; задержанное большинство не доказательство",
                   ["air", "telemetry_quality", "safety"], ["increase_fresh_air", "check_co2_sources"] if corroborated else ["check_co2_sources"],
                   _evidence(nodes, *[n + ".co2_ppm" for n, _ in sensors], "climate_sensor_02.reading_delay_s", "climate_sensor_03.reading_delay_s")))
        supply = _node(nodes, "supply_ventilation_01")
        if corroborated and quality(supply, now, config) == "OK" and supply.get("state") == "OFF":
            add(_fault("SUPPLY_STOPPED", "supply_ventilation_01", "Перед усилением притока требуется включение",
                       ["air", "power_distribution"], ["start_supply"], _evidence(nodes, "supply_ventilation_01.state")))
    inv, battery = _node(nodes, "solar_inverter_01"), _node(nodes, "battery_01")
    generator_fault = generator_start_failure(nodes,now,config)
    if generator_fault is not None:
        add(generator_fault)
    if (quality(inv, now, config) == "OK" and number(inv.get("power_deficit_w")) and inv["power_deficit_w"] > 0) or (
        quality(battery, now, config) == "OK" and number(battery.get("soc_pct")) and battery["soc_pct"] < 25):
        add(_fault("ENERGY_DEFICIT", "solar_inverter_01", "Дефицит/SOC, а не сам факт разряда; точки измерения различаются",
                   ["energy", "resources", "power_distribution"], ["restore_energy"],
                   _evidence(nodes, "solar_inverter_01.power_deficit_w", "solar_inverter_01.output_voltage_v", "battery_01.soc_pct")))
    if quality(inv, now, config) == "OK" and ((number(inv.get("inverter_temp_c")) and inv["inverter_temp_c"] > 75) or inv.get("error_code") == "E07"):
        add(_fault("INVERTER_THERMAL_OR_LOAD", "solar_inverter_01", "Перегрев и перегрузка требуют раздельной проверки",
                   ["energy", "thermal", "safety"], ["cool_inverter"],
                   _evidence(nodes, "solar_inverter_01.inverter_temp_c", "solar_inverter_01.error_code", "solar_inverter_01.fan_state")))
    active_production = any(_node(nodes, n).get("state") in {"PRINTING", "RUNNING"} for n in ("printer_3d_01", "cnc_01"))
    cooling = _node(nodes, "equipment_cooling_01")
    if active_production and quality(cooling, now, config) == "OK":
        if cooling.get("state") == "OFF":
            add(_fault("COOLING_STOPPED", "equipment_cooling_01", "Охлаждение выключено при работающем производстве",
                       ["thermal", "production"], ["start_cooling", "inspect_cooling"], _evidence(nodes, "equipment_cooling_01.state")))
        elif cooling.get("alarm") is True or not number(cooling.get("coolant_flow_l_min")) or cooling.get("coolant_flow_l_min", 0) <= 0:
            add(_fault("COOLING_FAILURE", "equipment_cooling_01", "Включенный насос не подтверждает поток",
                       ["thermal", "production"], ["inspect_cooling"], _evidence(nodes, "equipment_cooling_01.coolant_flow_l_min")))
    for name in ("printer_3d_01", "cnc_01"):
        machine = _node(nodes, name)
        if quality(machine, now, config) == "OK" and (machine.get("hold_reason") or machine.get("hold_level") or machine.get("state") in {"PAUSED", "ALARM"}):
            add(_fault("MACHINE_HELD", name, "Сначала причина удержания; resume прототип не отправляет",
                       ["production", "safety", "power_distribution", "air"], ["resume_production"],
                       _evidence(nodes, name + ".hold_reason", name + ".hold_level", name + ".resume_attempts")))
    water = _node(nodes, "water_filter_01")
    if quality(water, now, config) == "OK" and (water.get("warning_active") is True or water.get("filter_state") in {"CLOGGED", "ERROR"}):
        add(_fault("WATER_FILTER", "water_filter_01", "Проверить фильтр и источник, не переключать вслепую",
                   ["water", "resources"], ["switch_water_filter"], _evidence(nodes, "water_filter_01.active_filter", "water_filter_01.warning_active")))
    return sorted(faults, key=lambda f: (0 if f["code"] == "EMERGENCY" else 1 if f["severity"] == "critical" else 2, f["code"]))


class Agent:
    def __init__(self, store, retriever, planner=None, config=None, environment="simulation", mode="observe"):
        if mode not in {"observe", "replay", "dry_run", "simulate", "execute"}:
            raise ValueError("Unknown agent mode")
        if mode == "simulate" and environment.casefold().startswith(("live", "station:")):
            raise ValueError("Simulation must not learn in live environment")
        self.store, self.retriever, self.planner = store, retriever, planner
        self.config, self.environment, self.mode = config or Config(), environment, mode
        self.last_timestamp = None
        self.active, self.planned, self.plan_cache = {}, {}, {}
        self._plan_cached_at = {}
        self._last_observation_at = 0.0
        self._last_observation_signature = None
        self.lock = asyncio.Lock()
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid.uuid4().hex[:6]
        self.seq, self._audit_stream = 0, None
        model = json.loads((Path(__file__).parent / "data/subsystems.json").read_text(encoding="utf-8"))
        self.subsystems = model["subsystems"]
        if self.config.log_dir:
            folder = Path(self.config.log_dir); folder.mkdir(parents=True, exist_ok=True)
            self._audit_stream = (folder / f"agent_{self.run_id}.jsonl").open("x", encoding="utf-8", buffering=1)
        self._emit("start", agent="kupol-rag-agent", version=__version__, models=[getattr(planner, "model", "configured-planner")] if planner else [], config={"mode": mode, "environment": environment})

    def _emit(self, kind, **values):
        self.seq += 1
        record = safe({"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "run_id": self.run_id, "seq": self.seq, "kind": kind, **values})
        if self._audit_stream:
            self._audit_stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"); self._audit_stream.flush()
        return self.seq

    def membership(self, nodes):
        values = flatten(nodes)
        return {name: [s["id"] for s in self.subsystems if any(fnmatch.fnmatchcase(name, p) for p in s["signals"])] for name in values}

    def audit(self, kind, **values):
        return self._emit(kind, **values)

    def _actions(self, runbook, fault, nodes):
        target = fault["target"]
        if runbook in {"start_fume", "start_supply", "start_cooling"}:
            return [{"type": "control", "node_id": target, "action": "turn_on"}]
        if runbook == "increase_fresh_air":
            supply = _node(nodes, "supply_ventilation_01")
            actions = []
            if supply.get("mode") != "FRESH_AIR":
                actions.append({"type": "control", "node_id": target, "action": "set_mode", "value": "FRESH_AIR"})
            if supply.get("speed_pct") != 100:
                actions.append({"type": "control", "node_id": target, "action": "set_speed", "value": 100})
            return actions
        return []

    def gate(self, actions, fault, nodes, now, context, transport):
        if self.mode not in {"simulate", "execute"}:
            return False, "OBSERVATION_ONLY"
        if transport is None:
            return False, "NO_TRANSPORT"
        if self.mode == "simulate" and not isinstance(transport, MockTransport):
            return False, "SIMULATION_REQUIRES_MOCK"
        if self.mode == "execute" and (not self.config.approved_live or context.get("trusted") is not True):
            return False, "LIVE_CONFIGURATION_NOT_APPROVED"
        if self.mode == "execute" and getattr(transport, "allow_control", False) is not True:
            return False, "TRANSPORT_CONTROL_REVOKED"
        if self.mode == "execute" and context.get("_frame_role") != "team":
            return False, "FRAME_NOT_ACTIVE_TEAM"
        if self.mode == "execute":
            if "_received_monotonic" in context:
                received = context["_received_monotonic"]
                if not number(received) or not 0 <= time.monotonic() - received <= self.config.max_age_s:
                    return False, "FRAME_STALE_AFTER_RECEIVE"
            latest = getattr(transport, "latest_frame", None)
            if not isinstance(latest, dict):
                latest = getattr(transport, "snapshot", None)
            if isinstance(latest, dict) and number(latest.get("timestamp")) and latest["timestamp"] > now:
                return False, "NEWER_FRAME_REQUIRES_REVALIDATION"
        if context.get("_critical_incident") is True:
            return False, "CRITICAL_INCIDENT_HAS_PRIORITY"
        if context.get("emergency_stop") is not False:
            return False, "EMERGENCY_STATE_NOT_CONFIRMED"
        if getattr(transport, "role", "guest") != "team":
            return False, "NOT_ACTIVE_TEAM"
        plc = _node(nodes, "dome_automation_01")
        if quality(plc, now, self.config) != "OK" or not isinstance(plc.get("interlocks"), dict) or not plc["interlocks"]:
            return False, "AUTOMATION_UNKNOWN"
        if any(not isinstance(v, dict) or v.get("state") != "ARMED" for v in plc["interlocks"].values()):
            return False, "CONFLICTING_OR_UNKNOWN_INTERLOCK"
        if any(_node(nodes, n).get("smoke_detected") is True for n in ("smoke_detector_01", "smoke_detector_02")):
            return False, "SMOKE"
        catalog = getattr(transport, "catalog", {})
        if isinstance(catalog, list):
            catalog = {v.get("node_id"): v for v in catalog if isinstance(v, dict)}
        for action in actions:
            target, command = action.get("node_id"), action.get("action")
            can_control = getattr(transport, "can_control", None)
            if callable(can_control):
                permitted, reason = can_control(action)
                if permitted is not True:
                    return False, str(reason)
            if (target, command) not in COMMANDS or action.get("type") != "control":
                return False, "ACTION_NOT_ALLOWLISTED"
            if command not in (catalog.get(target, {}).get("controls", []) if isinstance(catalog, dict) else []):
                return False, "NOT_IN_CURRENT_CATALOG"
            node = _node(nodes, target)
            if quality(node, now, self.config) != "OK" or node.get("alarm") is not False:
                return False, "TARGET_NOT_FRESH_OR_ALARMED"
            minimum = (self.config.min_cooling_flow_l_min if target == "equipment_cooling_01" else
                       self.config.min_supply_flow_m3_h if target == "supply_ventilation_01" else self.config.min_airflow_pct)
            if self.mode == "execute" and minimum is None:
                return False, "LIVE_MINIMUM_FLOW_NOT_CONFIGURED"
            if target == "fume_extraction_01":
                power = line(nodes, 1)
                if quality(_node(nodes, "smart_panel_01"), now, self.config) != "OK" or power.get("state") != "ON" or power.get("protection_state") != "ARMED" or power.get("lockout_remaining_s") != 0:
                    return False, "FUME_POWER_NOT_CONFIRMED"
                if node.get("motor_state") != "OK" or not (context.get("required_fume") is True or context.get("operator_activity") in {"SOLDERING", "CHEMICALS"}):
                    return False, "FUME_CONTEXT_OR_MOTOR_UNKNOWN"
            if target == "supply_ventilation_01":
                if command != "turn_on" and node.get("state") != "ON":
                    return False, "SUPPLY_NOT_RUNNING"
                weather = _node(nodes, "weather_station_01")
                sealing = _node(nodes, "dome_sealing_01")
                if quality(weather, now, self.config) != "OK" or not number(weather.get("wind_speed_ms")) or weather["wind_speed_ms"] > 20:
                    return False, "STORM_OR_WEATHER_UNKNOWN"
                if quality(sealing, now, self.config) != "OK" or sealing.get("state") != "OPEN":
                    return False, "SEALING_CONFLICT_OR_UNKNOWN"
                if command == "set_mode" and action.get("value") != "FRESH_AIR":
                    return False, "MODE_NOT_ALLOWED"
                if command == "set_speed" and (type(action.get("value")) is not int or action["value"] != 100):
                    return False, "SPEED_NOT_ALLOWED"
            if target == "equipment_cooling_01" and not any(_node(nodes, n).get("state") in {"PRINTING", "RUNNING"} for n in ("printer_3d_01", "cnc_01")):
                return False, "COOLING_NOT_REQUIRED"
        return True, "ALLOWED"

    def _effect(self, request, nodes, now):
        node = _node(nodes, request["node_id"])
        if quality(node, now, self.config) != "OK" or node.get("alarm") is not False:
            return False
        target, command = request["node_id"], request["action"]
        supply_minimum = self.config.min_supply_flow_m3_h or (1.0 if self.mode == "simulate" else None)
        supply_flow = (supply_minimum is not None and number(node.get("airflow_m3_h"))
                       and node["airflow_m3_h"] >= supply_minimum
                       and number(node.get("fresh_air_m3_h")) and node["fresh_air_m3_h"] >= supply_minimum)
        if command == "set_mode":
            return node.get("state") == "ON" and node.get("mode") == request["value"] and supply_flow
        if command == "set_speed":
            return node.get("state") == "ON" and node.get("speed_pct") == request["value"] and supply_flow
        if target == "fume_extraction_01":
            minimum = self.config.min_airflow_pct or (1.0 if self.mode == "simulate" else None)
            return minimum is not None and node.get("enabled") is True and number(node.get("fan_rpm")) and node["fan_rpm"] > 0 and number(node.get("airflow_pct")) and node["airflow_pct"] >= minimum
        if target == "equipment_cooling_01":
            minimum = self.config.min_cooling_flow_l_min or (1.0 if self.mode == "simulate" else None)
            return minimum is not None and node.get("state") == "ON" and number(node.get("coolant_flow_l_min")) and node["coolant_flow_l_min"] >= minimum
        minimum = self.config.min_supply_flow_m3_h or (1.0 if self.mode == "simulate" else None)
        return minimum is not None and node.get("state") == "ON" and number(node.get("airflow_m3_h")) and node["airflow_m3_h"] >= minimum

    def _fresh_co2_sources(self, pending, nodes, now, context):
        origins = self.config.source_origins or context.get("source_origins", {})
        origins = origins if isinstance(origins, dict) else {}
        fresh = []
        for name in ("climate_sensor_01", "climate_sensor_02", "climate_sensor_03"):
            node = _node(nodes, name)
            if (quality(node, now, self.config) == "OK" and number(node.get("updated_at"))
                    and node["updated_at"] > pending["step_started"]
                    and number(node.get("reading_delay_s", 0))
                    and 0 <= node.get("reading_delay_s", 0) <= self.config.max_measurement_delay_s
                    and number(node.get("co2_ppm")) and isinstance(origins.get(name), str) and origins[name]):
                fresh.append((origins[name], node))
        return fresh if len({origin for origin, _ in fresh}) >= 2 else []

    def _fault_effect(self, pending, nodes, now, context):
        if pending["fault"]["code"] == "CO2_CONFIRMED":
            current = self._fresh_co2_sources(pending, nodes, now, context)
            return bool(current) and all(node["co2_ppm"] <= self.config.co2_threshold_ppm for _, node in current)
        return True

    def _finish(self, pending, outcome, now, reason):
        evidence = {"actual_execution": pending["sent"] > 0, "effect_confirmed": outcome == "VERIFIED_SUCCESS",
                    "fresh_observations": pending["total_observations"], "post_timestamp": now,
                    "reason": reason, "stable_window_s": self.config.verification_window_s,
                    "verified_steps": pending["verified_steps"]}
        self.store.finish_attempt(pending["attempt_id"], outcome, evidence, now)
        self._emit("verification", ref=pending.get("audit_action"), expected="fresh stable physical effect", observed=evidence, ok=outcome == "VERIFIED_SUCCESS")
        self.active.pop(pending["incident_id"], None)
        return {"attempt_id": pending["attempt_id"], "incident_id": pending["incident_id"], "outcome": outcome, "evidence": evidence}

    async def _send_step(self, pending, nodes, now, context, transport):
        request = pending["actions"][pending["step"]]
        allowed, reason = self.gate([request], pending["fault"], nodes, now, context, transport)
        if not allowed:
            return self._finish(pending, "UNKNOWN" if pending["sent"] else "BLOCKED", now, reason)
        target = _node(nodes, request["node_id"])
        pending.update(step_before=target["updated_at"], step_started=now, observations=set(), seen_updates=set(), stable_since=None)
        # SQLite commits intent before the transport can see the request.
        self.store.append_attempt_event(pending["attempt_id"], "send_intent", {"request": request, "step": pending["step"]}, now)
        ref = self._emit("decision", situation=pending["fault"]["message"], cause=pending["fault"]["code"], confidence=0.8,
                         evidence=pending["fault"]["evidence"], plan=[request], source="rules")
        self._emit("send_intent", ref=ref, request=request)
        pending["sent"] += 1
        try:
            ack = await transport.send(deepcopy(request))
        except asyncio.CancelledError:
            pending["audit_action"] = self._emit("action", ref=ref, request=request,
                response={"success": None, "outcome": "UNKNOWN", "error": "cancelled_after_send_intent"})
            self._finish(pending, "UNKNOWN", now, "send_cancelled_reconciliation_required")
            raise
        except Exception as exc:
            pending["audit_action"] = self._emit("action", ref=ref, request=request,
                response={"success": None, "outcome": "UNKNOWN", "error": type(exc).__name__})
            self.store.append_attempt_event(pending["attempt_id"], "transport_error", {"error": type(exc).__name__}, now)
            return self._finish(pending, "UNKNOWN", now, "transport_result_unknown_no_retry")
        pending["audit_action"] = self._emit("action", ref=ref, request=request, response=safe(ack))
        self.store.append_attempt_event(pending["attempt_id"], "ack", {"request": request, "response": safe(ack)}, now)
        if not isinstance(ack, dict) or ack.get("success") is not True:
            return self._finish(pending, "FAILED" if isinstance(ack, dict) and ack.get("success") is False else "UNKNOWN", now, "API_rejected" if isinstance(ack, dict) and ack.get("success") is False else "invalid_ack")
        return {"attempt_id": pending["attempt_id"], "outcome": "PENDING", "reason": "ACK_IS_NOT_EFFECT"}

    async def _advance(self, nodes, now, context, transport):
        results = []
        for pending in list(self.active.values()):
            request = pending["actions"][pending["step"]]
            node = _node(nodes, request["node_id"])
            allowed, reason = self.gate([request], pending["fault"], nodes, now, context, transport)
            if not allowed:
                results.append(self._finish(pending, "UNKNOWN", now, reason)); continue
            updated = node.get("updated_at")
            fresh = number(updated) and updated > pending["step_before"] and updated not in pending["seen_updates"]
            if fresh:
                pending["seen_updates"].add(updated)
                if self._effect(request, nodes, now):
                    pending["observations"].add(updated)
                    pending["total_observations"] += 1
                    if pending["stable_since"] is None:
                        pending["stable_since"] = updated
                else:
                    pending["observations"].clear(); pending["stable_since"] = None
                self.store.append_attempt_event(pending["attempt_id"], "verification_observation",
                    {"target": request["node_id"], "updated_at": updated, "effect": self._effect(request, nodes, now)}, now)
            stable = pending["stable_since"] is not None and updated - pending["stable_since"] >= self.config.verification_window_s
            if stable and len(pending["observations"]) >= self.config.verification_observations:
                if len(pending["verified_steps"]) <= pending["step"]:
                    pending["verified_steps"].append({"request": request, "observations": sorted(pending["observations"]), "effect_confirmed": True})
                if pending["step"] + 1 == len(pending["actions"]):
                    if self._fault_effect(pending, nodes, now, context) and all(self._effect(action, nodes, now) for action in pending["actions"]):
                        results.append(self._finish(pending, "VERIFIED_SUCCESS", now, "all_steps_verified_by_fresh_stable_observations"))
                    elif now - pending["step_started"] >= self.config.verification_timeout_s:
                        co2_known = pending["fault"]["code"] != "CO2_CONFIRMED" or bool(self._fresh_co2_sources(pending, nodes, now, context))
                        results.append(self._finish(pending, "FAILED" if fresh and co2_known else "UNKNOWN", now,
                                                    "actuator_changed_but_incident_not_resolved" if fresh and co2_known else "no_new_usable_fault_measurement_at_deadline"))
                else:
                    pending["step"] += 1
                    results.append(await self._send_step(pending, nodes, now, context, transport))
            elif now - pending["step_started"] >= self.config.verification_timeout_s:
                known_failure = fresh and not self._effect(request, nodes, now)
                results.append(self._finish(pending, "FAILED" if known_failure else "UNKNOWN", now,
                                            "fresh_measurement_shows_no_effect" if known_failure else "insufficient_fresh_verification_at_deadline"))
        return results

    async def process(self, record: dict, transport=None) -> dict:
        async with self.lock:
            if not isinstance(record, dict):
                raise ValueError("Expected JSON object")
            if record.get("kind") == "incoming" and isinstance(record.get("payload"), dict):
                record = record["payload"]
            data = record.get("data", record)
            nodes = data.get("nodes", {}) if isinstance(data, dict) else {}
            if not isinstance(nodes, dict):
                nodes = {}
            now = record.get("timestamp")
            if not number(now):
                times = [v.get("updated_at") for v in nodes.values() if isinstance(v, dict) and number(v.get("updated_at"))]
                now = max(times) if times else 0.0
            base = {"mode": self.mode, "environment": self.environment, "timestamp": now, "faults": [], "outcomes": [], "sent": 0}
            if self.last_timestamp is not None and now <= self.last_timestamp:
                return {**base, "ignored": "DUPLICATE_OR_OUT_OF_ORDER"}
            self.last_timestamp = now
            context = record.get("context") if isinstance(record.get("context"), dict) else {}
            context = {**context, "_frame_role": record.get("role")}
            if "_received_monotonic" in record:
                context["_received_monotonic"] = record["_received_monotonic"]
            if record.get("role") not in {None, "team"}:
                context = {**context, "emergency_stop": None}
            observed = diagnose(nodes, now, context, self.config)
            context["_critical_incident"] = any(f["severity"] == "critical" for f in observed)
            observation_signature = (len(nodes), tuple((f["code"], f["target"]) for f in observed))
            local_now = time.monotonic()
            if observation_signature != self._last_observation_signature or local_now - self._last_observation_at >= 30:
                self._emit("observation", summary="Причинная диагностика", metrics={"nodes": len(nodes), "faults": len(observed)}, anomalies=[f["code"] for f in observed])
                self._last_observation_signature, self._last_observation_at = observation_signature, local_now
            base["outcomes"] = await self._advance(nodes, now, context, transport) if self.active else []
            membership = self.membership(nodes)
            base["subsystem_signals"] = {s["id"]: [p for p, owners in membership.items() if s["id"] in owners] for s in self.subsystems}
            for fault in observed:
                fingerprint = hashlib.sha256(f"{fault['code']}:{fault['target']}".encode()).hexdigest()
                incident = self.store.open_incident(fingerprint, fault["code"], fault["subsystems"], safe(fault["evidence"]), self.environment, now)
                fault["incident_id"] = incident
                query = f"{fault['code']} {fault['target']} {fault['message']} {' '.join(fault['subsystems'])}"
                retrieved = self.retriever.retrieve(query, self.environment, 8, telemetry=record, faults=[fault]) if self.retriever else []
                candidates = fault["candidates"]
                for candidate in candidates:
                    candidate["version"] = self.config.runbook_version
                    candidate["memory"] = self.store.stats(fault["code"], candidate["runbook_id"], self.config.runbook_version, self.environment)
                    candidate["rank"] = candidate["priority"] + candidate["memory"]["score"] - min(1.0, candidate["memory"]["failures"] * 0.4)
                candidates.sort(key=lambda c: (-c["rank"], c["runbook_id"]))
                selected = candidates[0]["runbook_id"]
                model_result = self.plan_cache.get(incident)
                # A temporary DeepSeek outage must not disable an incident's diagnosis forever.
                if model_result and "error" in model_result and time.monotonic() - self._plan_cached_at.get(incident, 0) >= 15:
                    model_result = None
                if self.planner and model_result is None and fault["code"] not in {"EMERGENCY", "MASS_TELEMETRY_LOSS"}:
                    llm_started = time.monotonic()
                    try:
                        model_result = await asyncio.to_thread(self.planner.choose, safe(fault), safe(candidates), safe(retrieved))
                        if not isinstance(model_result, dict) or "runbook_id" not in model_result or (
                            model_result["runbook_id"] is not None and (model_result["runbook_id"] not in {c["runbook_id"] for c in candidates}
                                                                       or model_result["runbook_id"] not in RUNBOOKS)):
                            raise ValueError("Planner selected an unreviewed runbook")
                        model_result = safe(model_result)
                        usage = model_result.get("usage") if isinstance(model_result.get("usage"), dict) else {}
                        self._emit("llm_call", model=getattr(self.planner, "model", "configured-planner"),
                                   served_model=model_result.get("served_model"), purpose=fault["code"], ok=True,
                                   prompt_tokens=usage.get("prompt_tokens") if type(usage.get("prompt_tokens")) is int else None,
                                   completion_tokens=usage.get("completion_tokens") if type(usage.get("completion_tokens")) is int else None,
                                   latency_ms=model_result.get("latency_ms") if number(model_result.get("latency_ms")) else round((time.monotonic() - llm_started) * 1000),
                                   error=None, summary=model_result.get("diagnosis", ""))
                    except Exception as exc:
                        model_result = {"error": type(exc).__name__, "fallback": "reviewed_rules"}
                        self._emit("llm_call", model=getattr(self.planner, "model", "configured-planner"),
                                   served_model=None, purpose=fault["code"], ok=False,
                                   prompt_tokens=None, completion_tokens=None,
                                   latency_ms=round((time.monotonic() - llm_started) * 1000),
                                   summary=None, error=type(exc).__name__)
                    self.plan_cache[incident] = model_result
                    self._plan_cached_at[incident] = time.monotonic()
                if model_result and model_result.get("runbook_id") in {c["runbook_id"] for c in candidates}:
                    selected = model_result["runbook_id"]
                abstained = bool(model_result and "error" not in model_result and model_result.get("runbook_id") is None)
                if abstained:
                    selected = "check_telemetry"
                actions = [] if abstained else self._actions(selected, fault, nodes)
                allowed, reason = self.gate(actions, fault, nodes, now, context, transport) if actions else (False, "DIAGNOSIS_OR_ESCALATION_ONLY")
                if any(f["severity"] == "critical" for f in observed):
                    allowed, reason = False, "CRITICAL_INCIDENT_HAS_PRIORITY"
                targets = {a["node_id"] for a in actions}
                if any(p["incident_id"] != incident and targets.intersection(a["node_id"] for a in p["actions"]) for p in self.active.values()):
                    allowed, reason = False, "TARGET_HAS_PENDING_SCENARIO"
                entry = {**fault, "selected_runbook": selected, "runbook_version": self.config.runbook_version,
                         "retrieved": safe(retrieved), "planner": model_result, "planner_abstained": abstained, "proposed_actions": actions,
                         "gate": {"allowed": allowed, "reason": reason}}
                previous = self.store.attempts_for_incident(incident)
                attempted = [a for a in previous if a["mode"] in {"execute", "simulate"} and a["state"] in {"PENDING", "UNKNOWN", "FAILED", "VERIFIED_SUCCESS"} and a["actions"]]
                if attempted:
                    entry["outcome"] = attempted[-1]["state"]
                    entry["gate"] = {"allowed": False, "reason": "PRIOR_ATTEMPT_REQUIRES_RECONCILIATION_NO_RETRY"}
                elif self.mode in {"observe", "replay", "dry_run"} or not allowed:
                    outcome = "DRY_RUN" if self.mode in {"observe", "replay", "dry_run"} else "BLOCKED"
                    if not previous:
                        attempt = self.store.begin_attempt(incident, selected, self.config.runbook_version, self.environment, self.mode, [], now)
                        self.store.append_attempt_event(attempt, "decision", {"fault": safe(fault), "selected_runbook": selected, "gate": entry["gate"], "retrieved": safe(retrieved)}, now)
                        self.store.finish_attempt(attempt, outcome, {"actual_execution": False, "effect_confirmed": False, "reason": reason}, now)
                    entry["outcome"] = outcome
                else:
                    ready = self.planned.get(incident)
                    if self.mode == "execute" and (ready is None or ready["timestamp"] >= now or _node(nodes, fault["target"]).get("updated_at", 0) <= ready["target_updated"]):
                        if ready is None:
                            self.planned[incident] = {"timestamp": now, "target_updated": _node(nodes, fault["target"]).get("updated_at", now)}
                        entry["outcome"] = "WAITING_FOR_FRESH_FRAME_AFTER_PLAN"
                    else:
                        attempt = self.store.begin_attempt(incident, selected, self.config.runbook_version, self.environment, self.mode, actions, now)
                        pending = {"attempt_id": attempt, "incident_id": incident, "fault": fault, "actions": actions,
                                   "step": 0, "sent": 0, "verified_steps": [], "total_observations": 0}
                        self.active[incident] = pending
                        self.store.append_attempt_event(attempt, "plan", {"selected_runbook": selected, "retrieved": safe(retrieved), "evidence": safe(fault["evidence"])}, now)
                        outcome = await self._send_step(pending, nodes, now, context, transport)
                        base["outcomes"].append(outcome)
                        entry["outcome"] = outcome["outcome"]
                        base["sent"] += pending["sent"]
                self._emit("decision", situation=fault["message"], cause=fault["code"], confidence=0.5,
                           evidence=fault["evidence"], plan=actions,
                           source="mixed" if retrieved or (model_result and "error" not in model_result) else "rules",
                           rag_refs=[hit["id"] for hit in retrieved if isinstance(hit.get("id"), str)],
                           selected_runbook=selected, mode=self.mode, proposed_only=self.mode in {"observe", "replay", "dry_run"},
                           gate=entry["gate"], outcome=entry.get("outcome"),
                           planner_abstained=abstained)
                base["faults"].append(entry)
            return safe(base)

    async def shutdown(self):
        for pending in list(self.active.values()):
            self._finish(pending, "UNKNOWN", self.last_timestamp or 0, "shutdown_before_verification_no_automatic_retry")
        self._emit("stop", reason="shutdown")
        if self._audit_stream:
            self._audit_stream.close(); self._audit_stream = None


def demo_snapshot(fault="fume_stopped"):
    now = 1000.0
    def node(**data):
        return {"online": True, "status": "OK", "updated_at": now, **data}
    nodes = {
        "smart_panel_01": node(bus_powered=True, bus_voltage_v=230, lines=[{"line": 1, "state": "ON", "protection_state": "ARMED", "lockout_remaining_s": 0, "voltage_v": 230}]),
        "dome_automation_01": node(interlocks={"IL_CO2": {"state": "ARMED"}, "IL_SMOKE": {"state": "ARMED"}, "IL_STORM": {"state": "ARMED"}}),
        "fume_extraction_01": node(enabled=True, motor_state="OK", fan_rpm=2000, airflow_pct=90, alarm=False),
        "supply_ventilation_01": node(state="ON", mode="MIXED", speed_pct=40, airflow_m3_h=200, fresh_air_m3_h=100, alarm=False),
        "equipment_cooling_01": node(state="ON", coolant_flow_l_min=4, alarm=False),
        "climate_sensor_01": node(co2_ppm=730, temperature_c=21, humidity_pct=45),
        "climate_sensor_02": node(co2_ppm=740, reading_delay_s=900),
        "climate_sensor_03": node(co2_ppm=750, reading_delay_s=1800),
        "weather_station_01": node(wind_speed_ms=5), "dome_sealing_01": node(state="OPEN"),
        "solar_inverter_01": node(power_deficit_w=0, inverter_temp_c=38.9, error_code=None, fan_mode="AUTO", fan_state="OFF"),
        "battery_01": node(soc_pct=80, state="DISCHARGING"),
        "printer_3d_01": node(state="IDLE", hold_reason=None, hold_level=None),
        "cnc_01": node(state="IDLE", hold_reason=None, hold_level=None),
        "smoke_detector_01": node(smoke_detected=False, alarm_state="NORMAL"),
        "smoke_detector_02": node(smoke_detected=False, alarm_state="NORMAL"),
    }
    if fault == "fume_stopped":
        nodes["fume_extraction_01"].update(enabled=False, fan_rpm=0, airflow_pct=0)
    elif fault == "fume_power":
        nodes["smart_panel_01"]["lines"][0]["state"] = "OFF"
        nodes["fume_extraction_01"]["online"] = False
    elif fault == "co2":
        for name in ("climate_sensor_01", "climate_sensor_02", "climate_sensor_03"):
            nodes[name].update(co2_ppm=2500, reading_delay_s=0)
    elif fault == "cooling":
        nodes["printer_3d_01"]["state"] = "PRINTING"
        nodes["equipment_cooling_01"].update(state="OFF", coolant_flow_l_min=0)
    return {"type": "telemetry", "timestamp": now, "role": "team", "data": {"nodes": nodes},
            "context": {"operator_activity": "SOLDERING", "required_fume": True, "emergency_stop": False, "trusted": True,
                        "source_origins": {n: n for n in ("climate_sensor_01", "climate_sensor_02", "climate_sensor_03")}}}


class MockTransport:
    def __init__(self, snapshot: dict, effect=True):
        self.snapshot, self.effect, self.role = deepcopy(snapshot), effect, "team"
        self.sent = []
        self.catalog = {node: {"controls": sorted(action for target, action in COMMANDS if target == node)}
                        for node in {target for target, _ in COMMANDS}}

    async def send(self, request):
        if (request.get("node_id"), request.get("action")) not in COMMANDS:
            raise ValueError("Mock only accepts the reviewed command pairs")
        self.sent.append(deepcopy(request))
        if self.effect:
            node = self.snapshot["data"]["nodes"][request["node_id"]]
            if request["action"] == "turn_on":
                if request["node_id"] == "fume_extraction_01":
                    node.update(enabled=True, fan_rpm=2000, airflow_pct=90)
                elif request["node_id"] == "equipment_cooling_01":
                    node.update(state="ON", coolant_flow_l_min=4)
                else:
                    node.update(state="ON", airflow_m3_h=200, fresh_air_m3_h=100)
            else:
                node["mode" if request["action"] == "set_mode" else "speed_pct"] = request["value"]
                node["fresh_air_m3_h"] = 300
        return {"success": True, "mode": "mock"}

    def tick(self, seconds=4):
        if not number(seconds) or seconds < 0:
            raise ValueError("Invalid tick")
        self.snapshot["timestamp"] += seconds
        nodes = self.snapshot["data"]["nodes"]
        supply = nodes.get("supply_ventilation_01", {})
        if self.effect and supply.get("mode") == "FRESH_AIR" and supply.get("speed_pct") == 100:
            for name in ("climate_sensor_01", "climate_sensor_02", "climate_sensor_03"):
                if number(nodes.get(name, {}).get("co2_ppm")):
                    nodes[name]["co2_ppm"] = max(730, nodes[name]["co2_ppm"] - 300)
        for node in self.snapshot["data"]["nodes"].values():
            if isinstance(node, dict):
                node["updated_at"] = self.snapshot["timestamp"]
        return deepcopy(self.snapshot)
