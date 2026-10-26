"""Reviewed manual API commands and bounded UTF-8 file transfer.

These helpers do not send requests and do not enlarge the autonomous allowlist.
The current transport role, command serialization and result journaling belong
to the runtime. A successful ACK is never a physical-effect verification.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
import re
import time


class ControlError(ValueError):
    pass


MAX_UPLOAD_BYTES = 16 * 1024 * 1024
MAX_API_MESSAGE_BYTES = 32 * 1024 * 1024
MACHINES = frozenset({"printer_3d_01", "cnc_01"})
EXTENSIONS = frozenset({".gcode", ".nc", ".tap", ".ngc"})
_BLOCKED = frozenset({"reset_protection", "reset_line_error", "reset_interlock", "block_interlock",
    "unblock_interlock", "acknowledge", "block_automation", "unblock_automation", "manual_start_zone",
    "set_data_trust", "set_threshold", "set_thresholds", "set_primary_sensor", "set_ventilation_auto",
    "set_diesel_autostart", "reset_alarm", "reset_error", "reboot"})
_ACTIONS = {
    "smart_panel_01": {"line_on", "line_off"},
    "printer_3d_01": {"upload_file", "delete_file", "home", "start_job", "resume", "set_speed"},
    "cnc_01": {"upload_file", "home", "start_job", "resume"},
    "fume_extraction_01": {"turn_on", "turn_off"},
    "supply_ventilation_01": {"turn_on", "turn_off", "set_speed", "set_mode"},
    "equipment_cooling_01": {"turn_on", "turn_off", "set_speed"},
    "water_pump_01": {"turn_on", "turn_off", "set_power_limit"},
    "water_filter_01": {"switch_to_reserve"},
    "wind_turbine_01": {"turn_on", "turn_off"},
    "dizel_1": {"turn_on", "turn_off", "preheat"},
    "solar_inverter_01": {"set_mode_hybrid", "set_mode_grid_only", "set_fan"},
    "radio_01": {"set_frequency", "set_protocol"},
    "backup_comms_01": {"switch_channel", "set_protocol", "set_traffic_priority"},
    "edge_compute_01": {"limit_load", "sync_time"},
}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _range(value, low, high, name):
    if not _number(value) or not low <= value <= high:
        raise ControlError(f"Некорректное значение {name}: допустимо {low}..{high}")
    return value


def _catalog(catalog):
    if isinstance(catalog, list):
        return {n["node_id"]: n for n in catalog if isinstance(n, dict) and isinstance(n.get("node_id"), str)}
    return catalog if isinstance(catalog, dict) else {}


def _strings(value):
    return {item for item in value if isinstance(item, str)} if isinstance(value, (list, tuple, set)) else set()


def available_controls(catalog, *, include_upload=False):
    """The model sees only reviewed canonical commands advertised right now."""
    result = {}
    for node_id, node in _catalog(catalog).items():
        if not isinstance(node, dict):
            continue
        allowed = _ACTIONS.get(node_id, set()) & _strings(node.get("controls"))
        allowed -= _strings(node.get("operator_controls")) | _strings(node.get("service_actions")) | _BLOCKED
        if not include_upload:
            allowed.discard("upload_file")
        if allowed:
            result[node_id] = {"title": node.get("title", node_id), "controls": sorted(allowed)}
    return result


def safe_filename(filename):
    if (not isinstance(filename, str) or not filename or len(filename) > 180
            or filename != filename.strip() or "\x00" in filename
            or any(ord(c) < 32 for c in filename) or any(c in filename for c in '/\\:')):
        raise ControlError("Нужно простое имя файла без пути и управляющих символов")
    if (PurePosixPath(filename).name != filename or PureWindowsPath(filename).name != filename
            or filename in {".", ".."} or filename.endswith((".", " "))):
        raise ControlError("Недопустимое имя файла")
    if PurePosixPath(filename).suffix.casefold() not in EXTENSIONS:
        raise ControlError("Допустимы текстовые файлы .gcode, .nc, .tap, .ngc")
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", filename):
        raise ControlError("Зарезервированное имя файла")
    return filename


def prepare_upload(node_id, filename, content):
    if not isinstance(node_id, str) or node_id not in MACHINES:
        raise ControlError("Файлы можно передавать только на 3D-принтер или фрезер")
    filename = safe_filename(filename)
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_UPLOAD_BYTES:
        raise ControlError("Нужен непустой файл организаторов размером не более 16 МиБ")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ControlError("Файл должен содержать текст UTF-8, а не бинарный STL/архив") from None
    if not text.strip() or "\x00" in text:
        raise ControlError("Файл пуст или содержит бинарные данные")
    request = {"type": "control", "node_id": node_id, "action": "upload_file",
               "value": {"file_name": filename, "content": text}}
    # The WebSocket limit applies to serialized JSON, not just the source file.
    # Newlines/quotes/control characters expand during escaping. Reserve a long
    # request_id for the transport, which adds it immediately before sending.
    encoded = json.dumps({**request, "request_id": "0" * 64}, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_API_MESSAGE_BYTES:
        raise ControlError("Файл после JSON-кодирования превышает лимит сообщения API 32 МиБ")
    return request


def normalize_files_response(response, node_id):
    """Normalize documented query_response.result without guessing file validity."""
    if not isinstance(node_id, str) or node_id not in MACHINES or not isinstance(response, dict) or response.get("success") is not True:
        raise ControlError("API не подтвердил получение списка файлов станка")
    if ((response.get("node_id") is not None and response["node_id"] != node_id)
            or (response.get("query") is not None and response["query"] != "list_files")):
        raise ControlError("Ответ со списком файлов относится к другому запросу")
    rows = response.get("result")
    if isinstance(rows, dict):
        rows = rows.get("files")
    if not isinstance(rows, list):
        raise ControlError("Ответ list_files не содержит ожидаемого списка result")
    result, seen = [], set()
    for row in rows[:1000]:
        item = {"name": row} if isinstance(row, str) else row if isinstance(row, dict) else None
        if item is None:
            continue
        name = item.get("name", item.get("file_name"))
        try:
            safe_filename(name)
        except ControlError:
            continue
        if name in seen:
            continue
        seen.add(name)
        normalized = {"name": name}
        for field in ("size_bytes", "estimated_time_s", "filament_g"):
            value = item.get(field)
            if _number(value) and value >= 0:
                normalized[field] = value
        if type(item.get("valid")) is bool:
            normalized["valid"] = item["valid"]
        if isinstance(item.get("error"), str):
            normalized["error"] = item["error"][:1200]
        if isinstance(item.get("modified"), str) or _number(item.get("modified")):
            normalized["modified"] = item["modified"]
        result.append(normalized)
    return result


def canonical_request(request, catalog, *, allow_upload=False):
    if (not isinstance(request, dict) or set(request) - {"type", "node_id", "action", "value"}
            or request.get("type") != "control"):
        raise ControlError("Ожидается одна каноническая управляющая команда API")
    node_id, action = request.get("node_id"), request.get("action")
    if not isinstance(node_id, str) or not isinstance(action, str):
        raise ControlError("Нужно строковое имя узла и действия")
    node = _catalog(catalog).get(node_id)
    if not isinstance(node, dict) or not isinstance(action, str):
        raise ControlError("Узел или действие отсутствует в актуальном каталоге")
    if (action in _BLOCKED or action not in _ACTIONS.get(node_id, set())
            or action not in _strings(node.get("controls")) or action in _strings(node.get("operator_controls"))
            or action in _strings(node.get("service_actions"))):
        if action == "upload_file" and node_id == "cnc_01":
            raise ControlError("Текущий API фрезера не разрешает upload_file; доступны только уже загруженные файлы")
        raise ControlError("Действие не входит в проверенные разрешённые команды этого узла")
    result = {"type": "control", "node_id": node_id, "action": action}
    value = request.get("value")
    if action in {"line_on", "line_off"}:
        value = value.get("line") if isinstance(value, dict) and set(value) == {"line"} else value
        if type(value) is not int or not 1 <= value <= 8:
            raise ControlError("Номер линии должен быть целым числом 1..8")
    elif action == "upload_file":
        if not allow_upload or not isinstance(value, dict) or set(value) != {"file_name", "content"} or not isinstance(value["content"], str):
            raise ControlError("Файл передаётся через отдельную форму загрузки, а не генерируется чатом")
        return prepare_upload(node_id, value["file_name"], value["content"].encode("utf-8"))
    elif action in {"start_job", "delete_file"}:
        value = value.get("file_name") if isinstance(value, dict) and set(value) == {"file_name"} else value
        value = safe_filename(value)
    elif action == "set_speed":
        value = value.get("pct") if isinstance(value, dict) and set(value) == {"pct"} else value
        lower, upper = (50, 200) if node_id == "printer_3d_01" else (10, 100) if node_id == "supply_ventilation_01" else (0, 100)
        value = _range(value, lower, upper, "скорости")
    elif action == "set_power_limit":
        value = _range(value, 20, 100, "мощности насоса")
    elif action == "limit_load":
        value = _range(value, 10, 100, "нагрузки")
    elif action == "set_frequency":
        value = _range(value, 430, 440, "частоты")
    elif action == "set_mode":
        if not isinstance(value, str) or value not in {"RECIRCULATION", "FRESH_AIR", "MIXED"}:
            raise ControlError("Неизвестный режим вентиляции")
    elif action == "set_fan":
        if not isinstance(value, str) or value not in {"AUTO", "ON", "OFF"}:
            raise ControlError("Неизвестный режим вентилятора инвертора")
    elif action == "set_protocol":
        allowed = {"AX25", "FSK", "LORA", "DMR"} if node_id == "radio_01" else {"IRIDIUM_SBD", "IP", "LORAWAN", "MESHTASTIC", "BATMAN", "OLSR"}
        if not isinstance(value, str) or value not in allowed:
            raise ControlError("Неизвестный протокол связи")
    elif action == "switch_channel":
        if not isinstance(value, str) or value not in {"SATELLITE", "LORA", "MESH"}:
            raise ControlError("Неизвестный канал связи")
    elif action == "set_traffic_priority":
        if not isinstance(value, str) or value not in {"NORMAL", "TELEMETRY", "EMERGENCY"}:
            raise ControlError("Неизвестный приоритет связи")
    else:
        if value is not None:
            raise ControlError("Для этого действия параметр value не предусмотрен")
        return result
    result["value"] = value
    return result


def _nodes(frame):
    data = frame.get("data") if isinstance(frame, dict) else None
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, dict):
        raise ControlError("Нет текущего снимка телеметрии")
    return nodes


def _fresh(frame, node_id, *, max_age=15):
    nodes = _nodes(frame)
    node = nodes.get(node_id)
    now, received = frame.get("timestamp"), frame.get("_received_monotonic")
    updated = node.get("updated_at") if isinstance(node, dict) else None
    if (frame.get("role") != "team" or not _number(now) or not _number(received)
            or not 0 <= time.monotonic() - received <= max_age
            or not isinstance(node, dict) or node.get("online") is not True or node.get("status") != "OK"
            or not _number(updated) or not -.001 <= now - updated <= max_age):
        raise ControlError(f"Нет свежих достоверных данных узла {node_id}")
    return node


def _line(panel, number):
    lines = panel.get("lines")
    found = [line for line in lines if isinstance(line, dict) and line.get("line") == number] if isinstance(lines, list) else []
    if len(found) != 1:
        raise ControlError(f"Линия {number} не определена однозначно")
    return found[0]


def _no_lockout(node):
    delay = node.get("lockout_remaining_s")
    return _number(delay) and delay == 0 and not node.get("lockout_reason")


def _file_names(node):
    files = node.get("files")
    if not isinstance(files, list):
        return None
    result = set()
    for item in files:
        name = item if isinstance(item, str) else item.get("file_name", item.get("name")) if isinstance(item, dict) else None
        if isinstance(name, str):
            result.add(name)
    return result


def _production_ready(frame, node_id, action):
    nodes = _nodes(frame)
    machine = _fresh(frame, node_id)
    plc = _fresh(frame, "dome_automation_01")
    interlocks = plc.get("interlocks")
    if (not isinstance(interlocks, dict) or not interlocks
            or any(not isinstance(item, dict) or item.get("state") != "ARMED" for item in interlocks.values())):
        raise ControlError("Запуск/движение запрещены: состояние блокировок не подтверждено")
    for detector in ("smoke_detector_01", "smoke_detector_02"):
        if _fresh(frame, detector).get("smoke_detected") is not False:
            raise ControlError("Запуск/движение запрещены: дым или неизвестное состояние датчика")
    if (machine.get("alarm") is True or machine.get("error_code") or machine.get("hold_reason")
            or machine.get("hold_level") or not _no_lockout(machine)):
        raise ControlError("Запуск/движение запрещены: авария, удержание или блокировка станка")
    if machine.get("state") not in {"IDLE", "COMPLETED", "PAUSED"}:
        raise ControlError("Станок занят или его состояние не позволяет запуск")
    if action != "resume" and machine.get("state") == "PAUSED":
        raise ControlError("Станок на паузе: новая задача не запускается поверх текущей")
    if action in {"start_job", "resume"} and machine.get("homed") is not True:
        raise ControlError("Перед запуском должна быть подтверждена процедура home")
    if node_id == "printer_3d_01" and action == "start_job" and machine.get("bed_clear") is not True:
        raise ControlError("Запуск печати запрещён: свободный стол не подтверждён; готовую деталь снимает Оператор")
    panel = _fresh(frame, "smart_panel_01")
    supply = _line(panel, 3 if node_id == "printer_3d_01" else 4)
    if panel.get("bus_powered") is not True or supply.get("state") != "ON" or supply.get("protection_state") != "ARMED" or supply.get("alarm") is not False:
        raise ControlError("Питание производственной линии не подтверждено")
    if action in {"start_job", "resume"}:
        cooling = _fresh(frame, "equipment_cooling_01")
        flow = cooling.get("coolant_flow_l_min")
        if cooling.get("state") != "ON" or cooling.get("alarm") is not False or not _number(flow) or flow <= 0:
            raise ControlError("Рабочий поток охлаждения станков не подтверждён")
    return machine


def validate_manual(request, catalog, frame):
    """Validate against the *current* frame again immediately before dispatch."""
    canonical = canonical_request(request, catalog, allow_upload=True)
    node_id, action = canonical["node_id"], canonical["action"]
    value = canonical.get("value")
    node = _fresh(frame, node_id)
    nodes = _nodes(frame)
    if node_id == "smart_panel_01":
        line = _line(node, value)
        if value == 7:
            name = str(line.get("name", "")).casefold()
            if not (("аварийн" in name and "освещ" in name) or ("emergency" in name and "light" in name)):
                raise ControlError("Назначение линии 7 как аварийного освещения не подтверждено")
        if action == "line_on" and (node.get("bus_powered") is not True or line.get("protection_state") != "ARMED"
                or line.get("alarm") is not False or not _no_lockout(line)):
            raise ControlError("Включение линии запрещено: питание, защита или блокировка не подтверждены")
    if node_id in MACHINES:
        if action in {"home", "start_job", "resume"}:
            node = _production_ready(frame, node_id, action)
        if action in {"start_job", "delete_file"}:
            names = _file_names(node)
            if names is None or value not in names:
                raise ControlError("Файл отсутствует в текущем списке файлов станка")
            if action == "start_job":
                for item in node.get("files", []):
                    if isinstance(item, dict) and item.get("name", item.get("file_name")) == value and item.get("valid") is False:
                        raise ControlError("Сервер пометил выбранный G-code как невалидный; запуск запрещён")
        if action == "delete_file" and node.get("file_name") == value and node.get("state") in {"PRINTING", "RUNNING", "PAUSED"}:
            raise ControlError("Нельзя удалить файл текущей задачи")
        if action == "upload_file":
            if node.get("file_name") == value["file_name"] and node.get("state") in {"PRINTING", "RUNNING", "PAUSED"}:
                raise ControlError("Нельзя перезаписать файл текущей задачи")
        if action == "set_speed" and node.get("state") != "PRINTING":
            raise ControlError("Изменение скорости печати применимо к работающему принтеру")
    if node_id == "equipment_cooling_01" and (action == "turn_off" or action == "set_speed" and value == 0):
        if any(nodes.get(n, {}).get("state") in {"PRINTING", "RUNNING"} for n in MACHINES if isinstance(nodes.get(n), dict)):
            raise ControlError("Нельзя отключать охлаждение работающих станков")
    if node_id == "fume_extraction_01" and action == "turn_off":
        if any(nodes.get(n, {}).get("state") in {"PRINTING", "RUNNING"} for n in MACHINES if isinstance(nodes.get(n), dict)):
            raise ControlError("Нельзя отключать вытяжку при работающем производстве")
    if node_id == "supply_ventilation_01" and action == "set_mode" and value == "FRESH_AIR":
        weather, sealing = _fresh(frame, "weather_station_01"), _fresh(frame, "dome_sealing_01")
        wind = weather.get("wind_speed_ms")
        if not _number(wind) or wind > 20 or sealing.get("state") != "OPEN":
            raise ControlError("Режим свежего воздуха конфликтует с погодой или герметизацией")
    if node_id == "wind_turbine_01" and action == "turn_on":
        wind = _fresh(frame, "weather_station_01").get("wind_speed_ms")
        if not _number(wind) or wind > 20:
            raise ControlError("Запуск ветрогенератора запрещён при шторме или неизвестном ветре")
    if node_id == "backup_comms_01" and action == "set_protocol":
        protocols = {"SATELLITE": {"IRIDIUM_SBD", "IP"}, "LORA": {"LORAWAN", "MESHTASTIC"}, "MESH": {"BATMAN", "OLSR"}}
        channel = node.get("active_channel")
        if not isinstance(channel, str) or value not in protocols.get(channel, set()):
            raise ControlError("Протокол несовместим с текущим каналом резервной связи")
    if node_id == "dizel_1" and action == "turn_off" and node.get("state") in {"FAULT", "ERROR", "ALARM"}:
        raise ControlError("Отключение не используется для неявного сброса аварии генератора")
    return deepcopy(canonical)


def verify_effect(request, before_frame, after_frame):
    """One fresh state observation, not stable repair verification or lamp sensing.

    None means this command has no adequate telemetry predicate here. The runtime
    should report that limitation rather than converting an ACK into success.
    """
    try:
        node_id, action = request["node_id"], request["action"]
        before, after = _nodes(before_frame).get(node_id), _fresh(after_frame, node_id)
        if not isinstance(before, dict):
            return False
        if (after_frame["timestamp"] <= before_frame["timestamp"]
                or after["updated_at"] <= before["updated_at"]
                or after_frame["_received_monotonic"] <= before_frame["_received_monotonic"]):
            return False
        value = request.get("value")
        if action in {"line_on", "line_off"}:
            line = _line(after, value)
            return line.get("state") == ("ON" if action == "line_on" else "OFF")
        if action in {"turn_on", "turn_off"}:
            if "enabled" in after:
                return after.get("enabled") is (action == "turn_on")
            if node_id == "water_pump_01" and "running" in after:
                return after.get("running") is (action == "turn_on")
            if node_id in {"dizel_1", "wind_turbine_01"}:
                return after.get("state") in ({"RUNNING", "ON"} if action == "turn_on" else {"OFF", "STOPPED"})
            return after.get("state") == ("ON" if action == "turn_on" else "OFF")
        fields = {"set_mode": "mode", "set_speed": "speed_pct", "set_fan": "fan_mode",
                  "set_power_limit": "power_limit_pct", "limit_load": "load_limit_pct",
                  "set_frequency": "frequency_mhz", "set_protocol": "protocol",
                  "switch_channel": "active_channel", "set_traffic_priority": "traffic_priority"}
        if action in fields:
            return after.get(fields[action]) == value
        if action == "home":
            return after.get("homed") is True
        if action in {"start_job", "resume"}:
            state_ok = after.get("state") == ("PRINTING" if node_id == "printer_3d_01" else "RUNNING")
            return state_ok and (action == "resume" or after.get("file_name") == value)
        if action in {"upload_file", "delete_file"}:
            names = _file_names(after)
            if names is None:
                return None
            filename = value["file_name"] if action == "upload_file" else value
            return (filename in names) if action == "upload_file" else (filename not in names)
        if action == "switch_to_reserve":
            return after.get("active_filter") is not None and after.get("active_filter") != before.get("active_filter")
        return None
    except (ControlError, KeyError, TypeError):
        return False
