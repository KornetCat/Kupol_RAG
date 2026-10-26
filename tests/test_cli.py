import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from kupol_agent import cli
from kupol_agent.memory import CaseStore


class FakeAgent:
    instances = []

    def __init__(self, store, retriever, planner=None, config=None, environment="simulation", mode="observe"):
        self.mode, self.environment, self.planner = mode, environment, planner
        self.records, self.transports, self.stopped = [], [], False
        self.instances.append(self)

    async def process(self, record, transport=None):
        self.records.append(record)
        self.transports.append(transport)
        return {"mode": self.mode, "timestamp": record.get("timestamp")}

    async def shutdown(self):
        self.stopped = True


class FakeTransport:
    def __init__(self, snapshot, effect=True):
        self.snapshot = copy.deepcopy(snapshot)

    def tick(self, seconds=4):
        self.snapshot["timestamp"] += seconds
        return copy.deepcopy(self.snapshot)


def fake_engine():
    module = types.ModuleType("kupol_agent.engine")
    module.Agent = FakeAgent
    module.Config = types.SimpleNamespace(from_json=lambda path: {"config_path": str(path)})
    module.MockTransport = FakeTransport
    module.demo_snapshot = lambda fault="fume_stopped": {"type": "telemetry", "timestamp": 100, "data": {"nodes": {}}}
    return module


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "cases.sqlite"
        self.docs = self.root / "knowledge"
        self.docs.mkdir()
        (self.docs / "guide.md").write_text("# Вытяжка\nfume_stopped вентиляция и качество телеметрии", encoding="utf-8")
        FakeAgent.instances.clear()

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, arguments):
        output = io.StringIO()
        with patch.dict(sys.modules, {"kupol_agent.engine": fake_engine()}), contextlib.redirect_stdout(output):
            code = cli.main([*arguments, "--db", str(self.db), "--documents", str(self.docs)])
        return code, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_analyze_unwraps_wire_record_and_never_passes_a_transport(self):
        input_file = self.root / "snapshot.json"
        input_file.write_text(json.dumps({"kind": "incoming", "payload": {"type": "telemetry", "timestamp": 101}}), encoding="utf-8")
        code, output = self.run_cli(["analyze", "--input", str(input_file)])
        self.assertEqual(code, 0)
        self.assertEqual(output[0]["timestamp"], 101)
        self.assertIn("summary", output[0])
        agent = FakeAgent.instances[0]
        self.assertEqual(agent.mode, "observe")
        self.assertEqual(agent.transports, [None])
        self.assertTrue(agent.stopped)

    def test_replay_limit_and_invalid_lines_are_visible_without_dome_transport(self):
        source = self.root / "history.jsonl"
        source.write_text('{"timestamp":1}\nnot json\n{"timestamp":2}\n{"timestamp":3}\n', encoding="utf-8")
        code, output = self.run_cli(["replay", "--input", str(source), "--limit", "2"])
        self.assertEqual(code, 0)
        self.assertEqual(output[-1]["processed"], 2)
        self.assertEqual(output[-1]["invalid_lines"], 1)
        self.assertEqual(FakeAgent.instances[0].mode, "replay")
        self.assertEqual(FakeAgent.instances[0].transports, [None, None])

    def test_demo_is_simulated_with_three_fresh_ticks_and_rejects_live(self):
        code, output = self.run_cli(["demo"])
        self.assertEqual(code, 0)
        self.assertEqual([r.get("timestamp") for r in output[:-1]], [100, 104, 108, 112])
        self.assertEqual(FakeAgent.instances[0].mode, "simulate")
        self.assertTrue(all(t is not None for t in FakeAgent.instances[0].transports))
        code, output = self.run_cli(["demo", "--environment", "live"])
        self.assertEqual(code, 2)
        self.assertIn("live", output[-1]["error"])

    def test_memory_and_search_emit_structured_json(self):
        store = CaseStore(self.db)
        store.open_incident("f", "fume_stopped", ["air"], [], "simulation", 100)
        store.close()
        code, output = self.run_cli(["memory"])
        self.assertEqual(code, 0)
        self.assertEqual(len(output[0]["incidents"]), 1)
        code, output = self.run_cli(["search", "--query", "fume_stopped"])
        self.assertEqual(code, 0)
        self.assertEqual(output[0]["results"][0]["kind"], "document")

    def test_keys_archive_only_configures_requested_llm_and_secrets_are_masked(self):
        key = "UNIT_TEST_DEEPSEEK_KEY_NOT_REAL"
        dome_key = "UNIT_TEST_DOME_KEY_NOT_REAL"
        credentials = types.ModuleType("kupol_agent.credentials")
        loaded = []
        def load(path, team=4):
            loaded.append((path, team))
            return {"dome_key": dome_key, "deepseek_api_key": key}
        credentials.load_team_keys = load
        llm = types.ModuleType("kupol_agent.llm")
        class Planner:
            def __init__(self):
                assert os.environ["DEEPSEEK_API_KEY"] == key
            def choose(self, report, candidates, context):
                return {"runbook_id": "observe_only", "diagnosis": key, "evidence_ids": [], "checks": []}
        llm.DeepSeekPlanner = Planner
        with patch.dict(os.environ, {}, clear=True), patch.dict(sys.modules, {"kupol_agent.credentials": credentials, "kupol_agent.llm": llm}):
            code, output = self.run_cli(["check-deepseek", "--keys-archive", "not-opened.zip", "--team", "4"])
            self.assertEqual(code, 0)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(output[0]["result"]["diagnosis"], "[REDACTED]")
            loaded.clear()
            self.run_cli(["search", "--query", "fume_stopped", "--keys-archive", "not-opened.zip"])
            self.assertEqual(loaded, [])

    def test_error_is_json_and_does_not_expose_key(self):
        key = "UNIT_ERROR_KEY_NOT_REAL"
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": key}), patch.object(cli, "DocumentIndex", side_effect=RuntimeError(key)):
            code, output = self.run_cli(["search", "--query", "x"])
        self.assertEqual(code, 2)
        self.assertEqual(output[-1]["error"], "[REDACTED]")

    def test_common_flags_work_before_and_after_subcommand(self):
        args = cli.arguments(["--environment", "simulation:isolated", "memory", "--db", str(self.db)])
        self.assertEqual(args.environment, "simulation:isolated")
        self.assertEqual(args.db, self.db)

    def test_attempt_lookup_preserves_state_and_actions_for_restart(self):
        store = CaseStore(self.db)
        try:
            incident = store.open_incident("fingerprint", "FUME_FAILURE", ["air"], [], "simulation", 100)
            attempt = store.begin_attempt(incident, "restore", "2", "simulation", "simulate",
                                           [{"action": "turn_on", "node_id": "fume_extraction_01"}], 100)
            item = store.attempts_for_incident(incident)[0]
            self.assertEqual((item["id"], item["state"], item["version"]), (attempt, "PENDING", "2"))
            self.assertEqual(item["actions"][0]["node_id"], "fume_extraction_01")
            store.recover_pending("simulation")
            self.assertEqual(store.attempts_for_incident(incident)[0]["state"], "UNKNOWN")
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
