"""Loopback-only web shell around the RAG runtime.

No equipment or language-model call is made by a route without going through
the runtime. Uploads never imply starting a job. Keys remain in process memory.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit
import webbrowser

from aiohttp import WSMsgType, web

from .control import ControlError, MACHINES, MAX_UPLOAD_BYTES
from .llm import PlannerError

_RUNTIME = web.AppKey("runtime", object)
_TOKEN = web.AppKey("csrf_token", str)
_COOKIE = web.AppKey("session_cookie", str)
_SOCKETS = web.AppKey("websockets", set)
_COOKIE_NAME = "cupol_session"
_STATIC = Path(__file__).resolve().parent / "webui"
_LOG = logging.getLogger(__name__)


def _json(value, *, status=200):
    return web.json_response(value, status=status, dumps=lambda v: json.dumps(v, ensure_ascii=False, allow_nan=False))


def _loopback(value):
    if value == "localhost":
        return True
    try:
        address = ipaddress.ip_address(value)
        if getattr(address, "ipv4_mapped", None) is not None:
            address = address.ipv4_mapped
        return address.is_loopback
    except (TypeError, ValueError):
        return False


def _origin_parts(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        return parsed.scheme, parsed.hostname.casefold(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except (TypeError, ValueError):
        return None


def _check_origin(request):
    origin = request.headers.get("Origin")
    if origin is not None:
        if _origin_parts(origin) != _origin_parts(request.scheme + "://" + request.host):
            raise web.HTTPForbidden(text="Запрос разрешён только из веб-интерфейса этого сервера")


def _has_session(request):
    value = request.cookies.get(_COOKIE_NAME, "")
    return bool(value) and secrets.compare_digest(value, request.app[_COOKIE])


def _safe_error(error):
    value = str(error)[:1200] or type(error).__name__
    for name in ("DOME_KEY", "DEEPSEEK_API_KEY"):
        key = os.getenv(name, "")
        if key:
            value = value.replace(key, "[СКРЫТО]")
    return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[СКРЫТО]", value)


@web.middleware
async def _security(request, handler):
    try:
        host = _origin_parts(request.scheme + "://" + request.host)
        if host is None or not _loopback(host[1]) or not _loopback(request.remote):
            raise web.HTTPForbidden(text="Веб-интерфейс доступен только с этого компьютера")
        _check_origin(request)
        is_api = request.path.startswith("/api/")
        if is_api and request.path != "/api/session" and not _has_session(request):
            raise web.HTTPUnauthorized(text="Откройте веб-интерфейс заново для создания локальной сессии")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            value = request.headers.get("X-Cupol-Token", "")
            if not value or not secrets.compare_digest(value, request.app[_TOKEN]):
                raise web.HTTPForbidden(text="Отсутствует действительный токен веб-сессии")
        response = await handler(request)
    except web.HTTPException as exc:
        response = _json({"error": exc.text or exc.reason}, status=exc.status)
    except PlannerError as exc:
        response = _json({"error": _safe_error(exc)}, status=503)
    except (ControlError, ValueError, TypeError) as exc:
        response = _json({"error": _safe_error(exc)}, status=400)
    except RuntimeError as exc:
        response = _json({"error": _safe_error(exc)}, status=409)
    except Exception as exc:
        # No body, credentials or raw traceback is copied to HTTP/access logs.
        _LOG.error("Web request failed: %s", type(exc).__name__)
        response = _json({"error": "Внутренняя ошибка обработки запроса"}, status=500)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self' ws://127.0.0.1:* ws://localhost:* ws://[::1]:*; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    return response


async def _body(request):
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="Ожидается application/json")
    try:
        value = await request.json()
    except (ValueError, UnicodeError):
        raise ValueError("Некорректный JSON запроса") from None
    if not isinstance(value, dict):
        raise ValueError("Ожидается JSON-объект")
    return value


def _device(value):
    if value not in MACHINES:
        raise ValueError("Выберите 3D-принтер или фрезерный станок")
    return value


async def _session(request):
    response = _json({"token": request.app[_TOKEN]})
    response.set_cookie(_COOKIE_NAME, request.app[_COOKIE], httponly=True, samesite="Strict", path="/")
    return response


async def _state(request):
    return _json(request.app[_RUNTIME].snapshot())


async def _connect(request):
    payload = await _body(request)
    if set(payload) - {"address", "profile", "autonomous", "dome_key", "deepseek_key", "team"}:
        raise ValueError("Неизвестный параметр подключения")
    result = await request.app[_RUNTIME].connect(payload)
    return _json(result or request.app[_RUNTIME].snapshot())


async def _disconnect(request):
    await _body(request)
    result = await request.app[_RUNTIME].disconnect()
    return _json(result or request.app[_RUNTIME].snapshot())


async def _chat(request):
    payload = await _body(request)
    message = payload.get("message")
    message_kind = payload.get("message_kind", "request")
    if (set(payload) - {"message", "message_kind"} or "message" not in payload
            or not isinstance(message, str) or not message.strip() or len(message) > 6000):
        raise ValueError("Введите сообщение длиной от 1 до 6000 символов")
    if not isinstance(message_kind, str) or message_kind not in {"request", "directive"}:
        raise ValueError("Тип сообщения должен быть request или directive")
    return _json(await request.app[_RUNTIME].chat(message.strip(), message_kind=message_kind))


async def _control(request):
    payload = await _body(request)
    command = payload.get("request")
    if (set(payload) != {"request"} or not isinstance(command, dict)
            or set(command) - {"type", "node_id", "action", "value"}
            or not {"type", "node_id", "action"} <= set(command)
            or command.get("type") != "control"
            or not all(isinstance(command.get(field), str) and 0 < len(command[field]) <= 128
                       for field in ("node_id", "action"))):
        raise ValueError("Укажите один запрос control с node_id, action и необязательным value")
    if len(json.dumps(command, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 4096:
        raise ValueError("Прямая команда превышает лимит 4 КиБ; файлы передаются отдельной формой")
    return _json(await request.app[_RUNTIME].manual_control(command))


async def _autonomy(request):
    payload = await _body(request)
    if set(payload) != {"enabled"} or type(payload.get("enabled")) is not bool:
        raise ValueError("Параметр enabled должен быть true или false")
    return _json(await request.app[_RUNTIME].set_autonomy(payload["enabled"]))


async def _configure_autonomy(request):
    payload = await _body(request)
    fields = {"min_airflow_pct", "min_supply_flow_m3_h", "min_cooling_flow_l_min"}
    if set(payload) != fields | {"safety_confirmed"}:
        raise ValueError("Заполните три минимальных потока и подтвердите безопасное состояние")
    if payload.get("safety_confirmed") is not True:
        raise ValueError("Нужно явное подтверждение безопасного состояния и выбранных параметров")
    if (any(type(payload[name]) not in (int, float) or not math.isfinite(payload[name]) or payload[name] <= 0
            for name in fields) or payload["min_airflow_pct"] > 100):
        raise ValueError("Минимальные потоки должны быть положительными числами; вытяжка не выше 100 %")
    return _json(await request.app[_RUNTIME].configure_autonomy(payload))


async def _clear_directives(request):
    await _body(request)
    return _json(await request.app[_RUNTIME].clear_directives())


async def _work_context(request):
    payload = await _body(request)
    if set(payload) != {"required_fume"} or type(payload.get("required_fume")) is not bool:
        raise ValueError("Параметр required_fume должен быть true или false")
    return _json(await request.app[_RUNTIME].set_work_context(payload["required_fume"]))


async def _files(request):
    return _json(await request.app[_RUNTIME].files(_device(request.query.get("node_id"))))


async def _start_job(request):
    payload = await _body(request)
    if set(payload) != {"node_id", "file_name"} or not isinstance(payload.get("file_name"), str):
        raise ValueError("Укажите устройство и имя файла задания")
    return _json(await request.app[_RUNTIME].start_job(_device(payload["node_id"]), payload["file_name"]))


async def _upload(request):
    if request.content_type != "multipart/form-data":
        raise web.HTTPUnsupportedMediaType(text="Ожидается форма с файлом")
    reader = await request.multipart()
    node_id = filename = None
    organizer_prepared = None
    content = None
    count = 0
    while True:
        field = await reader.next()
        if field is None:
            break
        count += 1
        if count > 3:
            raise ValueError("Передавайте один файл, одно устройство и декларацию происхождения файла")
        if field.name == "node_id" and node_id is None and field.filename is None:
            raw = await field.read_chunk(1024)
            if len(raw) > 100 or not field.at_eof():
                raise ValueError("Некорректное имя устройства")
            node_id = raw.decode("utf-8")
        elif field.name == "organizer_prepared" and organizer_prepared is None and field.filename is None:
            raw = await field.read_chunk(1024)
            if not field.at_eof() or raw not in {b"true", b"false"}:
                raise ValueError("Декларация происхождения файла должна иметь значение true или false")
            organizer_prepared = raw == b"true"
        elif field.name == "file" and field.filename and content is None:
            filename = field.filename
            data = bytearray()
            while True:
                chunk = await field.read_chunk(65536)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_UPLOAD_BYTES:
                    raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD_BYTES, actual_size=len(data))
            content = bytes(data)
        else:
            raise ValueError("Неизвестное или повторное поле формы")
    if content is None or filename is None:
        raise ValueError("Файл не выбран")
    if organizer_prepared is not True:
        raise ValueError("Подтвердите, что выбран готовый файл организаторов")
    return _json(await request.app[_RUNTIME].upload(_device(node_id), filename, content,
                                                  organizer_prepared=organizer_prepared))


async def _history(request):
    return _json(request.app[_RUNTIME].history_summary())


async def _stream(request):
    socket = web.WebSocketResponse(heartbeat=20, max_msg_size=1024)
    await socket.prepare(request)
    runtime = request.app[_RUNTIME]
    queue = runtime.subscribe()
    request.app[_SOCKETS].add(socket)

    async def push():
        try:
            await socket.send_json({"type": "state", "data": runtime.snapshot()})
            while True:
                event = await queue.get()
                await socket.send_json(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            await socket.close(code=1011, message="Поток состояния закрыт".encode())

    task = asyncio.create_task(push())
    try:
        async for message in socket:
            if message.type in {WSMsgType.TEXT, WSMsgType.BINARY}:
                await socket.close(code=1008, message=b"Use authenticated HTTP API")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        runtime.unsubscribe(queue)
        request.app[_SOCKETS].discard(socket)
    return socket


async def _index(request):
    return web.FileResponse(_STATIC / "index.html")


async def _static(request):
    name = request.match_info["name"]
    if name not in {"app.js", "style.css"}:
        raise web.HTTPNotFound(text="Файл интерфейса не найден")
    return web.FileResponse(_STATIC / name)


def create_app(runtime=None, **runtime_options):
    if runtime is None:
        from .runtime import DomeRuntime
        runtime = DomeRuntime(**runtime_options)
    app = web.Application(middlewares=[_security], client_max_size=MAX_UPLOAD_BYTES + 65536)
    app[_RUNTIME], app[_TOKEN], app[_COOKIE], app[_SOCKETS] = runtime, secrets.token_urlsafe(32), secrets.token_urlsafe(32), set()

    async def lifecycle(application):
        await runtime.start()
        try:
            yield
        finally:
            for socket in list(application[_SOCKETS]):
                await socket.close(code=1001, message=b"Server shutdown")
            await runtime.close()

    app.cleanup_ctx.append(lifecycle)
    app.add_routes([
        web.get("/", _index), web.get("/static/{name}", _static),
        web.get("/api/session", _session), web.get("/api/state", _state),
        web.get("/api/stream", _stream), web.get("/api/history", _history), web.get("/api/files", _files),
        web.post("/api/connect", _connect), web.post("/api/disconnect", _disconnect),
        web.post("/api/chat", _chat), web.post("/api/control", _control), web.post("/api/upload", _upload),
        web.post("/api/autonomy", _autonomy), web.post("/api/autonomy/config", _configure_autonomy),
        web.post("/api/job/start", _start_job),
        web.post("/api/directives/clear", _clear_directives),
        web.post("/api/work-context", _work_context),
    ])
    return app


def run(options):
    if not _loopback(options.host):
        raise ValueError("Для этой локальной версии используйте --host 127.0.0.1, localhost или ::1")
    if not 1 <= options.port <= 65535:
        raise ValueError("Порт веб-интерфейса должен быть в диапазоне 1..65535")
    app = create_app(config_path=options.config, context_path=options.context,
                     keys_archive=options.keys_archive, profile=options.profile, data_dir=options.data_dir)
    shown_host = f"[{options.host}]" if ":" in options.host else options.host
    address = f"http://{shown_host}:{options.port}"
    if not options.no_open:
        async def open_browser(_):
            asyncio.get_running_loop().call_later(.5, webbrowser.open, address)
        app.on_startup.append(open_browser)
    print(f"Веб-интерфейс RAG-агента: {address}\nДля остановки нажмите Ctrl+C.", flush=True)
    web.run_app(app, host=options.host, port=options.port, access_log=None)
    return 0


def main(argv=None):
    from start_web import build_parser
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
