import asyncio
import json
import time
import unittest
from unittest.mock import patch

from kupol_agent.transport import LiveTransport, TransportError


CATALOG = [{"node_id": "fume_extraction_01", "controls": ["turn_on", "turn_off"],
            "control_aliases": {"turn_on": ["on"]}, "operator_controls": ["clean_filter"],
            "service_actions": ["reconnect"], "queries": ["read_extra"]},
           {"node_id": "meeting_01", "queries": ["read_messages"]}]


class FakeSocket:
    END = object()
    def __init__(self, role="team", on_request=None, auth_success=True):
        self.role, self.on_request, self.auth_success = role, on_request, auth_success
        self.incoming, self.sent, self.closed = asyncio.Queue(), [], False

    def __aiter__(self): return self
    async def __anext__(self):
        item = await self.incoming.get()
        if item is self.END: raise StopAsyncIteration
        return item

    def feed(self, message): self.incoming.put_nowait(json.dumps(message))
    def finish(self): self.incoming.put_nowait(self.END)

    async def send(self, raw):
        request = json.loads(raw)
        self.sent.append((time.monotonic(), request))
        if request["type"] == "auth":
            self.feed({"type": "telemetry", "timestamp": 1, "data": {"nodes": {}}})
            self.feed({"type": "auth_response", "success": self.auth_success, "role": self.role, "team": 4,
                       "key": request["key"], "echo": request["key"]})
        elif request["type"] == "describe_nodes":
            self.feed({"type": "describe_nodes_response", "success": True,
                       "request_id": request["request_id"], "nodes": CATALOG})
        elif self.on_request:
            await self.on_request(self, request)
        else:
            self.feed({"type": request["type"] + "_response", "success": True,
                       "request_id": request["request_id"]})

    async def close(self):
        self.closed = True
        self.finish()


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def open(self, role="team", on_request=None, allow_control=False):
        ws = FakeSocket(role=role, on_request=on_request)
        events = []
        t = LiveTransport("ws://example.invalid:8765", "LOCAL_ONLY_KEY", logger=lambda kind, **kw: events.append((kind, kw)),
                          max_rate=5, allow_control=allow_control)
        with patch.object(t, "_connect", return_value=ws):
            await t.__aenter__()
        self.addAsyncCleanup(t.__aexit__, None, None, None)
        return t, ws, events

    async def test_auth_first_interleaved_telemetry_and_catalog(self):
        t, ws, _ = await self.open()
        self.assertEqual([r[1]["type"] for r in ws.sent], ["auth", "describe_nodes"])
        self.assertEqual((t.role, t.team), ("team", 4))
        self.assertIn("turn_on", t.catalog["fume_extraction_01"]["controls"])
        self.assertEqual((await anext(t.frames()))["type"], "telemetry")

    async def test_no_secret_in_logger_payloads(self):
        _, _, events = await self.open()
        self.assertNotIn("LOCAL_ONLY_KEY", json.dumps(events))
        self.assertIn("[REDACTED]", json.dumps(events))

    async def test_control_requires_opt_in_canonical_action_and_catalog(self):
        t, ws, _ = await self.open()
        request = {"type": "control", "node_id": "fume_extraction_01", "action": "turn_on"}
        with self.assertRaises(TransportError): await t.send(request)
        self.assertEqual(len(ws.sent), 2)
        t.allow_control = True
        self.assertTrue((await t.send(request))["success"])
        for action in ("on", "clean_filter", "reconnect", "disable_interlock"):
            with self.assertRaises(TransportError): await t.send({**request, "action": action})

    async def test_guest_only_reads_chat_without_catalog_request(self):
        t, ws, _ = await self.open(role="guest", allow_control=True)
        self.assertEqual([r[1]["type"] for r in ws.sent], ["auth"])
        self.assertEqual(t.catalog, {})
        result = await t.send({"type": "query", "node_id": "meeting_01", "query": "read_messages"})
        self.assertTrue(result["success"])
        for request in ({"type": "describe_nodes"}, {"type": "ping"},
                        {"type": "control", "node_id": "fume_extraction_01", "action": "turn_on"}):
            with self.assertRaises(TransportError): await t.send(request)

    async def test_out_of_order_responses_are_correlated(self):
        waiting = []
        async def respond(ws, request):
            waiting.append(request)
            if len(waiting) == 2:
                ws.feed({"type": "telemetry", "timestamp": 2, "data": {"nodes": None}})
                for req in reversed(waiting):
                    ws.feed({"type": "ping_response", "request_id": req["request_id"], "marker": req["marker"]})
        t, _, _ = await self.open(on_request=respond)
        a, b = await asyncio.gather(t.send({"type": "ping", "marker": "a"}), t.send({"type": "ping", "marker": "b"}))
        self.assertEqual((a["marker"], b["marker"]), ("a", "b"))

    async def test_latest_two_frames_and_drop_are_logged(self):
        t, ws, events = await self.open()
        for i in range(2, 6): ws.feed({"type": "telemetry", "timestamp": i, "data": {"nodes": {}}})
        await asyncio.sleep(0)
        stream = t.frames()
        self.assertEqual([(await anext(stream))["timestamp"], (await anext(stream))["timestamp"]], [4, 5])
        self.assertTrue(any(kind == "transport_queue_drop" for kind, _ in events))

    async def test_disconnect_fails_pending_and_frames(self):
        async def never_reply(ws, request): pass
        t, ws, _ = await self.open(on_request=never_reply)
        pending = asyncio.create_task(t.send({"type": "ping"}))
        await asyncio.sleep(0)
        ws.finish()
        with self.assertRaises(TransportError): await pending
        with self.assertRaises(TransportError): await anext(t.frames())
        self.assertEqual(t._pending, {})

    async def test_revocation_stops_frames_and_control(self):
        t, ws, _ = await self.open(allow_control=True)
        ws.feed({"type": "session_revoked", "reason": "LOCAL_ONLY_KEY"})
        await asyncio.sleep(0)
        self.assertEqual(t.role, "guest")
        with self.assertRaises(TransportError): await anext(t.frames())
        with self.assertRaises(TransportError): await t.send({"type": "control", "node_id": "fume_extraction_01", "action": "turn_on"})

    async def test_timeout_does_not_retry_or_stop_receiver(self):
        async def never_reply(ws, request): pass
        t, ws, _ = await self.open(on_request=never_reply)
        await asyncio.sleep(0.21)  # This case times out after an actual send, not in the limiter.
        t._request_timeout = 0.01
        with self.assertRaises(TransportError): await t.send({"type": "ping"})
        self.assertEqual(sum(r[1]["type"] == "ping" for r in ws.sent), 1)
        ws.feed({"type": "telemetry", "timestamp": 2, "data": {"nodes": {}}})
        await asyncio.sleep(0)
        self.assertFalse(t._reader_task.done())

    async def test_control_guard_requires_literal_true_and_exception_fails_closed(self):
        transport,socket,_=await self.open(allow_control=True)
        request={'type':'control','node_id':'fume_extraction_01','action':'turn_on'}
        for value in (False,None,1,'yes'):
            transport.control_guard=lambda request,value=value:value
            with self.assertRaises(TransportError):
                await transport.send(request)
        def broken(request):
            raise RuntimeError('private implementation detail')
        transport.control_guard=broken
        with self.assertRaises(TransportError) as rejected:
            await transport.send(request)
        self.assertNotIn('private implementation detail',str(rejected.exception))
        self.assertEqual(sum(item['type']=='control' for _,item in socket.sent),0)
        self.assertEqual(transport._pending,{})

    async def test_timeout_while_rate_limited_does_not_send_later(self):
        t, ws, _ = await self.open()
        t._request_timeout = 0.01
        with self.assertRaises(TransportError): await t.send({"type": "ping"})
        await asyncio.sleep(0.21)
        self.assertEqual(sum(r[1]["type"] == "ping" for r in ws.sent), 0)

    async def test_all_sends_share_rate_limiter(self):
        t, ws, _ = await self.open()
        await asyncio.gather(t.send({"type": "ping"}), t.send({"type": "ping"}))
        gaps = [b[0] - a[0] for a, b in zip(ws.sent, ws.sent[1:])]
        self.assertTrue(all(gap >= 0.18 for gap in gaps), gaps)

    async def test_auth_rejection_closes_connection(self):
        ws = FakeSocket(auth_success=False)
        t = LiveTransport("ws://example.invalid", "LOCAL_ONLY_KEY")
        with patch.object(t, "_connect", return_value=ws):
            with self.assertRaises(TransportError): await t.__aenter__()
        self.assertTrue(ws.closed)

    async def test_invalid_json_is_ignored_and_query_permission_is_enforced(self):
        t, ws, events = await self.open()
        ws.incoming.put_nowait('{"type":"telemetry","value":NaN}')
        ws.feed([None, 1])
        await asyncio.sleep(0)
        self.assertFalse(t._reader_task.done())
        self.assertTrue(any(kind == "error" for kind, _ in events))
        with self.assertRaises(TransportError): await t.send({"type": "query", "node_id": "unknown", "query": "read"})
        with self.assertRaises(TransportError): await t.send({"type": []})


if __name__ == "__main__":
    unittest.main(verbosity=2)
