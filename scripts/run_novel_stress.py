"""Phantom-repair stress: mock ACK, stale success echoes, delayed real effect.

Uses the actual Agent, retriever, verifier and case memory. The only external
call is one official DeepSeek plan over five synthetic nodes. No Dome transport.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run_generated_crisis import CrisisPlanner
from kupol_agent.credentials import load_team_keys
from kupol_agent.engine import Agent, Config, MockTransport, demo_snapshot
from kupol_agent.llm import PlannerError
from kupol_agent.memory import CaseStore
from kupol_agent.retrieval import DocumentIndex, HybridRetriever


def generated_frame():
    frame = demo_snapshot('fume_stopped')
    keep = {'fume_extraction_01', 'smart_panel_01', 'dome_automation_01', 'smoke_detector_01', 'smoke_detector_02'}
    frame['data']['nodes'] = {name: node for name, node in frame['data']['nodes'].items() if name in keep}
    frame['timestamp'] = time.time()
    for node in frame['data']['nodes'].values():
        node['updated_at'] = frame['timestamp']
    return frame


class DelayedMock(MockTransport):
    def __init__(self, snapshot, planner):
        super().__init__(snapshot, effect=False)
        self.planner = planner

    def can_control(self, request):
        return (isinstance(self.planner.validated, dict)
                and self.planner.validated.get('runbook_id') == 'start_fume'), 'REAL_MODEL_REQUIRED_IN_THIS_TEST'


class ReusedInitialPlan:
    """The secondary safety branch reuses a documented actual model plan."""
    model = 'cached-initial-DeepSeek-plan; no-new-provider-call'

    def __init__(self, result):
        self.validated = deepcopy(result)

    def choose(self, report, candidates, context):
        if report.get('code') != 'FUME_STOPPED':
            raise PlannerError('Secondary safety branch does not request another model diagnosis')
        result = deepcopy(self.validated)
        result['usage'] = {}
        result['latency_ms'] = 0
        result['source'] = 'reused_validated_initial_plan'
        return result


def success_rows(result):
    return [row for row in result.get('outcomes', []) if row.get('outcome') == 'VERIFIED_SUCCESS']


async def main():
    run = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6]
    folder = ROOT / 'output/novel_stress' / run
    folder.mkdir(parents=True)
    frame = generated_frame()
    key = load_team_keys(ROOT / 'Dome_RAG-main.zip', 4)['deepseek_api_key']
    planner = CrisisPlanner(key, deepcopy(frame))
    store = CaseStore(folder / 'cases.sqlite')
    retriever = HybridRetriever(DocumentIndex([ROOT / 'kupol_agent/knowledge']), store)
    config = Config(approved_live=True, min_airflow_pct=30, log_dir=str(folder / 'logs'))
    environment = 'synthetic:phantom_repair:' + run
    transport = DelayedMock(frame, planner)
    agent = Agent(store, retriever, planner=planner, config=config, environment=environment, mode='simulate')
    started = time.monotonic()
    report = {'schema_version': 1, 'run': run, 'passed': False,
              'scope': 'ISOLATED_SYNTHETIC_STRESS_WITH_REAL_DEEPSEEK',
              'scenario': 'ACK success with cached positive telemetry and delayed actuator startup',
              'novelty_claim': 'Designed combination of existing failure protections; not a claim that constituent failures were undocumented.',
              'dome_commands_sent': 0, 'physical_equipment_tested': False}
    branches = [agent]
    try:
        first = await agent.process(frame, transport)
        fault = next(row for row in first['faults'] if row['code'] == 'FUME_STOPPED')
        report['planner'] = fault.get('planner')
        report['retrieved_source_ids'] = [hit['id'] for hit in fault.get('retrieved', [])]
        if not (planner.validated and planner.validated.get('runbook_id') == 'start_fume' and len(transport.sent) == 1):
            raise AssertionError('Actual DeepSeek did not authorize the expected reviewed runbook')
        original_timestamp = frame['timestamp']
        false_successes = 0
        duplicate_ignored = 0
        burst_started = time.monotonic()
        for index in range(100):
            ghost = deepcopy(frame)
            ghost['timestamp'] = original_timestamp if index % 2 == 0 else original_timestamp - 1
            ghost['data']['nodes']['fume_extraction_01'].update(enabled=True, fan_rpm=2000, airflow_pct=90)
            result = await agent.process(ghost, transport)
            duplicate_ignored += result.get('ignored') == 'DUPLICATE_OR_OUT_OF_ORDER'
            false_successes += len(success_rows(result))
        duplicate_duration_ms = round((time.monotonic() - burst_started) * 1000, 3)
        for index in range(3):
            ghost = deepcopy(frame)
            ghost['timestamp'] = original_timestamp + (index + 1) / 10
            for node in ghost['data']['nodes'].values():
                node['updated_at'] = ghost['timestamp']
            ghost['data']['nodes']['fume_extraction_01'].update(
                enabled=True, fan_rpm=2000, airflow_pct=90, updated_at=original_timestamp)
            result = await agent.process(ghost, transport)
            false_successes += len(success_rows(result))
        observations_after_ghosts = sum(pending['total_observations'] for pending in agent.active.values())
        # A real fresh observation still reports that startup has not occurred.
        negative = transport.tick(max(time.time(), original_timestamp + .4) - transport.snapshot['timestamp'])
        negative_result = await agent.process(negative, transport)
        false_successes += len(success_rows(negative_result))
        report['before_actual_effect'] = {
            'duplicate_or_out_of_order_frames': 100, 'ignored_duplicate_frames': duplicate_ignored,
            'duplicate_burst_ms': duplicate_duration_ms, 'cached_positive_frames': 3,
            'counted_positive_verification_observations': observations_after_ghosts,
            'fresh_negative_frames': 1, 'false_success_count': false_successes,
            'actuator_command_count': len(transport.sent)}
        # The simulated motor now actually starts. It was not started by a retry.
        transport.snapshot['data']['nodes']['fume_extraction_01'].update(enabled=True, fan_rpm=2000, airflow_pct=90)
        actual = []
        verified = None
        for index in range(3):
            if index:
                await asyncio.sleep(4.1)
            now = max(time.time(), transport.snapshot['timestamp'] + .001)
            update = transport.tick(now - transport.snapshot['timestamp'])
            result = await agent.process(update, transport)
            row = {'timestamp': update['timestamp'], 'updated_at': update['data']['nodes']['fume_extraction_01']['updated_at'],
                   'verified_success_count': len(success_rows(result)), 'wall_elapsed_s': round(time.monotonic() - started, 3)}
            actual.append(row)
            if success_rows(result):
                verified = success_rows(result)[-1]
        span = actual[-1]['timestamp'] - actual[0]['timestamp']
        report['delayed_recovery'] = {'observations': actual, 'observation_span_s': round(span, 3),
            'outcome': verified, 'actuator_commands': transport.sent,
            'memory_stats': store.stats('FUME_STOPPED', 'start_fume', '1.0', environment)}
        # Separate branch: a fresh enabled=true/fan=0/flow=0 observation is a
        # critical mechanical fault; accepting a later echo as repair is unsafe.
        branch_frame = generated_frame()
        reused = ReusedInitialPlan(planner.validated)
        branch_transport = DelayedMock(branch_frame, reused)
        branch_environment = environment + ':mechanical'
        branch = Agent(store, retriever, planner=reused, config=config,
                       environment=branch_environment, mode='simulate')
        branches.append(branch)
        await branch.process(branch_frame, branch_transport)
        branch_transport.snapshot['data']['nodes']['fume_extraction_01'].update(enabled=True, fan_rpm=0, airflow_pct=0)
        failed_frame = branch_transport.tick(1)
        failed = await branch.process(failed_frame, branch_transport)
        unknown = [row for row in failed['outcomes'] if row.get('outcome') == 'UNKNOWN']
        branch_transport.snapshot['data']['nodes']['fume_extraction_01'].update(enabled=True, fan_rpm=2000, airflow_pct=90)
        later = await branch.process(branch_transport.tick(9), branch_transport)
        mechanical_stats = store.stats('FUME_STOPPED', 'start_fume', '1.0', branch_environment)
        report['mechanical_failure_branch'] = {
            'initial_plan': 'Reused the actual validated DeepSeek start_fume result; no additional provider call',
            'critical_fault_detected': any(row['code'] == 'FUME_MECHANICAL' for row in failed['faults']),
            'pending_outcomes': unknown, 'false_success_after_late_positive': len(success_rows(later)),
            'actuator_command_count': len(branch_transport.sent), 'memory_stats': mechanical_stats,
            'safety_diagnosis': 'Critical gate closes pending verification; new mechanical diagnosis uses reviewed fallback without another LLM call.'}
        report['passed'] = bool(duplicate_ignored == 100 and observations_after_ghosts == 0
            and false_successes == 0 and verified and span >= 8
            and all(row['verified_success_count'] == 0 for row in actual[:2])
            and len(transport.sent) == 1 and mechanical_stats['successes'] == 0
            and mechanical_stats['unknown'] == 1 and unknown
            and not success_rows(later) and len(branch_transport.sent) == 1)
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=planner._safe(str(exc)))
    finally:
        for active_agent in branches:
            await active_agent.shutdown()
        store.close()
        report['duration_s'] = round(time.monotonic() - started, 3)
        report['provider_calls'] = planner.calls
        report['limits'] = ['Mock actuator recovery only; not a physical mechanical repair',
                            'Training DeepSeek alias does not establish exact final-event model compliance']
        text = json.dumps(planner._safe(report), ensure_ascii=False, indent=2) + '\n'
        assert key not in text
        (folder / 'result.json').write_text(text, encoding='utf-8')
        (ROOT / 'output/novel_stress_result.json').write_text(text, encoding='utf-8')
        print(json.dumps({name: report.get(name) for name in ('passed', 'before_actual_effect', 'duration_s', 'error_type', 'error')}, ensure_ascii=False))
        print(str(folder / 'result.json'))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
