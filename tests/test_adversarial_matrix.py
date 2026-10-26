"""Seeded contract mutations; synthetic inputs only, no provider or device I/O.

Run directly to write output/adversarial_results.json. Generated cases are
reported separately from unittest methods. The existing command bridge tests
exercise the real transport; this matrix stresses the decoder and its gates.
"""
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import random
import string
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kupol_agent.chat import DeepSeekChat, _parse_chat_response
from kupol_agent.control import ControlError, available_controls, canonical_request, validate_manual
from kupol_agent.llm import DeepSeekPlanner, PlannerError, _strict_json
from kupol_agent.runtime import DomeRuntime
from test_command_bridge import ready_frame


SEED = 20261027
MEASURED = {"seed": SEED, "scope": "synthetic_only", "provider_calls": 0, "equipment_commands": 0,
            "generated_model_mutations": 0, "rejected_model_mutations": 0,
            "accepted_benign_newline_variants": 0, "valid_boundary_cases": 0,
            "autonomy_payload_cases": 0, "rejected_autonomy_payloads": 0, "unexpected": 0,
            "invalid_unicode_cases": 0, "rejected_invalid_unicode_cases": 0,
            "valid_unicode_pairs": 0, "dynamic_catalog_revocations": 0,
            "planner_unicode_cases": 0, "numeric_overflow_cases": 0, "deep_json_cases": 0,
            "mutation_families": {}}
CATALOG = {
    "smart_panel_01": {"controls": ["line_on", "line_off", "reset_protection"],
                       "operator_controls": ["physical_switch"], "service_actions": ["service_restart"]},
    "supply_ventilation_01": {"controls": ["turn_on", "turn_off", "set_speed", "set_mode"]},
    "fume_extraction_01": {"controls": ["turn_on", "turn_off", "physical_switch", "service_restart"],
                           "operator_controls": ["physical_switch"], "service_actions": ["service_restart"]},
    "printer_3d_01": {"controls": ["home", "start_job", "delete_file", "set_speed"]},
    "cnc_01": {"controls": ["home", "start_job"]},
    "equipment_cooling_01": {"controls": ["turn_on", "turn_off", "set_speed"]},
    "water_pump_01": {"controls": ["turn_on", "turn_off", "set_power_limit"]},
    "radio_01": {"controls": ["set_frequency", "set_protocol"]},
    "edge_compute_01": {"controls": ["limit_load"]},
    "solar_inverter_01": {"controls": ["set_fan"]},
    "backup_comms_01": {"controls": ["switch_channel", "set_protocol"]},
}
CONTROLS = available_controls(CATALOG)
SOURCES = {"doc:matrix"}
BASE = {"type": "control", "node_id": "smart_panel_01", "action": "line_on", "value": 7}


def response(body=None, *, raw=None):
    if raw is None:
        raw = json.dumps(body, ensure_ascii=False)
    return {"model": "deepseek-flash", "choices": [{"finish_reason": "stop", "message": {"content": raw}}]}


def envelope(command=None, answer="Проверяем контракт"):
    return {"answer": answer, "commands": [deepcopy(command or BASE)], "sources": ["doc:matrix"]}


def all_ready():
    frame = ready_frame()
    frame["_received_monotonic"] = time.monotonic()
    nodes = frame["data"]["nodes"]
    known = {row["line"] for row in nodes["smart_panel_01"]["lines"]}
    for line in range(1, 9):
        if line not in known:
            nodes["smart_panel_01"]["lines"].append({"line": line, "name": f"Линия {line}", "state": "OFF",
                "protection_state": "ARMED", "alarm": False, "lockout_remaining_s": 0, "lockout_reason": None})
    for row in nodes["smart_panel_01"]["lines"]:
        row.update(alarm=False, lockout_remaining_s=0, lockout_reason=None)
    for node in CATALOG:
        nodes.setdefault(node, {"online": True, "status": "OK", "updated_at": frame["timestamp"], "state": "IDLE"})
    nodes["backup_comms_01"]["active_channel"] = "SATELLITE"
    nodes["printer_3d_01"]["state"] = "PRINTING"
    nodes["printer_3d_01"]["file_name"] = "existing.gcode"
    nodes["printer_3d_01"]["files"].append({"name": "деталь_№1.gcode", "valid": True})
    nodes["cnc_01"]["files"].append({"name": "плата_№2.nc", "valid": True})
    return frame


