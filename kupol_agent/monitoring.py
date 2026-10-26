"""Bounded temporal observations for live crisis triage; no commands or LLMs."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import hashlib
import json
import math
import time

from .engine import generator_start_failure


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _node(nodes, name):
    value = nodes.get(name)
    return value if isinstance(value, dict) else {}


def _line(nodes, number):
    lines = _node(nodes, "smart_panel_01").get("lines")
    return next((item for item in lines if isinstance(item, dict) and item.get("line") == number), {}) if isinstance(lines, list) else {}


def _evidence(nodes, *paths):
    result = []
    for path in paths:
        node, _, field = path.partition(".")
        value = _node(nodes, node)
        for part in field.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, (dict, list)):
            value = json.loads(json.dumps(value, ensure_ascii=False, default=str))
        result.append({"id": "temporal:" + path, "node": node, "param": field, "value": value})
    return result


class TemporalMonitor:
    def __init__(self, max_frames=120, max_age_s=15):
        if type(max_frames) is not int or not 1 <= max_frames <= 120:
            raise ValueError("max_frames must be between 1 and 120")
        if not _number(max_age_s) or max_age_s <= 0:
            raise ValueError("max_age_s must be positive")
        self.max_age_s = max_age_s
        self.frames = deque(maxlen=max_frames)
        self._last_stamp = None
        self._signature = None
        self._last_faults = []
        self._last_metrics = {}

    def reset(self):
        self.frames.clear()
        self._last_stamp = None
        self._signature = None
        self._last_faults = []
        self._last_metrics = {}

    def _result(self, faults, metrics, ignored=None):
        signature_rows = [(f["code"], f["target"], f["severity"], tuple(sorted(str(e.get("id", "")) for e in f["evidence"]))) for f in faults]
        signature = hashlib.sha256(json.dumps(sorted(signature_rows)).encode()).hexdigest()
        changed = signature != self._signature
        if not ignored:
            self._signature = signature
            self._last_faults = deepcopy(faults)
            self._last_metrics = deepcopy(metrics)
        return {"faults": faults, "metrics": metrics, "changed": changed if not ignored else False,
                **({"ignored": ignored} if ignored else {})}

    def observe(self, frame):
        faults = []

        def add(code, target, message, severity, subsystems, evidence):
            faults.append({"code": code, "target": target, "message": message,
                           "severity": severity, "subsystems": subsystems, "evidence": evidence})

        if not isinstance(frame, dict):
            add("LIVE_DATA_UNAVAILABLE", "network_link_01", "Текущая телеметрия не получена; команды нельзя основывать на прошлом снимке",
                "critical", ["communications", "telemetry_quality"], [])
            return self._result(faults, {"frames_in_window": len(self.frames), "nodes": 0})
        data = frame.get("data")
        nodes = data.get("nodes") if isinstance(data, dict) else None
        stamp = frame.get("timestamp")
        if not isinstance(nodes, dict) or not _number(stamp):
            add("LIVE_DATA_INVALID", "network_link_01", "Получен снимок неизвестного формата или без корректного времени",
                "critical", ["telemetry_quality", "communications"], [])
            return self._result(faults, {"frames_in_window": len(self.frames), "nodes": 0})
        if self._last_stamp is not None and stamp <= self._last_stamp:
            return self._result(deepcopy(self._last_faults), deepcopy(self._last_metrics), "DUPLICATE_OR_OUT_OF_ORDER")
        self._last_stamp = stamp
        received = frame.get("_received_monotonic")
        receipt_age = time.monotonic() - received if _number(received) else None
        if receipt_age is not None and not 0 <= receipt_age <= self.max_age_s:
            add("LIVE_FRAME_STALE", "network_link_01", "Локальный срок получения кадра истёк; история не заменяет текущие данные",
                "critical", ["telemetry_quality", "communications"], [{"id":"temporal:receipt_age_s", "value":receipt_age}])
        unavailable = []
        for name, node in nodes.items():
            if name == "meeting_01":
                continue
            if not isinstance(node, dict):
                unavailable.append(name)
                continue
            updated = node.get("updated_at")
            if (node.get("online") is not True or node.get("status") != "OK"
                    or not _number(updated) or not -.001 <= stamp - updated <= self.max_age_s):
                unavailable.append(name)
        if unavailable:
            add("LIVE_NODES_UNUSABLE", unavailable[0], "Часть узлов недоступна, имеет UNKNOWN или устаревшие данные; искать общую причину",
                "critical" if len(unavailable) >= max(2, len(nodes) / 2) else "warning",
                ["telemetry_quality", "communications", "power_distribution"],
                [{"id":"temporal:unusable_nodes", "value":unavailable}])
        smoke = [name for name in ("smoke_detector_01", "smoke_detector_02")
                 if _node(nodes, name).get("smoke_detected") is True or _node(nodes, name).get("alarm_state") == "ALARM"]
        if smoke:
            add("SMOKE_OR_FIRE", smoke[0], "Дымовая тревога требует немедленного оповещения и штатной процедуры; не ждать LLM и не подавлять защиту",
                "critical", ["safety", "air", "production"], _evidence(nodes, *[name + ".smoke_detected" for name in smoke], "air_quality_sensor_01.co_ppm"))
        inv = _node(nodes, "solar_inverter_01")
        generator_fault = generator_start_failure(nodes,stamp,self)
        if generator_fault is not None:
            add(generator_fault["code"],generator_fault["target"],generator_fault["message"],
                generator_fault["severity"],generator_fault["subsystems"],generator_fault["evidence"])
        if inv.get("error_code") == "E09":
            add("PV_ISOLATION_ERROR", "solar_inverter_01", "E09: ошибка изоляции PV; сырость является гипотезой, а не разрешением сбросить ошибку",
                "critical", ["energy", "safety", "thermal"], _evidence(nodes, "solar_inverter_01.error_code", "climate_sensor_01.humidity_pct", "weather_station_01.humidity_pct"))
        if inv.get("fan_mode") == "ON" and (inv.get("fan_state") != "ON" or (_number(inv.get("fan_current_a")) and inv["fan_current_a"] <= 0)):
            add("INVERTER_FAN_MISMATCH", "solar_inverter_01", "Вентилятор задан ON, но состояние или ток не подтверждает работу",
                "critical", ["thermal", "energy", "safety"], _evidence(nodes, "solar_inverter_01.fan_mode", "solar_inverter_01.fan_state", "solar_inverter_01.fan_current_a", "solar_inverter_01.inverter_temp_c"))
        climate_names = [f"climate_sensor_{i:02}" for i in (1, 2, 3)]
        co2 = {name:_node(nodes, name).get("co2_ppm") for name in climate_names if _number(_node(nodes, name).get("co2_ppm"))}
        delays = {name:_node(nodes, name).get("reading_delay_s", 0) for name in co2}
        if any(value > 2000 for value in co2.values()) and any(value <= 2000 for value in co2.values()):
            add("CO2_TIME_DISAGREEMENT", "climate_sensor_01", "Высокий CO₂ противоречит другим показаниям: сравнить задержки и независимость; большинство не доказывает ложную тревогу",
                "critical", ["air", "safety", "telemetry_quality"],
                _evidence(nodes, *[name + ".co2_ppm" for name in co2], *[name + ".reading_delay_s" for name in co2]))
        elif any(value > 2000 for value in co2.values()) and any(_number(value) and value > self.max_age_s for value in delays.values()):
            add("CO2_DELAYED_CORROBORATION", "climate_sensor_01", "Высокий CO₂ и задержанные источники: синхронное независимое подтверждение отсутствует",
                "critical", ["air", "telemetry_quality", "safety"], _evidence(nodes, *[name + ".co2_ppm" for name in co2], *[name + ".reading_delay_s" for name in co2]))
        printer = _node(nodes, "printer_3d_01")
        claims_complete = printer.get("state") == "COMPLETED" or printer.get("webhook_state") == "COMPLETED"
        contradicts = (printer.get("real_state") in ("PRINTING", "PAUSED") or printer.get("state") in ("PRINTING", "PAUSED")
                       or (_number(printer.get("progress_pct")) and printer["progress_pct"] < 100))
        if claims_complete and contradicts:
            add("PRINTER_COMPLETION_CONFLICT", "printer_3d_01", "Сообщение о завершении противоречит фактическому состоянию/прогрессу; не считать изделие готовым",
                "critical", ["production", "telemetry_quality", "safety"],
                _evidence(nodes, "printer_3d_01.state", "printer_3d_01.real_state", "printer_3d_01.webhook_state", "printer_3d_01.progress_pct", "printer_3d_01.z_mm"))
        fuel = _node(nodes, "fuel_tank_01")
        if _number(fuel.get("water_content_ppm")) and fuel["water_content_ppm"] > 500:
            add("FUEL_CONTAMINATION", "fuel_tank_01", "Вода в топливе выше исторического порога отказа 500 ppm; проверить топливо до повторного запуска дизеля",
                "warning", ["energy", "resources"], _evidence(nodes, "fuel_tank_01.water_content_ppm", "fuel_tank_01.fuel_quality", "dizel_1.fault_reason"))
        panel = _node(nodes, "smart_panel_01")
        lines = panel.get("lines")
        if isinstance(lines, list):
            for line in lines:
                if isinstance(line, dict) and line.get("protection_state") in ("TRIPPED", "RCD_TRIPPED"):
                    add("LINE_PROTECTION_ACTIVE", "smart_panel_01", f"Защита линии {line.get('line')} сработала; причина должна быть выяснена до восстановления",
                        "critical", ["power_distribution", "safety"], [{"id":f"temporal:line{line.get('line')}_protection", "value":deepcopy(line)}])
        # Retain only the fields used for trends; never keep full frames or file contents.
        compact = {"timestamp":stamp, "line6":_line(nodes, 6).get("state"),
            "leakage":{line.get("line"):line.get("leakage_ma") for line in lines if isinstance(line, dict)
                       and type(line.get("line")) is int} if isinstance(lines, list) else {},
            "fan_mode":inv.get("fan_mode"), "fan_state":inv.get("fan_state"), "fan_current_a":inv.get("fan_current_a"),
            "rx_power_dbm":_node(nodes, "network_link_01").get("rx_power_dbm"),
            "sensor_states":{name:_node(nodes, name).get("sensor_state") for name in climate_names}}
        self.frames.append(compact)
        recent = list(self.frames)[-6:]
        if len(recent) >= 3:
            for number in compact["leakage"]:
                values = [record["leakage"].get(number) for record in recent[-4:]]
                if len(values) >= 3 and all(_number(v) for v in values) and all(b > a for a, b in zip(values, values[1:])):
                    add("LINE_LEAKAGE_RISING", "smart_panel_01", f"В нескольких наблюдениях растёт утечка линии {number}; тренд требует проверки причины и осмотра",
                        "warning", ["power_distribution", "safety"], [{"id":f"temporal:line{number}_leakage_trend", "value":values}])
            currents = [r["fan_current_a"] for r in recent[-4:]]
            if (len(currents) >= 3 and all(r["fan_mode"] == recent[-1]["fan_mode"] and r["fan_state"] == "ON" for r in recent[-4:])
                    and all(_number(v) for v in currents) and all(b > a for a, b in zip(currents, currents[1:]))):
                add("INVERTER_FAN_CURRENT_RISING", "solar_inverter_01", "Ток вентилятора растёт при одинаковом режиме; сравнить нагрузку, температуру и механику",
                    "warning", ["thermal", "energy"], [{"id":"temporal:fan_current_trend", "value":currents}])
            powers = [r["rx_power_dbm"] for r in recent[-4:]]
            if len(powers) >= 3 and all(_number(v) for v in powers) and all(b < a for a, b in zip(powers, powers[1:])):
                add("LINK_OPTICAL_POWER_FALLING", "network_link_01", "Принимаемая оптическая мощность последовательно падает; проверить канал/SFP и резерв",
                    "warning", ["communications", "telemetry_quality"], [{"id":"temporal:rx_power_trend", "value":powers}])
            states = [r["line6"] for r in recent if r["line6"] in ("ON", "OFF")]
            toggles = sum(a != b for a, b in zip(states, states[1:]))
            quality_changed = any(a["sensor_states"] != b["sensor_states"] for a, b in zip(recent, recent[1:]))
            if toggles >= 2 and quality_changed:
                add("LIGHT_SENSOR_COOCCURRENCE", "smart_panel_01", "Переключения обычного света совпадают по времени с изменением качества датчиков; наводка пока лишь гипотеза",
                    "warning", ["power_distribution", "telemetry_quality"],
                    [{"id":"temporal:light_states", "value":states}, {"id":"temporal:sensor_states", "value":[r["sensor_states"] for r in recent]}])
        metrics = {"timestamp":stamp, "nodes":len(nodes), "unusable_nodes":len(unavailable),
            "frames_in_window":len(self.frames), "receipt_age_s":receipt_age, "co2_ppm":co2,
            "battery_soc_pct":_node(nodes, "battery_01").get("soc_pct"),
            "printer_state":printer.get("state"), "cnc_state":_node(nodes, "cnc_01").get("state")}
        faults.sort(key=lambda fault: (fault["severity"] != "critical", fault["code"], fault["target"]))
        return self._result(faults, metrics)
