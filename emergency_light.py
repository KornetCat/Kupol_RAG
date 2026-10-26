"""Manually set Dome emergency-light line 7. Never retry a control request."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import getpass
import json
import math
import os
from pathlib import Path
import sys
import time

from check_dome import HERE, MSK, ProbeTransport, normalize_address, sanitize
from kupol_agent.credentials import load_team_keys
from kupol_agent.transport import TransportError

NODE = "smart_panel_01"
LINE = 7


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def light_state(frame):
    """Use line ID, not list position; require fresh, usable panel telemetry."""
    nodes = frame.get("data", {}).get("nodes", {}) if isinstance(frame, dict) else {}
    panel = nodes.get(NODE, {}) if isinstance(nodes, dict) else {}
    stamp, received = frame.get("timestamp"), frame.get("_received_monotonic")
    updated = panel.get("updated_at") if isinstance(panel, dict) else None
    # updated_at is rounded to milliseconds, while frame timestamp retains microseconds.
    if not (_number(stamp) and _number(updated) and -0.001 <= stamp - updated <= 15
            and _number(received) and 0 <= time.monotonic() - received <= 5
            and panel.get("online") is True and panel.get("status") == "OK"):
        raise ValueError("Нет свежей достоверной телеметрии умного щитка")
    lines = panel.get("lines")
    matching = [v for v in lines if isinstance(v, dict) and v.get("line") == LINE] if isinstance(lines, list) else []
    if len(matching) != 1:
        raise ValueError("В телеметрии нет единственной линии 7 аварийного освещения")
    line = matching[0]
    name = str(line.get("name", "")).casefold()
    if not (("аварийн" in name and "освещ" in name) or ("emergency" in name and "light" in name)):
        raise ValueError("Название линии 7 не подтверждает назначение аварийного освещения")
    if line.get("state") not in {"ON", "OFF"}:
        raise ValueError("Состояние линии 7 неизвестно")
    return {"server_timestamp": stamp, "panel_updated_at": updated,
            "received_monotonic": received, "bus_powered": panel.get("bus_powered"),
            "line": dict(line)}


def matches(state, requested):
    line = state["line"]
    if line.get("state") != requested.upper():
        return False
    voltage = line.get("voltage_v")
    if requested == "on":
        return state.get("bus_powered") is True and _number(voltage) and voltage > 0
    return True


async def _fresh_state(transport, timeout, *, after_receipt=None, after_stamp=None):
    deadline = time.monotonic() + timeout
    iterator = transport.frames().__aiter__()
    while time.monotonic() < deadline:
        try:
            frame = await asyncio.wait_for(anext(iterator), max(.01, deadline - time.monotonic()))
        except StopAsyncIteration:
            raise TransportError("Поток телеметрии закрыт") from None
        try:
            state = light_state(frame)
        except (ValueError, TypeError, AttributeError):
            continue
        if after_receipt is not None and state["received_monotonic"] <= after_receipt:
            continue
        if after_stamp is not None and (state["server_timestamp"] <= after_stamp
                                       or state["panel_updated_at"] <= after_stamp):
            continue
        return state
    raise asyncio.TimeoutError


def _display(state):
    line = state["line"]
    return (f"Линия 7 — {line.get('name', 'Аварийное освещение')}: {line['state']}; "
            f"напряжение {line.get('voltage_v', '—')} В; мощность {line.get('power_w', '—')} Вт; "
            f"защита {line.get('protection_state', '—')}")


async def switch_light(url, key, team, requested, result, transport_factory=ProbeTransport):
    # Connection/auth/catalog requests remain read-only. Only one selected control is possible.
    result["stage"] = "websocket"
    print(f"Подключение к {url}...", flush=True)
    async with transport_factory(url, key, report=result, allow_control=False) as transport:
        result.update(role=transport.role, team=transport.team, stage="telemetry_before")
        print(f"Авторизация: роль {transport.role}, команда {transport.team}. Ожидание телеметрии щитка...", flush=True)
        if transport.role != "team" or str(transport.team) != str(team):
            raise TransportError(f"Требуются права команды {team}")
        before = await _fresh_state(transport, 10)
        result["before"] = before
        print(_display(before), flush=True)
        if requested == "status":
            result.update(success=True, outcome="STATUS_READ", message="Получено текущее состояние линии 7")
            return
        if matches(before, requested):
            result.update(success=True, outcome="ALREADY_SET", after=before,
                          message="Линия уже в выбранном состоянии; команда не отправлялась")
            return
        action = "line_on" if requested == "on" else "line_off"
        node = transport.catalog.get(NODE, {})
        if (action not in node.get("controls", []) or action in node.get("operator_controls", [])
                or action in node.get("service_actions", [])):
            raise ValueError(f"Команда {action} недоступна в текущем каталоге API")
        if requested == "on":
            line = before["line"]
            lockout = line.get("lockout_remaining_s")
            if (line.get("protection_state") != "ARMED" or line.get("alarm") is not False
                    or not _number(lockout) or lockout != 0 or line.get("lockout_reason")):
                raise ValueError("Включение не выполнено: защита, авария или блокировка линии 7. Сбросы не отправляются")
            if before.get("bus_powered") is not True:
                raise ValueError("Включение не выполнено: питание шины щитка не подтверждено")
        request = {"type": "control", "node_id": NODE, "action": action, "value": LINE}
        result.update(request=request, command_attempted=True, outcome="UNKNOWN", stage="control")
        print(f"Отправка {action}, линия {LINE}...", flush=True)
        transport.allow_control = True
        try:
            response = await transport.send(request)
        finally:
            transport.allow_control = False
        result["response"] = response
        if response.get("success") is not True:
            result.update(outcome="REJECTED", message="API отклонил команду; повторная отправка не выполнялась")
            return
        result["acknowledged"] = True
        result["stage"] = "verification"
        print("API принял команду. Ожидание подтверждения новой телеметрией...", flush=True)
        acknowledged_at = time.monotonic()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            after = await _fresh_state(transport, max(.01, deadline - time.monotonic()),
                                       after_receipt=acknowledged_at,
                                       after_stamp=before["server_timestamp"])
            result["after"] = after
            if matches(after, requested):
                result.update(success=True, outcome="VERIFIED", message="Выбранное состояние линии 7 подтверждено новой телеметрией")
                print(_display(after), flush=True)
                return
        raise asyncio.TimeoutError


def _save(result, folder, secrets):
    result = sanitize(result, secrets)
    for field in ("before", "after"):
        if field in result:
            result[field].pop("received_monotonic", None)
    result["finished_at"] = datetime.now(MSK).isoformat(timespec="seconds")
    (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    status = "УСПЕШНО" if result["success"] else "НЕ ПОДТВЕРЖДЕНО" if result["outcome"] == "UNKNOWN" else "НЕУСПЕШНО"
    lines = [f"АВАРИЙНОЕ ОСВЕЩЕНИЕ: {status}", f"Адрес: {result['address']}",
             f"Действие: {result['requested_state']}; линия: 7; узел: {NODE}", result.get("message", "")]
    if result.get("error"):
        lines.append(f"Причина: {result['error']}")
    for field, label in (("before", "До"), ("after", "После")):
        if result.get(field):
            lines.append(label + ": " + _display(result[field]))
    if result["outcome"] == "UNKNOWN":
        lines.append("Команда могла быть исполнена. Для сверки запустите --state status; команда автоматически не повторяется.")
    lines += [f"Отчёт: {folder.resolve()}", "Подтверждение относится к линии питания; отдельного датчика свечения ламп в этом API нет."]
    text = "\n".join(lines) + "\n"
    (folder / "result.txt").write_text(text, encoding="utf-8")
    print("\n" + text, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ручное управление аварийным освещением: только линия 7 smart_panel_01")
    parser.add_argument("--state", choices=("on", "off", "status"), help="Включить, выключить или прочитать состояние")
    parser.add_argument("--address", "--dome-url", default=os.getenv("DOME_URL") or "192.168.8.126:8766")
    parser.add_argument("--team", type=int, default=4)
    parser.add_argument("--keys-archive", type=Path, default=HERE / "Dome_RAG-main.zip")
    parser.add_argument("--timeout", type=float, default=45)
    args = parser.parse_args(argv)
    try:
        url = normalize_address(args.address)
    except ValueError as exc:
        parser.error(str(exc))
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.team < 1:
        parser.error("Время и номер команды должны быть положительными")
    if args.state is None:
        try:
            print("Аварийное освещение — линия 7 умного щитка.\n1 — Включить\n2 — Выключить\n3 — Показать состояние")
            selected = input("Выберите 1, 2 или 3 [3]: ").strip() or "3"
        except (EOFError, KeyboardInterrupt):
            return 130
        if selected not in {"1", "2", "3"}:
            print("Некорректный выбор. Команда не отправлена.", file=sys.stderr)
            return 2
        args.state = {"1": "on", "2": "off", "3": "status"}[selected]
    folder = HERE / "output/emergency_light" / datetime.now(MSK).strftime("%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True, exist_ok=False)
    result = dict(success=False, address=url, expected_team=args.team, requested_state=args.state,
                  command_attempted=False, acknowledged=False, outcome="FAILED",
                  started_at=datetime.now(MSK).isoformat(timespec="seconds"))
    secrets = [os.getenv("DOME_KEY", ""), os.getenv("DEEPSEEK_API_KEY", "")]
    try:
        key = os.getenv("DOME_KEY", "").strip()
        if not key and args.keys_archive.is_file():
            keys = load_team_keys(args.keys_archive, args.team)
            secrets += list(keys.values())
            key = keys.get("dome_key", "")
        if not key:
            key = getpass.getpass(f"Ключ Купола команды {args.team} (ввод скрыт): ").strip()
        secrets.append(key)
        if not key:
            raise ValueError("Ключ Купола не введён")

        async def run():
            await asyncio.wait_for(switch_light(url, key, args.team, args.state, result), args.timeout)

        asyncio.run(run())
    except asyncio.TimeoutError:
        result["error"] = "Истёк таймаут подключения или подтверждения состояния по телеметрии"
    except (KeyboardInterrupt, EOFError):
        result["error"] = "Выполнение остановлено пользователем"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    _save(result, folder, secrets)
    return 0 if result["success"] else 3 if result["outcome"] == "UNKNOWN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
