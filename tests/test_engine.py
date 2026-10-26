import copy
import hashlib
from pathlib import Path
import unittest

from kupol_agent.engine import Agent, Config, MockTransport, demo_snapshot, diagnose
from kupol_agent.memory import CaseStore
from kupol_agent.retrieval import DocumentIndex, HybridRetriever


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = CaseStore(":memory:")
        self.retriever = HybridRetriever(DocumentIndex([Path(__file__).parents[1] / "kupol_agent/knowledge"]), self.store)
        self.agents = []

    async def asyncTearDown(self):
        for agent in self.agents:
            await agent.shutdown()
        self.store.close()

    def agent(self, **kwargs):
        agent = Agent(self.store, self.retriever, **kwargs)
        self.agents.append(agent)
        return agent

    async def test_healthy_discharge_and_auto_fan_off_are_not_faults(self):
        result = await self.agent().process(demo_snapshot("healthy"))
        self.assertEqual(result["faults"], [])

    async def test_millisecond_rounding_does_not_make_healthy_nodes_stale(self):
        from kupol_agent.engine import quality
        frame = demo_snapshot("healthy")
        for node in frame["data"]["nodes"].values():
            node["updated_at"] = frame["timestamp"] + .0003
        result = await self.agent().process(frame)
        self.assertEqual(result["faults"], [])
        node = frame["data"]["nodes"]["climate_sensor_01"]
        node["updated_at"] = frame["timestamp"] + .1
        self.assertEqual(quality(node, frame["timestamp"], Config()), "STALE_OR_INVALID_TIME")

    async def test_transient_model_error_is_retried_after_backoff(self):
        import time
        class Unavailable:
            calls = 0
            def choose(self, *args):
                self.calls += 1
                raise TimeoutError('temporary test failure')
        planner = Unavailable()
        agent = self.agent(planner=planner)
        transport = MockTransport(demo_snapshot())
        await agent.process(transport.snapshot)
        await agent.process(transport.tick(4))
        self.assertEqual(planner.calls, 1)
        for incident in agent._plan_cached_at:
            agent._plan_cached_at[incident] = time.monotonic() - 16
        await agent.process(transport.tick(4))
        self.assertEqual(planner.calls, 2)

    async def test_line_one_loss_is_not_a_motor_failure(self):
        mock = MockTransport(demo_snapshot("fume_power"))
        result = await self.agent(mode="simulate").process(mock.snapshot, mock)
        codes = {f["code"] for f in result["faults"]}
        self.assertIn("FUME_POWER_LOSS", codes)
        self.assertNotIn("FUME_MECHANICAL", codes)
        self.assertEqual(mock.sent, [])

    async def test_delayed_co2_sources_do_not_vote_for_false_alarm(self):
        frame = demo_snapshot("healthy")
        frame["data"]["nodes"]["climate_sensor_01"]["co2_ppm"] = 3000
        result = await self.agent().process(frame)
        fault = next(f for f in result["faults"] if f["code"].startswith("CO2"))
        self.assertEqual(fault["code"], "CO2_UNCONFIRMED")
        self.assertEqual(fault["selected_runbook"], "check_co2_sources")
        self.assertEqual(fault["proposed_actions"], [])

    async def test_two_interfaces_of_one_origin_are_not_independent(self):
        frame = demo_snapshot("co2")
        frame["context"]["source_origins"] = {n: "same_physical_sensor" for n in ("climate_sensor_01", "climate_sensor_02", "climate_sensor_03")}
        result = await self.agent().process(frame)
        self.assertIn("CO2_UNCONFIRMED", {f["code"] for f in result["faults"]})

    async def test_verified_success_requires_distinct_frames_and_eight_seconds(self):
        mock = MockTransport(demo_snapshot())
        agent = self.agent(mode="simulate")
        first = await agent.process(mock.snapshot, mock)
        self.assertEqual(first["outcomes"][0]["outcome"], "PENDING")
        for _ in range(2):
            result = await agent.process(mock.tick(4), mock)
            self.assertFalse(any(v["outcome"] == "VERIFIED_SUCCESS" for v in result["outcomes"]))
        result = await agent.process(mock.tick(4), mock)
        self.assertEqual(result["outcomes"][0]["outcome"], "VERIFIED_SUCCESS")
        self.assertEqual(len(mock.sent), 1)
        stats = self.store.stats("FUME_STOPPED", "start_fume", "1.0", "simulation")
        self.assertEqual(stats["successes"], 1)

    async def test_ack_without_effect_fails_and_does_not_resend(self):
        mock = MockTransport(demo_snapshot(), effect=False)
        agent = self.agent(mode="simulate", config=Config(verification_timeout_s=12))
        await agent.process(mock.snapshot, mock)
        for _ in range(3):
            result = await agent.process(mock.tick(4), mock)
        self.assertEqual(result["outcomes"][0]["outcome"], "FAILED")
        await agent.process(mock.tick(4), mock)
        self.assertEqual(len(mock.sent), 1)
        self.assertEqual(self.store.stats("FUME_STOPPED", "start_fume", "1.0", "simulation")["successes"], 0)

    async def test_duplicate_frame_cannot_verify_effect(self):
        mock = MockTransport(demo_snapshot())
        agent = self.agent(mode="simulate")
        await agent.process(mock.snapshot, mock)
        for _ in range(4):
            result = await agent.process(copy.deepcopy(mock.snapshot), mock)
            self.assertEqual(result["ignored"], "DUPLICATE_OR_OUT_OF_ORDER")
        self.assertEqual(self.store.stats("FUME_STOPPED", "start_fume", "1.0", "simulation")["successes"], 0)

    async def test_replay_never_executes_or_learns_repair(self):
        mock = MockTransport(demo_snapshot())
        agent = self.agent(mode="replay")
        result = await agent.process(mock.snapshot, mock)
        self.assertEqual(result["faults"][0]["outcome"], "DRY_RUN")
        self.assertEqual(mock.sent, [])
        self.assertEqual(self.store.stats("FUME_STOPPED", "start_fume", "1.0", "simulation")["verified_attempts"], 0)

    async def test_unknown_attempt_after_restart_is_not_retried(self):
        fingerprint = hashlib.sha256(b"FUME_STOPPED:fume_extraction_01").hexdigest()
        incident = self.store.open_incident(fingerprint, "FUME_STOPPED", ["air"], [], "simulation", 900)
        self.store.begin_attempt(incident, "start_fume", "1.0", "simulation", "simulate",
                                 [{"type": "control", "node_id": "fume_extraction_01", "action": "turn_on"}], 900)
        self.store.recover_pending("simulation")
        mock = MockTransport(demo_snapshot())
        result = await self.agent(mode="simulate").process(mock.snapshot, mock)
        self.assertEqual(mock.sent, [])
        self.assertEqual(result["faults"][0]["outcome"], "UNKNOWN")

    async def test_unreviewed_planner_commands_have_no_authority(self):
        class Injection:
            def choose(self, report, candidates, context):
                return {"runbook_id": "disable_interlock", "commands": [{"action": "block_interlock"}]}
        mock = MockTransport(demo_snapshot())
        result = await self.agent(mode="observe", planner=Injection()).process(mock.snapshot, mock)
        self.assertEqual(mock.sent, [])
        self.assertEqual(result["faults"][0]["selected_runbook"], "start_fume")
        self.assertIn("error", result["faults"][0]["planner"])

    async def test_protected_action_is_rejected_even_if_catalog_exposes_it(self):
        mock = MockTransport(demo_snapshot())
        mock.catalog["dome_automation_01"] = {"controls": ["block_interlock"]}
        agent = self.agent(mode="simulate")
        action = {"type": "control", "node_id": "dome_automation_01", "action": "block_interlock", "value": {"id": "IL_SMOKE"}}
        allowed, reason = agent.gate([action], {}, mock.snapshot["data"]["nodes"], 1000, mock.snapshot["context"], mock)
        self.assertFalse(allowed)
        self.assertEqual(reason, "ACTION_NOT_ALLOWLISTED")

    async def test_execute_without_approved_live_configuration_is_blocked(self):
        mock = MockTransport(demo_snapshot())
        result = await self.agent(mode="execute").process(mock.snapshot, mock)
        self.assertEqual(mock.sent, [])
        self.assertEqual(result["faults"][0]["gate"]["reason"], "LIVE_CONFIGURATION_NOT_APPROVED")

    async def test_execute_defers_until_a_new_frame_after_planning(self):
        mock = MockTransport(demo_snapshot())
        mock.allow_control = True
        agent = self.agent(mode="execute", config=Config(approved_live=True, min_airflow_pct=1))
        result = await agent.process(mock.snapshot, mock)
        self.assertEqual(mock.sent, [])
        self.assertEqual(result["faults"][0]["outcome"], "WAITING_FOR_FRESH_FRAME_AFTER_PLAN")
        await agent.process(mock.tick(4), mock)
        self.assertEqual(len(mock.sent), 1)

    async def test_new_smoke_interrupts_pending_repair_without_followup(self):
        mock = MockTransport(demo_snapshot())
        agent = self.agent(mode="simulate")
        await agent.process(mock.snapshot, mock)
        frame = mock.tick(4)
        frame["data"]["nodes"]["smoke_detector_01"]["smoke_detected"] = True
        result = await agent.process(frame, mock)
        self.assertEqual(result["faults"][0]["code"], "EMERGENCY")
        self.assertEqual(result["outcomes"][0]["outcome"], "UNKNOWN")
        self.assertEqual(len(mock.sent), 1)

    async def test_failure_memory_changes_rank_but_not_new_version(self):
        for index in range(2):
            incident = self.store.open_incident(f"seed-{index}", "FUME_STOPPED", ["air"], [], "simulation", 900)
            attempt = self.store.begin_attempt(incident, "start_fume", "1.0", "simulation", "simulate", [{"action": "turn_on"}], 900)
            self.store.finish_attempt(attempt, "FAILED", {"actual_execution": True, "effect_confirmed": False}, 920)
        result = await self.agent().process(demo_snapshot())
        self.assertEqual(result["faults"][0]["selected_runbook"], "inspect_fume_mechanical")
        self.assertTrue(any(h["kind"] == "case" and h["provenance"]["type"] == "agent_case" for h in result["faults"][0]["retrieved"]))
        newer = await self.agent(config=Config(runbook_version="2.0")).process(demo_snapshot())
        self.assertEqual(newer["faults"][0]["selected_runbook"], "start_fume")

    async def test_model_call_is_cached_for_unchanged_incident(self):
        class Planner:
            calls = 0
            def choose(self, report, candidates, context):
                self.calls += 1
                return {"runbook_id": candidates[0]["runbook_id"], "diagnosis": "test", "evidence_ids": [], "checks": []}
        planner = Planner()
        mock = MockTransport(demo_snapshot(), effect=False)
        agent = self.agent(planner=planner)
        await agent.process(mock.snapshot)
        await agent.process(mock.tick(4))
        self.assertEqual(planner.calls, 1)

    async def test_fresh_air_steps_are_sequential_and_fault_effect_is_checked(self):
        mock = MockTransport(demo_snapshot("co2"))
        agent = self.agent(mode="simulate")
        await agent.process(mock.snapshot, mock)
        self.assertEqual([a["action"] for a in mock.sent], ["set_mode"])
        outcomes = []
        for _ in range(7):
            result = await agent.process(mock.tick(4), mock)
            outcomes.extend(result["outcomes"])
        self.assertEqual([a["action"] for a in mock.sent], ["set_mode", "set_speed"])
        self.assertTrue(any(o["outcome"] == "VERIFIED_SUCCESS" for o in outcomes))

    async def test_shared_signals_are_views_of_one_canonical_value(self):
        agent = self.agent()
        frame = demo_snapshot("healthy")
        owners = agent.membership(frame["data"]["nodes"])
        self.assertIn("air", owners["fume_extraction_01.enabled"])
        self.assertIn("production", owners["fume_extraction_01.enabled"])
        self.assertEqual(frame["data"]["nodes"]["fume_extraction_01"]["enabled"], True)

    async def test_public_audit_callback_is_callable_without_or_with_log_stream(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            agent = self.agent(config=Config(log_dir=folder))
            self.assertTrue(callable(agent.audit))
            agent.audit("connection", event="connected", role="team")
            await agent.shutdown()
            entries = [json.loads(line) for file in Path(folder).glob("agent_*.jsonl") for line in file.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(e["kind"] == "connection" and e.get("role") == "team" for e in entries))
            self.assertTrue(callable(self.agent().audit))

    async def test_llm_audit_preserves_usage_latency_and_served_model(self):
        import json
        import tempfile
        class Planner:
            model = "configured-model"
            def choose(self, report, candidates, context):
                return {"runbook_id": "start_fume", "diagnosis": "test", "evidence_ids": [], "checks": [],
                        "usage": {"prompt_tokens": 17, "completion_tokens": 8}, "latency_ms": 12,
                        "served_model": "served-version"}
        with tempfile.TemporaryDirectory() as folder:
            agent = self.agent(planner=Planner(), config=Config(log_dir=folder))
            await agent.process(demo_snapshot())
            entries = [json.loads(line) for file in Path(folder).glob("agent_*.jsonl") for line in file.read_text(encoding="utf-8").splitlines()]
            entry = next(e for e in entries if e["kind"] == "llm_call")
            self.assertEqual((entry["prompt_tokens"], entry["completion_tokens"], entry["latency_ms"], entry["served_model"]), (17, 8, 12, "served-version"))
            await agent.shutdown()

    async def test_valid_planner_abstention_never_falls_back_to_execution(self):
        class Abstain:
            def choose(self, report, candidates, context):
                return {"runbook_id": None, "diagnosis": "Недостаточно данных", "evidence_ids": [], "checks": []}
        mock = MockTransport(demo_snapshot())
        result = await self.agent(mode="simulate", planner=Abstain()).process(mock.snapshot, mock)
        fault = next(f for f in result["faults"] if f["code"] == "FUME_STOPPED")
        self.assertTrue(fault["planner_abstained"])
        self.assertEqual(fault["selected_runbook"], "check_telemetry")
        self.assertEqual(fault["proposed_actions"], [])
        self.assertEqual(mock.sent, [])

    async def test_slow_other_diagnosis_cannot_execute_an_aged_frame(self):
        import time
        class SlowQualityPlanner:
            def choose(self, report, candidates, context):
                if report["code"] == "DATA_QUALITY":
                    time.sleep(0.2)
                return {"runbook_id": candidates[0]["runbook_id"], "diagnosis": "test", "evidence_ids": [], "checks": []}
        mock = MockTransport(demo_snapshot())
        mock.allow_control = True
        agent = self.agent(mode="execute", planner=SlowQualityPlanner(),
                           config=Config(approved_live=True, min_airflow_pct=1, max_age_s=0.1))
        await agent.process(mock.snapshot, mock)
        self.assertEqual(mock.sent, [])
        frame = mock.tick(4)
        frame["data"]["nodes"]["irrelevant_sensor"] = {"online": False, "status": "UNKNOWN", "updated_at": frame["timestamp"]}
        frame["_received_monotonic"] = time.monotonic()
        result = await agent.process(frame, mock)
        fault = next(f for f in result["faults"] if f["code"] == "FUME_STOPPED")
        self.assertEqual(fault["gate"]["reason"], "FRAME_STALE_AFTER_RECEIVE")
        self.assertEqual(mock.sent, [])

    async def test_newer_transport_frame_blocks_old_plan(self):
        mock = MockTransport(demo_snapshot())
        mock.allow_control = True
        agent = self.agent(mode="execute", config=Config(approved_live=True, min_airflow_pct=1))
        await agent.process(mock.snapshot, mock)
        old_frame = mock.tick(4)
        mock.tick(4)
        result = await agent.process(old_frame, mock)
        fault = next(f for f in result["faults"] if f["code"] == "FUME_STOPPED")
        self.assertEqual(fault["gate"]["reason"], "NEWER_FRAME_REQUIRES_REVALIDATION")
        self.assertEqual(mock.sent, [])

    async def test_co2_verification_requires_independent_post_action_sources(self):
        agent = self.agent(mode="simulate")
        frame = demo_snapshot("co2")
        nodes = frame["data"]["nodes"]
        for name in ("climate_sensor_01", "climate_sensor_02", "climate_sensor_03"):
            nodes[name].update(co2_ppm=730, updated_at=1004)
        pending = {"fault": {"code": "CO2_CONFIRMED"}, "step_started": 1000}
        context = frame["context"]
        self.assertTrue(agent._fault_effect(pending, nodes, 1004, context))
        context["source_origins"] = {n: "one_sensor" for n in context["source_origins"]}
        self.assertFalse(agent._fault_effect(pending, nodes, 1004, context))
        context["source_origins"] = {n: n for n in ("climate_sensor_01", "climate_sensor_02", "climate_sensor_03")}
        for name in context["source_origins"]:
            nodes[name]["updated_at"] = 1000
        self.assertFalse(agent._fault_effect(pending, nodes, 1004, context))

    async def test_timeout_with_no_new_usable_measurement_is_unknown(self):
        mock = MockTransport(demo_snapshot(), effect=False)
        agent = self.agent(mode="simulate", config=Config(max_age_s=100, verification_timeout_s=12))
        await agent.process(mock.snapshot, mock)
        frame = mock.tick(12)
        frame["data"]["nodes"]["fume_extraction_01"]["updated_at"] = 1000
        result = await agent.process(frame, mock)
        self.assertEqual(result["outcomes"][0]["outcome"], "UNKNOWN")
        stats = self.store.stats("FUME_STOPPED", "start_fume", "1.0", "simulation")
        self.assertEqual(stats["verified_attempts"], 0)

    async def test_supply_mode_and_speed_require_approved_physical_flow(self):
        agent = self.agent(mode="simulate", config=Config(min_supply_flow_m3_h=500))
        frame = demo_snapshot("healthy")
        node = frame["data"]["nodes"]["supply_ventilation_01"]
        node.update(mode="FRESH_AIR", speed_pct=100, airflow_m3_h=0.1, fresh_air_m3_h=0.1)
        for action, value in (("set_mode", "FRESH_AIR"), ("set_speed", 100)):
            request = {"node_id": "supply_ventilation_01", "action": action, "value": value}
            self.assertFalse(agent._effect(request, frame["data"]["nodes"], 1000))

    async def test_observe_audits_candidate_decision_and_rag_references(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            agent = self.agent(mode="observe", config=Config(log_dir=folder))
            result = await agent.process(demo_snapshot())
            await agent.shutdown()
            entries = [json.loads(line) for file in Path(folder).glob("agent_*.jsonl") for line in file.read_text(encoding="utf-8").splitlines()]
            decision = next(e for e in entries if e["kind"] == "decision")
            self.assertEqual(decision["selected_runbook"], result["faults"][0]["selected_runbook"])
            self.assertEqual(decision["outcome"], "DRY_RUN")
            self.assertTrue(decision["proposed_only"])
            self.assertIn(decision["source"], {"rules", "mixed"})
            self.assertTrue(decision["rag_refs"])
            self.assertFalse(any(e["kind"] == "action" for e in entries))


if __name__ == "__main__":
    unittest.main()
