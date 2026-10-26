"""One-shot, read-only Dome connection check. No DeepSeek or equipment controls."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import getpass
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

_VENDOR = Path(__file__).resolve().parent / "_vendor"
if (_VENDOR / "websockets/asyncio/client.py").is_file():
    sys.path.insert(0, str(_VENDOR))

from kupol_agent.credentials import load_team_keys
from kupol_agent.transport import LiveTransport, TransportError

HERE = Path(__file__).resolve().parent
MSK = timezone(timedelta(hours=3))
DEFAULT_ADDRESS = "192.168.8.126:8765"
_SENSITIVE = re.compile(r"^(?:key|.*_key|token|.*_token|authorization|password|secret)$", re.I)


def normalize_address(value):
    value = value.strip()
    if "://" not in value:
        value = "ws://" + value
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"ws", "wss"} and parsed.hostname
                 and not parsed.username and not parsed.password and not parsed.fragment
                 and not parsed.query and parsed.port != 0)
        if any(char.isspace() for char in value):
            valid = False
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError("Нужен адрес вида 192.168.8.126:8765 или ws://адрес:порт/путь")
    return value


def sanitize(value, secrets=()):
    if isinstance(value, dict):
        return {sanitize(str(k), secrets): "[СКРЫТО]" if _SENSITIVE.fullmatch(str(k)) else sanitize(v, secrets)
                for k, v in value.items() if k != "_received_monotonic"}
    if isinstance(value, (list, tuple)):
        return [sanitize(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[СКРЫТО]")
        return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[СКРЫТО]", value)
    return value


def _now():
    return datetime.now(MSK).isoformat(timespec="seconds")


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _parameters(nodes, catalog):
    lines = ["ПАРАМЕТРЫ КУПОЛА — последний полученный снимок", ""]

    def visit(value, name):
        if isinstance(value, dict):
            if not value:
                lines.append(f"  {name}: {{}}")
            for field, child in value.items():
                visit(child, f"{name}.{field}" if name else field)
        elif isinstance(value, list):
            if not value:
                lines.append(f"  {name}: []")
            for index, child in enumerate(value):
                visit(child, f"{name}[{index}]")
        else:
            shown = {True: "да", False: "нет", None: "нет данных"}.get(value) if type(value) in (bool, type(None)) else str(value)
            lines.append(f"  {name}: {shown}")

    for node_id, node in sorted(nodes.items()):
        description = catalog.get(node_id, {})
        title = description.get("title") or description.get("name") or description.get("label") or node_id
        lines += [f"{title} [{node_id}]", "-" * 60]
        visit(node, "")
        lines.append("")
    if not nodes:
        lines.append("Телеметрия не получена. Причина приведена в report.txt.")
    return "\n".join(lines) + "\n"


class ProbeTransport(LiveTransport):
    """Retain a useful, non-secret handshake error for the connection report."""
    def __init__(self, *args, report, **kwargs):
        super().__init__(*args, **kwargs)
        self.report = report

    async def _connect(self):
        from websockets.asyncio.client import connect
        self.report["stage"] = "websocket"
        try:
            connection = await connect(self.url, open_timeout=8, close_timeout=2,
                                       max_size=32 * 1024 * 1024, ping_interval=20,
                                       ping_timeout=20, proxy=None)
        except Exception as exc:
            raise TransportError(f"WebSocket: {type(exc).__name__}: {exc}") from None
        self.report["websocket_ok"] = True
        self.report["stage"] = "authorization"
        return connection


async def probe(url, key, team, duration, report, folder, secrets, transport_factory=ProbeTransport):
    address = urlsplit(url)
    report["stage"] = "tcp"
    writer = None
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(
            address.hostname, address.port or (443 if address.scheme == "wss" else 80)), 5)
        report["tcp_ok"] = True
        report["local_address"] = writer.get_extra_info("sockname")[0]
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()

    def log(kind, **fields):
        if kind == "connection" and fields.get("event") == "auth":
            report.update(authorized=True, role=fields.get("role"), team=fields.get("team"), stage="catalog")

    async with transport_factory(url, key, report=report, logger=log, allow_control=False) as transport:
        report.update(authorized=True, role=transport.role, team=transport.team)
        if transport.role != "team" or str(transport.team) != str(team):
            raise TransportError(f"Нет ожидаемых прав команды {team}; роль={transport.role}, команда={transport.team}")
        catalog = sanitize(transport.catalog, secrets)
        report["catalog_nodes"] = len(catalog)
        _write_json(folder / "catalog.json", catalog)
        report["stage"] = "telemetry"
        deadline = time.monotonic() + duration
        iterator = transport.frames().__aiter__()
        with (folder / "telemetry.jsonl").open("w", encoding="utf-8") as stream:
            while time.monotonic() < deadline:
                try:
                    frame = await asyncio.wait_for(anext(iterator), max(.01, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                except StopAsyncIteration:
                    raise TransportError("Сервер закрыл поток телеметрии до конца проверки") from None
                data = frame.get("data") if isinstance(frame, dict) else None
                nodes = data.get("nodes") if isinstance(data, dict) else None
                if not isinstance(nodes, dict) or not nodes:
                    report["invalid_frames"] += 1
                    continue
                cleaned = sanitize(frame, secrets)
                stream.write(json.dumps(cleaned, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                _write_json(folder / "latest_telemetry.json", cleaned)
                report.update(frames=report["frames"] + 1, nodes=len(nodes),
                              online_nodes=sum(isinstance(n, dict) and n.get("online") is True for n in nodes.values()),
                              last_received_at=_now(), server_timestamp=frame.get("timestamp"))
                (folder / "parameters.txt").write_text(_parameters(cleaned["data"]["nodes"], catalog), encoding="utf-8")
                print(f"Получен кадр {report['frames']}: узлов {len(nodes)}, онлайн {report['online_nodes']}", flush=True)
        if not report["frames"]:
            raise TransportError("Авторизация прошла, но за время проверки телеметрия не поступила")
        report["stage"] = "complete"


def finish_report(report, folder, secrets):
    report = sanitize(report, secrets)
    report["finished_at"] = _now()
    report["output_folder"] = str(folder.resolve())
    _write_json(folder / "report.json", report)
    status = "УСПЕШНО" if report["success"] else "НЕУСПЕШНО"
    stage_names = {"credentials": "чтение ключа", "dependency": "зависимости Python", "tcp": "TCP-соединение",
                   "websocket": "WebSocket Upgrade", "authorization": "авторизация", "catalog": "каталог API",
                   "telemetry": "получение телеметрии", "complete": "завершено"}
    lines = [f"ПРОВЕРКА КУПОЛА: {status}", f"Адрес: {report['address']}",
             f"Время (Москва): {report['started_at']} — {report['finished_at']}",
             f"Этап: {stage_names.get(report['stage'], report['stage'])}",
             f"TCP: {'доступен' if report['tcp_ok'] else 'не подтверждён'}",
             f"WebSocket: {'открыт' if report['websocket_ok'] else 'не открыт'}",
             f"Авторизация: {'подтверждена' if report['authorized'] else 'не подтверждена'}",
             f"Роль: {report.get('role', '—')}; команда: {report.get('team', '—')}",
             f"Кадров телеметрии: {report['frames']}; узлов в последнем кадре: {report['nodes']}",
             f"Команд оборудованию отправлено: 0", ""]
    if report.get("local_address"):
        lines.append(f"Локальный IP соединения: {report['local_address']}")
    if report.get("last_received_at"):
        lines.append(f"Последний кадр получен (Москва): {report['last_received_at']}")
    stamp = report.get("server_timestamp")
    if type(stamp) in (int, float) and math.isfinite(stamp):
        try:
            lines.append(f"Время сервера (Москва): {datetime.fromtimestamp(stamp, MSK).isoformat(timespec='seconds')}")
        except (ValueError, OverflowError, OSError):
            pass
    if report.get("error"):
        lines.append(f"Причина: {report['error']}")
    if report["success"]:
        lines += ["Подключение и получение параметров подтверждены.",
                  "Работоспособность отдельных устройств этим тестом не подтверждается."]
    elif report["stage"] == "websocket":
        lines += ["Проверьте адрес/порт сервера и протокол ws или wss.",
                  "Для локального адреса проверьте подключение к сети Купола и маршрут VPN.",
                  "Доступный TCP-порт сам по себе не подтверждает доступность API Купола."]
    elif report["stage"] == "tcp":
        lines.append("Проверьте подключение к локальной сети Купола, IP, порт и маршрут VPN.")
    lines += ["", f"Файлы проверки: {folder.resolve()}",
              "Параметры: parameters.txt; отчёт: report.json"]
    lines.append("Снимок JSON: latest_telemetry.json" if report['frames'] else
                 "Снимок latest_telemetry.json не создан: телеметрия не получена.")
    text = "\n".join(lines) + "\n"
    (folder / "report.txt").write_text(text, encoding="utf-8")
    print("\n" + text, flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Проверка Купола: только чтение телеметрии, без управления и LLM")
    parser.add_argument("--address", "--dome-url", default=os.getenv("DOME_URL") or DEFAULT_ADDRESS)
    parser.add_argument("--duration", type=float, default=12, help="Сколько секунд получать телеметрию после входа")
    parser.add_argument("--timeout", type=float, default=35, help="Общий предельный срок сетевой проверки, секунд")
    parser.add_argument("--team", type=int, default=4)
    parser.add_argument("--keys-archive", type=Path, default=HERE / "Dome_RAG-main.zip")
    parser.add_argument("--out", type=Path, help="Новый каталог результата; по умолчанию output/dome_check/<время>")
    args = parser.parse_args(argv)
    try:
        url = normalize_address(args.address)
    except ValueError as exc:
        parser.error(str(exc))
    if not all(math.isfinite(v) and v > 0 for v in (args.duration, args.timeout)) or args.team < 1:
        parser.error("Время и номер команды должны быть положительными")
    folder = args.out or HERE / "output/dome_check" / datetime.now(MSK).strftime("%Y%m%d-%H%M%S-%f")
    try:
        folder.mkdir(parents=True, exist_ok=False)
    except OSError:
        print("Не удалось создать новый каталог результата. Укажите доступный новый путь через --out.", file=sys.stderr)
        return 2
    report = dict(success=False, address=url, started_at=_now(), stage="credentials", tcp_ok=False,
                  websocket_ok=False, authorized=False, expected_team=args.team,
                  frames=0, nodes=0, invalid_frames=0, equipment_commands_sent=0)
    secrets = [os.getenv("DOME_KEY", ""), os.getenv("DEEPSEEK_API_KEY", "")]
    (folder / "parameters.txt").write_text(_parameters({}, {}), encoding="utf-8")
    (folder / "telemetry.jsonl").touch()
    print(f"Проверка {url}, команда {args.team}. Только чтение. Ожидание не более {args.timeout:g} с.", flush=True)
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
        report["stage"] = "dependency"
        try:
            from websockets.asyncio.client import connect  # noqa: F401
        except ImportError:
            raise ValueError('Нужна зависимость: python -m pip install "websockets>=15,<18"; либо запустите check_dome.cmd') from None

        async def run():
            await asyncio.wait_for(probe(url, key, args.team, args.duration, report, folder, secrets), args.timeout)

        asyncio.run(run())
        report["success"] = True
    except (KeyboardInterrupt, EOFError):
        report["error"] = "Проверка остановлена пользователем"
    except asyncio.TimeoutError:
        report["error"] = f"Истёк общий таймаут {args.timeout:g} с на этапе {report['stage']}"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finish_report(report, folder, secrets)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