def mutate_case(family, index, rng):
    nonce = "".join(rng.choices(string.ascii_letters + string.digits, k=12))
    body = envelope(answer=f"Случай {index}: {nonce}")
    command = body["commands"][0]
    intent, raw = True, None
    if family == "unknown_response_fields": body["extra_" + nonce] = index
    elif family == "unknown_command_fields": command["extra_" + nonce] = index
    elif family == "action_in_wire_type": command["type"] = rng.choice(["line_on", "line_off", "CONTROL", "control\n", None, 42, True, []])
    elif family == "unknown_node": command["node_id"] = rng.choice(["unknown_" + nonce, "smart_panel_0１", None, [], True])
    elif family == "unavailable_or_unsafe_action":
        command["action"] = rng.choice(["reset_protection", "reset_interlock", "on", "physical_switch", "service_restart", "unknown_" + nonce])
    elif family == "wrong_line_value_type": command["value"] = rng.choice(["7", True, False, 7.0, None, [], {"line": "7"}])
    elif family == "line_outside_contract": command["value"] = rng.choice([rng.randint(-10000, 0), rng.randint(9, 10000)])
    elif family == "speed_wrong_type":
        command.update(node_id="supply_ventilation_01", action="set_speed", value=rng.choice(["75", True, None, [], {"percent": 75}]))
    elif family == "speed_outside_contract":
        command.update(node_id="supply_ventilation_01", action="set_speed", value=rng.choice([rng.randint(-10000, 9), rng.randint(101, 10000)]))
    elif family == "nonfinite_numbers":
        command.update(node_id="supply_ventilation_01", action="set_speed", value=rng.choice([float("nan"), float("inf"), -float("inf")]))
    elif family == "forged_sources": body["sources"] = ["doc:forged_" + nonce]
    elif family == "duplicate_commands": body["commands"].append(deepcopy(command))
    elif family == "read_only_cannot_control": intent = False
    elif family == "duplicate_json_keys":
        raw = '{"answer":"' + nonce + '","answer":"second","commands":[],"sources":[]}'
    elif family == "invalid_unicode_escape": raw = '{"answer":"' + nonce + '\\u12XY","commands":[],"sources":[]}'
    elif family == "raw_control_characters":
        control = chr(rng.choice([number for number in range(32) if number not in (10, 13)]))
        raw = '{"answer":"' + nonce + control + nonce + '","commands":[],"sources":[]}'
    elif family == "invalid_backslash_newline": raw = '{"answer":"' + nonce + '\\' + '\n' + nonce + '","commands":[],"sources":[]}'
    elif family == "outside_json_text": raw = nonce + json.dumps(body)
    elif family == "wrong_root_type": raw = json.dumps(rng.choice([None, index, [body], nonce]))
    elif family == "missing_mandatory_fields": body.pop(rng.choice(["answer", "commands", "sources"]))
    elif family == "invalid_answer": body["answer"] = rng.choice([None, [], True, "", " " * 7, nonce * 501])
    elif family == "commands_not_array": body["commands"] = rng.choice([None, {}, nonce, True, index])
    elif family == "invalid_sources_shape": body["sources"] = rng.choice([None, {}, nonce, True, ["doc:matrix"] * 9])
    elif family == "parameter_or_filename_injection":
        if index % 2:
            command["value"] = {"line": 7, "node_id": "cnc_01"}
        else:
            command.update(node_id="cnc_01", action="start_job", value=rng.choice(["../" + nonce + ".nc", nonce + "\x00.nc", "C:\\" + nonce + ".nc", "CON.nc"]))
    else: raise AssertionError("Unknown mutation family")
    return response(body, raw=raw), intent


FAMILIES = ("unknown_response_fields", "unknown_command_fields", "action_in_wire_type", "unknown_node",
    "unavailable_or_unsafe_action", "wrong_line_value_type", "line_outside_contract", "speed_wrong_type",
    "speed_outside_contract", "nonfinite_numbers", "forged_sources", "duplicate_commands", "read_only_cannot_control",
    "duplicate_json_keys", "invalid_unicode_escape", "raw_control_characters", "invalid_backslash_newline",
    "outside_json_text", "wrong_root_type", "missing_mandatory_fields", "invalid_answer", "commands_not_array",
    "invalid_sources_shape", "parameter_or_filename_injection")


