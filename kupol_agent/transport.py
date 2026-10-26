"""One WebSocket reader, correlated requests, no automatic command replay.

Control requires explicit opt-in and a canonical action in the current catalog.
An ACK is only an API response; repair verification belongs to the agent engine.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from urllib.parse import urlsplit


class TransportError(Exception):
    pass


_SENSITIVE = re.compile(r"^(?:key|.*_key|token|.*_token|authorization|password|secret)$", re.I)
_END = object()


def _json_constant(_):
    raise ValueError("Invalid JSON constant")


class LiveTransport:
    def __init__(self, url, key, logger=None, max_rate=4, allow_control=False):
        try:
            address = urlsplit(url)
            valid = address.scheme in {"ws", "wss"} and address.hostname and not address.username and not address.password
            _ = address.port
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise TransportError("Invalid Dome WebSocket address")
        if not isinstance(key, str) or not key.strip() or "\n" in key or "\r" in key:
            raise TransportError("A Dome team key is required")
        if type(max_rate) not in (int, float) or not math.isfinite(max_rate) or not 0 < max_rate <= 5:
            raise TransportError("max_rate must be > 0 and <= 5")
        self.url, self._key, self.logger = url, key, logger
        self.allow_control, self.role, self.team, self.catalog = bool(allow_control), "unauthed", None, {}
        self._gap, self._last_send = 1 / max_rate, None
        self._send_lock = asyncio.Lock()
        self._frames = asyncio.Queue(maxsize=2)
        self._pending, self._next_id = {}, 0
        self._ws = self._reader_task = self._auth = None
        self._failure, self._closing = None, False
        self._request_timeout = 10.0
        self._entered = False
        self.latest_frame = None
        self.control_guard = None

    def _safe(self, value):
        if isinstance(value, dict):
            return {str(k): "[REDACTED]" if _SENSITIVE.fullmatch(str(k)) else self._safe(v)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._safe(v) for v in value]
        if isinstance(value, str):
            return value.replace(self._key, "[REDACTED]")
        return value

    def _emit(self, kind, **fields):
        if self.logger is not None:
            try:
                self.logger(kind, **self._safe(fields))
            except Exception:
                raise TransportError("Transport logger failed") from None

    async def _connect(self):
        try:
            from websockets.asyncio.client import connect
        except ImportError:
            raise TransportError("Install websockets>=15,<18 to connect to the Dome") from None
        try:
            return await connect(self.url, max_size=32 * 1024 * 1024, open_timeout=10,
                                 close_timeout=5, ping_interval=20, ping_timeout=20, proxy=None)
        except Exception:
            raise TransportError("Dome WebSocket opening handshake failed") from None

    async def __aenter__(self):
        if self._entered:
            raise TransportError("Create a new transport for each Dome session")
        self._entered = True
        self._auth = asyncio.get_running_loop().create_future()
        try:
            self._ws = await self._connect()
            self._reader_task = asyncio.create_task(self._reader())
            await self._send_raw({"type": "auth", "key": self._key}, auth=True)
            response = await asyncio.wait_for(self._auth, 10)
            if response.get("success") is not True or response.get("role") not in {"team", "guest"}:
                raise TransportError("Dome authorization rejected")
            self.role, self.team = response["role"], response.get("team")
            self._emit("connection", event="auth", role=self.role, team=self.team)
            if self.role == "team":
                response = await self.send({"type": "describe_nodes"})
                if response.get("success") is not True or not isinstance(response.get("nodes"), list):
                    raise TransportError("Dome catalog unavailable")
            return self
        except asyncio.CancelledError:
            await self.__aexit__(None, None, None)
            raise
        except Exception as exc:
            await self.__aexit__(None, None, None)
            if isinstance(exc, TransportError):
                raise
            raise TransportError("Dome authorization timed out or connection closed") from None

    async def __aexit__(self, exc_type, exc, tb):
        self._closing = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        self._terminate()
        if self._auth is not None and self._auth.done() and not self._auth.cancelled():
            self._auth.exception()
        self.catalog = {}
        self.role = "closed"

    def _terminate(self, failure=None):
        if failure is not None and self._failure is None:
            self._failure = failure
        error = self._failure or TransportError("Dome connection closed")
        if self._auth is not None and not self._auth.done():
            self._auth.set_exception(error)
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        while not self._frames.empty():
            self._frames.get_nowait()
        self._frames.put_nowait(_END)

    @staticmethod
    def _catalog(nodes):
        result = {}
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("node_id"), str):
                continue
            item = dict(node)
            for kind in ("controls", "operator_controls", "queries", "service_actions"):
                values = item.get(kind)
                item[kind] = [v for v in values if isinstance(v, str)] if isinstance(values, list) else []
            result[item["node_id"]] = item
        return result

    def _validate(self, request):
        if (not isinstance(request, dict) or not isinstance(request.get("type"), str)
                or request["type"] not in {"describe_nodes", "query", "ping", "control"}):
            raise TransportError("Unsupported request type")
        if self._failure is not None or self.role not in {"team", "guest"}:
            raise TransportError("Dome session is not active")
        kind, node_id = request["type"], request.get("node_id")
        query = request.get("query")
        if self.role == "guest":
            if kind == "query" and node_id == "meeting_01" and query == "read_messages":
                return
            raise TransportError("Guest access is limited to reading chat")
        node = self.catalog.get(node_id, {}) if isinstance(node_id, str) else {}
        if kind == "query" and query not in node.get("queries", []):
            raise TransportError("Query is not in the current Dome catalog")
        if kind == "control":
            action = request.get("action")
            if not self.allow_control or not isinstance(action, str) or action not in node.get("controls", []):
                raise TransportError("Control is not enabled or canonical action is not advertised")
            if action in node.get("operator_controls", []) or action in node.get("service_actions", []):
                raise TransportError("Physical and service actions cannot be sent as controls")
            if self.control_guard is not None:
                try:
                    permitted = self.control_guard(request)
                except Exception:
                    raise TransportError("Control pre-send validation failed") from None
                if permitted is not True:
                    raise TransportError("Control pre-send validation rejected the command")

    async def _send_raw(self, request, auth=False):
        async with self._send_lock:
            if self._last_send is not None:
                await asyncio.sleep(max(0, self._last_send + self._gap - time.monotonic()))
            if not auth:
                self._validate(request)  # Recheck after waiting: role may have been revoked.
            self._last_send = time.monotonic()
            try:
                raw = json.dumps(request, ensure_ascii=False, allow_nan=False)
                await self._ws.send(raw)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise TransportError("Dome send failed; outcome is unknown, no automatic retry") from None

    async def send(self, request) -> dict:
        self._validate(request)
        self._next_id += 1
        request = {**request, "request_id": str(self._next_id)}
        future = asyncio.get_running_loop().create_future()
        self._pending[request["request_id"]] = future
        try:
            self._emit("transport_request", request=request)
            async with asyncio.timeout(self._request_timeout):
                await self._send_raw(request)
                response = await future
            if request["type"] == "describe_nodes" and response.get("success") is True and isinstance(response.get("nodes"), list):
                self.catalog = self._catalog(response["nodes"])
            return response
        except asyncio.CancelledError:
            raise
        except TransportError:
            raise
        except Exception:
            raise TransportError("Dome response timed out or connection closed; no automatic retry") from None
        finally:
            self._pending.pop(request["request_id"], None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def _reader(self):
        try:
            async for raw in self._ws:
                try:
                    message = json.loads(raw, parse_constant=_json_constant)
                except (ValueError, TypeError, UnicodeError):
                    self._emit("error", where="transport_reader", error="Invalid JSON received")
                    continue
                self._emit("incoming", payload=message)
                if not isinstance(message, dict):
                    continue
                kind = message.get("type")
                if kind == "auth_response" and self._auth is not None and not self._auth.done():
                    self._auth.set_result(message)
                elif kind == "session_revoked":
                    self.role, self.catalog = "guest", {}
                    self._emit("connection", event="revoked", role="guest")
                    self._terminate(TransportError("Dome session revoked"))
                    return
                elif kind == "telemetry":
                    message = {**message, "_received_monotonic": time.monotonic()}
                    self.latest_frame = message
                    if self._frames.full():
                        self._frames.get_nowait()
                        self._emit("transport_queue_drop", dropped=1)
                    self._frames.put_nowait(message)
                rid = message.get("request_id")
                future = self._pending.get(str(rid)) if type(rid) in (str, int) else None
                if future is not None and not future.done():
                    future.set_result(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._closing:
                self._terminate(TransportError("Dome receive failed"))
        finally:
            if not self._closing and self._failure is None:
                self.role, self.catalog = "disconnected", {}
                self._terminate(TransportError("Dome connection closed"))

    async def frames(self):
        while True:
            if self._failure is not None:
                raise self._failure
            frame = await self._frames.get()
            if frame is _END:
                if self._failure is not None:
                    raise self._failure
                return
            yield frame
