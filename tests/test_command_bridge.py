"""Model decision -> reviewed control -> real serialized WebSocket contract.

Only the model completion and peer socket are simulated. The production chat,
command validators, transport reader/serializer/correlation and state predicate
run unchanged. No provider or equipment network connection is opened.
"""
import asyncio
from copy import deepcopy
import json
import time
import unittest
from unittest.mock import patch

from kupol_agent.chat import DeepSeekChat
from kupol_agent.control import ControlError, canonical_request, validate_manual, verify_effect
from kupol_agent.engine import demo_snapshot
from kupol_agent.llm import PlannerError
from kupol_agent.transport import LiveTransport
from test_transport import FakeSocket


CATALOG = [
    {"node_id": "smart_panel_01", "controls": ["line_on", "line_off", "reset_protection"]},
    {"node_id": "supply_ventilation_01", "controls": ["set_speed", "set_mode"]},
    {"node_id": "fume_extraction_01", "controls": ["turn_on", "turn_off"]},
    {"node_id": "printer_3d_01", "controls": ["start_job"]},
    {"node_id": "cnc_01", "controls": ["start_job"]},
]


def ready_frame(*, lighting="OFF"):
    value = demo_snapshot("healthy")
    nodes = value["data"]["nodes"]
    nodes["smart_panel_01"]["lines"] += [
        {"line": 3, "name": "Питание принтера", "state": "ON", "protection_state": "ARMED", "alarm": False},
        {"line": 4, "name": "Питание фрезера", "state": "ON", "protection_state": "ARMED", "alarm": False},
        {"line": 7, "name": "Аварийное освещение", "state": lighting, "protection_state": "ARMED",
         "alarm": False, "lockout_remaining_s": 0, "lockout_reason": None},
    ]
    for identity, name in (("printer_3d_01", "part.gcode"), ("cnc_01", "board.nc")):
        nodes[identity].update(homed=True, bed_clear=True, alarm=False, error_code=None,
            lockout_remaining_s=0, lockout_reason=None, files=[{"name": name, "valid": True}], file_name=None)
    nodes["fume_extraction_01"]["enabled"] = False
    nodes["supply_ventilation_01"]["mode"] = "RECIRCULATION"
    return value


def next_frame(before, mutate):
    after = deepcopy(before)
    after.pop("_received_monotonic", None)
    after["timestamp"] += 4
    for node in after["data"]["nodes"].values():
        node["updated_at"] = after["timestamp"]
    mutate(after["data"]["nodes"])
    return after


def completion(command):
    return {"model": "deepseek-flash", "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({"answer": "Предлагаю выбранное действие; результат проверяется отдельно.",
                               "commands": [command], "sources": ["doc:bridge"]}, ensure_ascii=False)}}]}


class CommandBridgeSocket(FakeSocket):
    """A protocol peer which does not derive its state from the tested command."""
    def __init__(self, after):
        super().__init__()
        self.after = after
        self.control_json = []

    async def send(self, raw):
        request = json.loads(raw)
        if request["type"] == "auth":
            return await super().send(raw)
        self.sent.append((time.monotonic(), request))
        if request["type"] == "describe_nodes":
            self.feed({"type": "describe_nodes_response", "success": True,
                       "request_id": request["request_id"], "nodes": deepcopy(CATALOG)})
        elif request["type"] == "control":
            self.control_json.append(raw)
            self.feed({"type": "control_response", "success": False,
                       "request_id": "unrelated-ack", "accepted_action": "WRONG"})
            self.feed({"type": "control_response", "success": True,
                       "request_id": request["request_id"], "accepted_action": request["action"]})
            self.feed(deepcopy(self.after))
        else:
            raise AssertionError("Unexpected request in the command bridge")


class CommandBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def open_peer(self, before, after):
        peer = CommandBridgeSocket(after)
        transport = LiveTransport("ws://command-bridge.invalid:8765", "LOCAL_BRIDGE_ONLY_KEY",
                                  max_rate=5, allow_control=True)
        with patch.object(transport, "_connect", return_value=peer):
            await transport.__aenter__()
        self.addAsyncCleanup(transport.__aexit__, None, None, None)
        peer.feed(deepcopy(before))
        frames = transport.frames()
        while True:
            received = await asyncio.wait_for(anext(frames), 1)
            if received.get("timestamp") == before["timestamp"]:
                break
        return transport, peer, frames, received

    async def bridge(self, message, proposed, expected, mutate, *, lighting="OFF"):
        initial = ready_frame(lighting=lighting)
        transport, peer, frames, before = await self.open_peer(initial, next_frame(initial, mutate))
        chat = DeepSeekChat(api_key="LOCAL_MODEL_FIXTURE_ONLY")
        with patch.object(chat, "_request", return_value=completion(proposed)) as model:
            result = chat.reply(message, [], before, transport.catalog,
                                [{"id": "doc:bridge", "source": "bridge.md", "text": "Требуются права и новая телеметрия."}])
        model.assert_called_once()
        self.assertEqual(result["commands"], [expected])
        canonical = canonical_request(result["commands"][0], transport.catalog)
        validated = validate_manual(canonical, transport.catalog, before)
        response = await transport.send(validated)
        self.assertEqual(len(peer.control_json), 1)
        wire = json.loads(peer.control_json[0])
        self.assertEqual(set(wire), set(expected) | {"request_id"})
        self.assertEqual({key: value for key, value in wire.items() if key != "request_id"}, expected)
        if "value" in expected:
            self.assertIs(type(wire["value"]), type(expected["value"]))
        self.assertIsInstance(wire["request_id"], str)
        self.assertNotEqual(wire["request_id"], next(request["request_id"] for _, request in peer.sent
                                                  if request["type"] == "describe_nodes"))
        self.assertEqual(response["request_id"], wire["request_id"])
        self.assertEqual(response["accepted_action"], expected["action"])
        self.assertTrue(response["success"])
        after = await asyncio.wait_for(anext(frames), 1)
        self.assertFalse(verify_effect(validated, before, before))
        self.assertTrue(verify_effect(validated, before, after))
        self.assertGreater(after["timestamp"], before["timestamp"])
        self.assertGreater(after["_received_monotonic"], before["_received_monotonic"])
        # Auth, describe_nodes and control share the unchanged production limiter.
        self.assertTrue(all(later[0] - earlier[0] >= .18 for earlier, later in zip(peer.sent, peer.sent[1:])))

    async def test_lighting_on_preserves_line_number_on_wire(self):
        command = {"type": "control", "node_id": "smart_panel_01", "action": "line_on", "value": 7}
        await self.bridge("Включи аварийное освещение smart_panel_01", command, command,
            lambda nodes: next(line for line in nodes["smart_panel_01"]["lines"] if line["line"] == 7).update(state="ON"))

    async def test_lighting_off_preserves_line_number_on_wire(self):
        command = {"type": "control", "node_id": "smart_panel_01", "action": "line_off", "value": 7}
        await self.bridge("Выключи аварийное освещение smart_panel_01", command, command,
            lambda nodes: next(line for line in nodes["smart_panel_01"]["lines"] if line["line"] == 7).update(state="OFF"), lighting="ON")

    async def test_nested_line_value_becomes_canonical_integer_on_wire(self):
        expected = {"type": "control", "node_id": "smart_panel_01", "action": "line_on", "value": 7}
        await self.bridge("Включи аварийное освещение smart_panel_01", {**expected, "value": {"line": 7}}, expected,
            lambda nodes: next(line for line in nodes["smart_panel_01"]["lines"] if line["line"] == 7).update(state="ON"))

    async def test_ventilation_speed_stays_numeric(self):
        command = {"type": "control", "node_id": "supply_ventilation_01", "action": "set_speed", "value": 75}
        await self.bridge("Установи скорость supply_ventilation_01 на 75", command, command,
                          lambda nodes: nodes["supply_ventilation_01"].update(speed_pct=75))

    async def test_ventilation_mode_stays_string(self):
        command = {"type": "control", "node_id": "supply_ventilation_01", "action": "set_mode", "value": "MIXED"}
        await self.bridge("Установи режим supply_ventilation_01 MIXED", command, command,
                          lambda nodes: nodes["supply_ventilation_01"].update(mode="MIXED"))

    async def test_fume_enable_does_not_gain_value(self):
        command = {"type": "control", "node_id": "fume_extraction_01", "action": "turn_on"}
        await self.bridge("Включи вытяжку fume_extraction_01", command, command,
                          lambda nodes: nodes["fume_extraction_01"].update(enabled=True))

    async def test_printer_job_preserves_existing_filename(self):
        command = {"type": "control", "node_id": "printer_3d_01", "action": "start_job", "value": "part.gcode"}
        await self.bridge("Запусти printer_3d_01 файл part.gcode", command, command,
                          lambda nodes: nodes["printer_3d_01"].update(state="PRINTING", file_name="part.gcode"))

    async def test_cnc_job_preserves_existing_filename(self):
        command = {"type": "control", "node_id": "cnc_01", "action": "start_job", "value": "board.nc"}
        await self.bridge("Запусти cnc_01 файл board.nc", command, command,
                          lambda nodes: nodes["cnc_01"].update(state="RUNNING", file_name="board.nc"))

    async def test_invalid_or_unauthorized_model_decisions_never_reach_control_wire(self):
        initial = ready_frame()
        transport, peer, _, before = await self.open_peer(initial, next_frame(initial, lambda nodes: None))
        chat = DeepSeekChat(api_key="LOCAL_MODEL_FIXTURE_ONLY")
        base = {"type": "control", "node_id": "smart_panel_01", "action": "line_on", "value": 7}
        decisions = [
            ("Включи smart_panel_01", "request", {**base, "type": "line_on"}),
            ("Включи smart_panel_01", "request", {**base, "action": "reset_protection"}),
            ("Включи smart_panel_01", "request", {**base, "action": "on"}),
            ("Включи smart_panel_01", "request", {**base, "value": "7"}),
            ("Включи smart_panel_01", "request", {**base, "value": True}),
            ("Включи smart_panel_01", "request", {**base, "node_id": "unknown_node"}),
            ("Включи smart_panel_01", "request", {**base, "unexpected": "argument"}),
            ("Установи скорость supply_ventilation_01", "request", {"type": "control", "node_id": "supply_ventilation_01", "action": "set_speed", "value": "75"}),
            ("Включи fume_extraction_01", "request", {"type": "control", "node_id": "fume_extraction_01", "action": "turn_on", "value": 1}),
            ("Почему освещение выключено?", "request", base),
            ("Включи аварийное освещение", "directive", base),
        ]
        for message, kind, proposed in decisions:
            with self.subTest(message=message, kind=kind, proposed=proposed):
                with patch.object(chat, "_request", return_value=completion(proposed)) as model:
                    with self.assertRaises((PlannerError, ControlError)):
                        result = chat.reply(message, [], before, transport.catalog, [{"id": "doc:bridge", "text": "API"}], message_kind=kind)
                        request = validate_manual(canonical_request(result["commands"][0], transport.catalog), transport.catalog, before)
                        await transport.send(request)
                model.assert_called_once()
                self.assertEqual(peer.control_json, [])

    async def test_current_telemetry_gate_blocks_otherwise_valid_model_command_before_wire(self):
        initial = ready_frame()
        transport, peer, _, before = await self.open_peer(initial, next_frame(initial, lambda nodes: None))
        chat = DeepSeekChat(api_key="LOCAL_MODEL_FIXTURE_ONLY")
        proposed = {"type": "control", "node_id": "smart_panel_01", "action": "line_on", "value": 7}
        before["data"]["nodes"]["smart_panel_01"]["lines"][-1]["protection_state"] = "TRIPPED"
        with patch.object(chat, "_request", return_value=completion(proposed)) as model:
            result = chat.reply("Включи аварийное освещение smart_panel_01", [], before, transport.catalog,
                                [{"id": "doc:bridge", "text": "Не обходить защиту"}])
        self.assertEqual(result["commands"], [proposed])
        with self.assertRaises(ControlError):
            request = validate_manual(canonical_request(result["commands"][0], transport.catalog), transport.catalog, before)
            await transport.send(request)
        model.assert_called_once()
        self.assertEqual(peer.control_json, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