class AdversarialModelMatrixTests(unittest.TestCase):
    def test_unicode_answers_must_be_utf8_serializable_before_execution(self):
        for codepoint in (0xD800, 0xDBFF, 0xDC00, 0xDFFF):
            for location in ("answer", "filename", "source", "field_name"):
                for index in range(16):
                    body = envelope(answer=f"Перед {index} ЗАМЕНА после")
                    if location == "filename":
                        body = envelope({"type": "control", "node_id": "printer_3d_01", "action": "start_job", "value": f"part_{index}_ЗАМЕНА.gcode"})
                    elif location == "source": body = {**envelope(), "sources": [f"doc:{index}_ЗАМЕНА"]}
                    elif location == "field_name": body = {**envelope(), f"field_{index}_ЗАМЕНА": True}
                    raw = json.dumps(body, ensure_ascii=False).replace("ЗАМЕНА", "\\u" + format(codepoint, "04x"))
                    MEASURED["invalid_unicode_cases"] += 1
                    with self.subTest(codepoint=codepoint, location=location, index=index):
                        # Assert the actual decoder boundary too, so a later
                        # schema rejection does not mask malformed Unicode.
                        with self.assertRaises(ValueError): _strict_json(raw)
                        try:
                            _parse_chat_response(response(raw=raw), True, SOURCES, CONTROLS)
                        except PlannerError:
                            MEASURED["rejected_invalid_unicode_cases"] += 1
                        else:
                            MEASURED["unexpected"] += 1
                            self.fail("A lone surrogate reached a command-bearing UTF-8 reply")
        # A correctly paired supplementary Unicode character is preserved.
        packet = response(envelope(answer="Состояние 😀 подтверждается отдельно"))
        packet["choices"][0]["message"]["content"] = json.dumps(envelope(answer="Состояние 😀 подтверждается отдельно"), ensure_ascii=True)
        answer, commands, *_ = _parse_chat_response(packet, True, SOURCES, CONTROLS)
        self.assertEqual(answer, "Состояние 😀 подтверждается отдельно")
        self.assertEqual(commands, [BASE])
        answer.encode("utf-8")
        MEASURED["valid_unicode_pairs"] += 1

    def test_planner_diagnosis_rejects_lone_unicode_surrogates(self):
        planner = DeepSeekPlanner(api_key="SYNTHETIC_MODEL_ONLY")
        for codepoint in (0xD800, 0xDBFF, 0xDC00, 0xDFFF):
            for index in range(16):
                content = json.dumps({"runbook_id": None, "diagnosis": f"Диагноз {index} ЗАМЕНА",
                                      "evidence_ids": [], "checks": []}, ensure_ascii=False)
                content = content.replace("ЗАМЕНА", "\\u" + format(codepoint, "04x"))
                with self.subTest(codepoint=codepoint, index=index):
                    with patch.object(planner, "_request", return_value=response(raw=content)) as provider:
                        with self.assertRaises(PlannerError): planner.choose({}, [], [])
                    provider.assert_called_once()
                    MEASURED["planner_unicode_cases"] += 1

    def test_numeric_overflow_and_extreme_nesting_fail_closed(self):
        for exponent in (309, 310, 350, 400, 1000, 9999, 10000, 99999):
            for sign in ("", "-"):
                raw = json.dumps(envelope({"type": "control", "node_id": "supply_ventilation_01", "action": "set_speed", "value": "OVERFLOW"}))
                raw = raw.replace('"OVERFLOW"', sign + "1e" + str(exponent))
                with self.subTest(exponent=exponent, sign=sign):
                    with self.assertRaises(ValueError): _strict_json(raw)
                    with self.assertRaises(PlannerError): _parse_chat_response(response(raw=raw), True, SOURCES, CONTROLS)
                    MEASURED["numeric_overflow_cases"] += 1
        for depth in (2000, 5000, 8000):
            raw = '{"answer":' + '[' * depth + '0' + ']' * depth + ',"commands":[],"sources":[]}'
            with self.subTest(depth=depth):
                # Valid JSON need not have an arbitrary depth cap. Decoder
                # depth limits differ between Python versions; acceptance or a
                # normalized ValueError is fine, but raw RecursionError is not.
                try:
                    _strict_json(raw)
                except ValueError:
                    pass
                with self.assertRaises(PlannerError): _parse_chat_response(response(raw=raw), True, SOURCES, CONTROLS)
                MEASURED["deep_json_cases"] += 1
        # Exercise the decoder failure branch using the same generated input,
        # even on runtimes whose C decoder accepts this nesting depth.
        with patch("kupol_agent.llm.json.loads",side_effect=RecursionError("synthetic decoder limit")):
            with self.assertRaises(ValueError): _strict_json(raw)
            with self.assertRaises(PlannerError): _parse_chat_response(response(raw=raw),True,SOURCES,CONTROLS)

    def test_dynamic_catalog_revocation_blocks_previously_valid_command(self):
        command = {"type": "control", "node_id": "fume_extraction_01", "action": "turn_on"}
        for field in ("operator_controls", "service_actions"):
            revoked = deepcopy(CATALOG)
            revoked["fume_extraction_01"][field].append("turn_on")
            with self.subTest(field=field):
                with self.assertRaises(PlannerError):
                    _parse_chat_response(response(envelope(command)), True, SOURCES, available_controls(revoked))
                MEASURED["dynamic_catalog_revocations"] += 1

    def test_seeded_invalid_model_mutations_do_not_form_commands(self):
        rng = random.Random(SEED)
        for family in FAMILIES:
            MEASURED["mutation_families"][family] = 0
            for index in range(100):
                packet, intent = mutate_case(family, index, rng)
                MEASURED["generated_model_mutations"] += 1
                MEASURED["mutation_families"][family] += 1
                with self.subTest(family=family, index=index):
                    try:
                        _parse_chat_response(packet, intent, SOURCES, CONTROLS)
                    except PlannerError:
                        MEASURED["rejected_model_mutations"] += 1
                    else:
                        MEASURED["unexpected"] += 1
                        self.fail("Invalid model mutation formed a command")

    def test_benign_literal_newlines_preserve_exact_command_values(self):
        rng = random.Random(SEED + 1)
        for index in range(120):
            value = rng.randint(1, 8)
            command = {**BASE, "value": value}
            if index % 3 == 1:
                command = {"type": "control", "node_id": "supply_ventilation_01", "action": "set_speed", "value": rng.randint(10, 100)}
            elif index % 3 == 2:
                command = {"type": "control", "node_id": "fume_extraction_01", "action": "turn_on"}
            answer = f'Случай {index}: "проверка"\nВторая строка\r\nЛитерал \\n сохранён.'
            raw = json.dumps(envelope(command, answer), ensure_ascii=False)
            raw = raw.replace('\\n', '\n', 1).replace('\\r', '\r', 1).replace('\\n', '\n', 1)
            with self.subTest(index=index):
                parsed_answer, commands, sources, normalized, newline_count = _parse_chat_response(response(raw=raw), True, SOURCES, CONTROLS)
                self.assertEqual(parsed_answer, answer)
                self.assertEqual(commands, [command])
                if "value" in command:
                    self.assertIs(type(commands[0]["value"]), type(command["value"]))
                self.assertEqual(sources, ["doc:matrix"])
                self.assertEqual(normalized, [])
                self.assertEqual(newline_count, 3)
                MEASURED["accepted_benign_newline_variants"] += 1

    def test_valid_boundaries_remain_available_through_actual_chat(self):
        cases = []
        for action in ("line_on", "line_off"):
            cases += [({**BASE, "action": action, "value": value}, value) for value in (1, 7, 8)]
        cases += [({**BASE, "value": {"line": 7}}, 7)]
        for node, action, values in (
            ("supply_ventilation_01", "set_speed", (10, 75, 100)),
            ("supply_ventilation_01", "set_mode", ("RECIRCULATION", "MIXED", "FRESH_AIR")),
            ("printer_3d_01", "set_speed", (50, 100, 200)),
            ("equipment_cooling_01", "set_speed", (1, 100)),
            ("water_pump_01", "set_power_limit", (20, 100)),
            ("radio_01", "set_frequency", (430, 435.5, 440)),
            ("radio_01", "set_protocol", ("AX25", "FSK", "LORA", "DMR")),
            ("edge_compute_01", "limit_load", (10, 100)),
            ("solar_inverter_01", "set_fan", ("AUTO", "ON", "OFF")),
            ("backup_comms_01", "switch_channel", ("SATELLITE", "LORA", "MESH")),
            ("backup_comms_01", "set_protocol", ("IRIDIUM_SBD", "IP")),
            ("cnc_01", "start_job", ("board.nc", "плата_№2.nc")),
            ("printer_3d_01", "delete_file", ("деталь_№1.gcode",)),
        ):
            cases += [({"type": "control", "node_id": node, "action": action, "value": value}, value) for value in values]
        cases.append(({"type": "control", "node_id": "fume_extraction_01", "action": "turn_on"}, None))
        for index, (proposed, canonical_value) in enumerate(cases):
            with self.subTest(index=index, node=proposed["node_id"], action=proposed["action"]):
                frame = all_ready()
                chat = DeepSeekChat(api_key="SYNTHETIC_MODEL_ONLY")
                with patch.object(chat, "_request", return_value=response(envelope(proposed))) as provider:
                    result = chat.reply("Выполни выбранное действие " + proposed["node_id"], [], frame, CATALOG,
                                        [{"id": "doc:matrix", "text": "Проверка API"}])
                provider.assert_called_once()
                expected = {key: value for key, value in proposed.items() if key != "value"}
                if canonical_value is not None:
                    expected["value"] = canonical_value
                self.assertEqual(result["commands"], [expected])
                self.assertEqual(canonical_request(result["commands"][0], CATALOG), expected)
                self.assertEqual(validate_manual(expected, CATALOG, frame), expected)
                MEASURED["valid_boundary_cases"] += 1
        # Zero cooling and stopping extraction are valid API operations only
        # after production is idle; do not erase this state-dependent boundary.
        idle_cases = [
            {"type": "control", "node_id": "equipment_cooling_01", "action": "set_speed", "value": 0},
            {"type": "control", "node_id": "fume_extraction_01", "action": "turn_off"},
            {"type": "control", "node_id": "printer_3d_01", "action": "start_job", "value": "деталь_№1.gcode"},
            {"type": "control", "node_id": "cnc_01", "action": "home"},
        ]
        for proposed in idle_cases:
            with self.subTest(idle_action=proposed):
                frame = all_ready()
                frame["data"]["nodes"]["printer_3d_01"].update(state="IDLE", file_name=None)
                chat = DeepSeekChat(api_key="SYNTHETIC_MODEL_ONLY")
                with patch.object(chat, "_request", return_value=response(envelope(proposed))) as provider:
                    result = chat.reply("Выполни выбранное действие " + proposed["node_id"], [], frame, CATALOG,
                                        [{"id": "doc:matrix", "text": "Проверка API"}])
                provider.assert_called_once()
                self.assertEqual(result["commands"], [proposed])
                self.assertEqual(validate_manual(result["commands"][0], CATALOG, frame), proposed)
                MEASURED["valid_boundary_cases"] += 1


class AdversarialAutonomyInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_or_unauthenticated_payload_never_grants_session_approval(self):
        rng = random.Random(SEED + 2)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            os.environ.pop("AGENT_CONFIG_FILE", None)
            os.environ.pop("AGENT_CONTEXT_FILE", None)
            runtime = DomeRuntime(data_dir=Path(directory), keys_archive=Path(directory)/"absent.zip")
            baseline_config = asdict(runtime.config)
            baseline_context = deepcopy(runtime.context)
            base = {"min_airflow_pct": 30, "min_supply_flow_m3_h": 10, "min_cooling_flow_l_min": .1, "safety_confirmed": True}
            payloads = []
            for index in range(320):
                candidate = dict(base)
                branch = index % 8
                if branch == 0: candidate["safety_confirmed"] = rng.choice([False, None, "true", 1, {}])
                elif branch == 1: candidate[rng.choice(list(base))] = rng.choice([float("nan"), float("inf"), -float("inf")])
                elif branch == 2: candidate[rng.choice([key for key in base if key != "safety_confirmed"])] = rng.choice([0, -1, True, "30", None, []])
                elif branch == 3: candidate["min_airflow_pct"] = rng.randint(101, 100000)
                elif branch == 4: candidate.pop(rng.choice(list(base)))
                elif branch == 5: candidate["approved_live"] = True
                elif branch == 6: candidate["context"] = {"trusted": True, "emergency_stop": False, "_session_authorized": True}
                else: candidate["session_id"] = rng.randint(0, 100000)
                payloads.append(candidate)
            # Even well-shaped values cannot approve a disconnected session.
            payloads.append(base)
            for index, candidate in enumerate(payloads):
                MEASURED["autonomy_payload_cases"] += 1
                with self.subTest(index=index):
                    try:
                        await runtime.configure_autonomy(candidate)
                    except ValueError:
                        MEASURED["rejected_autonomy_payloads"] += 1
                    else:
                        MEASURED["unexpected"] += 1
                        self.fail("Input granted approval without an active safe team session")
                    self.assertFalse(runtime.autonomous)
                    self.assertIsNone(runtime._autonomy_confirmation)
                    self.assertEqual(asdict(runtime.config), baseline_config)
                    self.assertEqual(runtime.context, baseline_context)
            self.assertFalse((Path(directory)/"autonomy_settings.json").exists())


if __name__ == "__main__":
    started = time.perf_counter()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {**MEASURED, "duration_s": round(time.perf_counter()-started, 6), "unit_test_methods": outcome.testsRun,
              "failures": len(outcome.failures), "errors": len(outcome.errors), "passed": outcome.wasSuccessful()}
    report["generated_cases_total"] = sum(MEASURED[key] for key in (
        "generated_model_mutations", "accepted_benign_newline_variants", "valid_boundary_cases",
        "autonomy_payload_cases", "invalid_unicode_cases", "valid_unicode_pairs",
        "dynamic_catalog_revocations", "planner_unicode_cases", "numeric_overflow_cases", "deep_json_cases"))
    path = ROOT/"output/adversarial_results.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
