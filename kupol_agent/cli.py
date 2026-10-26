"""JSON CLI for local diagnosis, replay, case memory and an explicit LLM probe."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sys
import math
from urllib.parse import urlsplit

from .memory import CaseStore
from .retrieval import DocumentIndex, HybridRetriever

_SENSITIVE = re.compile(r"^(?:key|.*_key|token|.*_token|authorization|password|secret)$", re.I)
_SECRETS = set()
DEFAULT_DOME_URL = "wss://janus-server.a.2na.am:443"


def _safe(value):
    secrets = _SECRETS | {os.getenv(name, "") for name in ("DOME_KEY", "DEEPSEEK_API_KEY")}
    if isinstance(value, dict):
        return {_safe(str(key)): "[REDACTED]" if _SENSITIVE.fullmatch(str(key)) else _safe(child)
                for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe(child) for child in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", value)
    return value


def _write(value):
    print(json.dumps(_safe(value), ensure_ascii=False, allow_nan=False), flush=True)


def _common(parser):
    for name, options in [
        ("--db", {"type": Path}), ("--documents", {"type": Path, "action": "append"}),
        ("--config", {"type": Path}), ("--environment", {}),
        ("--dome-url", {"help": "Адрес monitor; по умолчанию wss://janus-server.a.2na.am:443"}),
        ("--keys-archive", {"type": Path}), ("--team", {"type": int}),
        ("--llm", {"action": "store_true"}),
    ]:
        parser.add_argument(name, default=argparse.SUPPRESS, **options)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description="Локальный RAG-диагност Купола. Вывод — JSON/JSONL.")
    _common(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("analyze", "replay", "demo", "monitor", "memory", "search", "check-deepseek"):
        command = commands.add_parser(name)
        _common(command)
        if name in {"analyze", "replay"}:
            command.add_argument("--input", type=Path, required=True)
        if name == "replay":
            command.add_argument("--limit", type=int, default=100)
        elif name == "memory":
            command.add_argument("--limit", type=int, default=20)
        elif name == "search":
            command.add_argument("--query", required=True)
            command.add_argument("--limit", type=int, default=8)
        elif name == "monitor":
            command.add_argument("--duration", type=float, default=60, help="Секунды; 0 — до Ctrl+C")
            command.add_argument("--execute", action="store_true", help="Только с утверждённым config, --context и --llm")
            command.add_argument("--context", type=Path, help="Явный контекст оператора, не текст из чата/RAG")
    args = parser.parse_args(argv)
    for name, default in {"db": Path("data/cases.sqlite"), "documents": None, "config": None,
                          "environment": "simulation", "keys_archive": None, "team": 4, "llm": False,
                          "dome_url": os.getenv("DOME_URL") or DEFAULT_DOME_URL}.items():
        if not hasattr(args, name):
            setattr(args, name, default)
    if getattr(args, "limit", 1) <= 0:
        parser.error("--limit должен быть положительным")
    if args.team <= 0:
        parser.error("--team должен быть положительным")
    if not args.environment.strip():
        parser.error("--environment не может быть пустым")
    if args.command == "monitor" and (not math.isfinite(args.duration) or args.duration < 0):
        parser.error("--duration должен быть >= 0")
    return args


def _document_paths(args):
    if args.documents is not None:
        return args.documents
    package = Path(__file__).resolve().parent
    repository = package.parent / "tmp/repo/Dome_RAG-main"
    if (package / "knowledge/reference/01_API.md").is_file():
        return [package / "knowledge"]
    return [p for p in (package / "knowledge", repository / "knowledge", repository / "01_API.md") if p.exists()]


def _planner(args):
    if not args.llm and args.command != "check-deepseek":
        return None
    if args.keys_archive is not None:
        from .credentials import load_team_keys
        keys = load_team_keys(args.keys_archive, team=args.team)
        for value in keys.values():
            if isinstance(value, str) and value:
                _SECRETS.add(value)
        api_key = keys.get("deepseek_api_key")
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("В архиве отсутствует ключ DeepSeek выбранной команды")
        os.environ["DEEPSEEK_API_KEY"] = os.getenv("DEEPSEEK_API_KEY") or api_key
    from .llm import DeepSeekPlanner
    return DeepSeekPlanner()


def _unwrap(record):
    if not isinstance(record, dict):
        raise ValueError("Ожидается JSON-объект")
    return record["payload"] if record.get("kind") == "incoming" and isinstance(record.get("payload"), dict) else record


def _result(value, summary):
    result = dict(value) if isinstance(value, dict) else {"result": value}
    result.setdefault("summary", summary)
    return result


async def _run(args):
    # memory/search do not import the engine or create a language-model client.
    store = CaseStore(args.db)
    agent = None
    try:
        if args.command == "memory":
            incidents = store.recent_incidents(args.limit)
            for incident in incidents:
                incident["attempts"] = store.attempts_for_incident(incident["id"])
                for attempt in incident["attempts"]:
                    attempt["effectiveness"] = store.stats(incident["fault_code"], attempt["runbook_id"],
                        attempt["runbook_version"], attempt["environment"])
            _write({"summary": f"Инцидентов в выборке: {len(incidents)}", "incidents": incidents})
            return 0
        index = DocumentIndex(_document_paths(args))
        retriever = HybridRetriever(index, store)
        if args.command == "search":
            hits = retriever.retrieve(args.query, args.environment, args.limit)
            _write({"summary": f"Найдено материалов: {len(hits)}", "environment": args.environment, "results": hits})
            return 0
        planner = _planner(args)
        if args.command == "check-deepseek":
            context = retriever.retrieve("достоверность телеметрии наблюдение freshness", args.environment, 3)
            report = {"situation": "Проверка связи с LLM. Только диагностический ответ, без исполнения.",
                      "evidence": [{"id": "probe:read_only", "value": "No equipment commands authorized"}]}
            choice = await asyncio.to_thread(planner.choose, report,
                [{"runbook_id": "observe_only", "eligible": True, "description": "Только наблюдение"}], context)
            _write({"summary": "Получен структурированный диагностический ответ DeepSeek", "result": choice})
            return 0
        from .engine import Agent, Config, MockTransport, demo_snapshot
        if args.command == "monitor":
            if args.execute:
                from .locking import ExecutionLock
                with ExecutionLock(args.db.with_suffix('.execute.lock')):
                    return await _monitor(args, store, retriever, planner, Agent, Config)
            return await _monitor(args, store, retriever, planner, Agent, Config)
        if args.command == "demo" and (args.environment.casefold() == "live" or args.environment.casefold().startswith("live:")):
            raise ValueError("demo не записывает симуляционные результаты в среду live")
        mode = {"analyze": "observe", "replay": "replay", "demo": "simulate"}[args.command]
        agent = Agent(store, retriever, planner=planner, config=Config.from_json(args.config),
                      environment=args.environment, mode=mode)
        if args.command == "analyze":
            record = _unwrap(json.loads(args.input.read_text(encoding="utf-8-sig")))
            result = await agent.process(record)
            _write(_result(result, "Снимок проанализирован; режим observe"))
        elif args.command == "replay":
            processed = errors = 0
            with args.input.open(encoding="utf-8-sig") as stream:
                for line_number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    if processed >= args.limit:
                        break
                    try:
                        record = _unwrap(json.loads(line))
                    except (ValueError, TypeError):
                        errors += 1
                        _write({"summary": "Некорректная строка пропущена", "line": line_number, "error": "Invalid JSON object"})
                        continue
                    result = await agent.process(record)
                    processed += 1
                    _write(_result(result, f"Replay: запись {processed}"))
            _write({"summary": "Replay завершён; реальные команды не отправлялись", "processed": processed, "invalid_lines": errors})
        else:
            transport = MockTransport(demo_snapshot(fault="fume_stopped"), effect=True)
            _write(_result(await agent.process(transport.snapshot, transport=transport), "Демо: исходный отказ"))
            for tick in range(1, 4):
                snapshot = transport.tick(seconds=4)
                _write(_result(await agent.process(snapshot, transport=transport), f"Демо: проверка {tick}"))
            await agent.shutdown()
            agent = None
            _write({"summary": "Симуляция завершена; результаты изолированы от live", "incidents": store.recent_incidents()})
        return 0
    finally:
        try:
            if agent is not None:
                await agent.shutdown()
        finally:
            store.close()


async def _monitor(args, store, retriever, planner, Agent, Config):
    from .transport import LiveTransport, TransportError
    from .credentials import load_team_keys
    config = Config.from_json(args.config)
    if args.execute and (not config.approved_live or not args.context or not args.llm):
        raise ValueError("--execute требует approved_live=true, --context и --llm; по умолчанию включено только наблюдение")
    context = json.loads(args.context.read_text(encoding="utf-8-sig")) if args.context else {}
    if not isinstance(context, dict):
        raise ValueError("--context должен содержать JSON-объект")
    if args.execute and (context.get("trusted") is not True or context.get("emergency_stop") is not False):
        raise ValueError("Для исполнения контекст должен явно подтверждать trusted=true и emergency_stop=false")
    if args.keys_archive:
        keys = load_team_keys(args.keys_archive, args.team)
        for value in keys.values():
            if value:
                _SECRETS.add(value)
        if keys.get("dome_key"):
            os.environ["DOME_KEY"] = os.getenv("DOME_KEY") or keys["dome_key"]
    key = os.getenv("DOME_KEY", "")
    if not key:
        raise ValueError("Для monitor нужен DOME_KEY либо --keys-archive")
    environment = args.environment
    if environment == "simulation":
        environment = f"station:{urlsplit(args.dome_url).hostname}:team{args.team}"
    if args.execute and environment.casefold().startswith("simulation"):
        raise ValueError("Сетевое исполнение не записывается в simulation")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + args.duration if args.duration else None
    reconnect_delay = 3
    processed = 0
    attempts = 0
    hazard_latched = False
    recovered = store.recover_pending(environment)
    if recovered:
        _write({"summary": "Незавершённые попытки предыдущего запуска переведены в UNKNOWN",
                "recovered_attempts": recovered, "environment": environment})
    while deadline is None or loop.time() < deadline:
        agent = Agent(store, retriever, planner=planner, config=config, environment=environment,
                      mode="execute" if args.execute else "observe")
        session = None

        def log_event(kind, **details):
            nonlocal hazard_latched
            if kind == "incoming":
                payload = details.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "telemetry":
                    return
                data = payload.get("data")
                nodes = data.get("nodes") if isinstance(data, dict) else None
                if not isinstance(nodes, dict):
                    return
                detectors = [nodes.get(name) for name in ("smoke_detector_01", "smoke_detector_02")]
                plc = nodes.get("dome_automation_01")
                interlocks = plc.get("interlocks", {}) if isinstance(plc, dict) else {}
                smoke_il = interlocks.get("IL_SMOKE", {}) if isinstance(interlocks, dict) else {}
                smoke = any(isinstance(node, dict) and (node.get("smoke_detected") is True
                              or node.get("alarm_state") == "ALARM") for node in detectors)
                if smoke or (isinstance(smoke_il, dict) and smoke_il.get("state") == "TRIPPED"):
                    if session is not None:
                        session.allow_control = False
                    if not hazard_latched:
                        hazard_latched = True
                        warning = {"kind": "safety_alert", "reason": "SMOKE_OR_FIRE_INTERLOCK",
                                   "instruction": "Прекратить опасную операцию, предупредить людей и уведомить ответственного по инструктажу",
                                   "api_control_disabled": True}
                        _write(warning)
                        agent.audit("safety_alert", **{k:v for k,v in warning.items() if k != "kind"})
                return
            agent.audit(kind, **details)

        async def one_session():
            nonlocal session, processed, attempts
            attempts += 1
            async with LiveTransport(args.dome_url, key, logger=log_event,
                                     allow_control=args.execute) as transport:
                session = transport
                if hazard_latched:
                    transport.allow_control = False
                _write({"summary": "Сессия Купола подключена", "role": transport.role,
                        "team": transport.team, "environment": environment, "execute": args.execute})
                catalog_time = loop.time()
                async for frame in transport.frames():
                    frame = dict(frame)
                    frame["context"] = dict(context)
                    frame["catalog"] = transport.catalog
                    report = await agent.process(frame, transport=transport)
                    processed += 1
                    _write(_result(report, "Телеметрия обработана"))
                    if loop.time() - catalog_time >= 60 and transport.role == "team":
                        await transport.send({"type": "describe_nodes"})
                        catalog_time = loop.time()

        try:
            if deadline is None:
                await one_session()
            else:
                await asyncio.wait_for(one_session(), max(0.01, deadline - loop.time()))
        except asyncio.TimeoutError:
            break
        except TransportError as exc:
            _write({"summary": "Связь прервана; незавершённые действия не повторяются вслепую",
                    "error": str(exc), "retry_after_s": reconnect_delay})
        finally:
            await agent.shutdown()
        if deadline is not None and loop.time() + reconnect_delay >= deadline:
            break
        await asyncio.sleep(reconnect_delay)
        reconnect_delay = min(30, reconnect_delay * 2)
    _write({"summary": "Мониторинг завершён", "processed_frames": processed,
            "connection_attempts": attempts, "environment": environment, "execute": args.execute})
    return 0 if processed else 3


def main(argv=None):
    args = arguments(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        _write({"summary": "Выполнение остановлено пользователем"})
        return 130
    except Exception as exc:
        _write({"summary": "Выполнение не завершено", "error": str(exc) or type(exc).__name__, "error_type": type(exc).__name__})
        return 2
