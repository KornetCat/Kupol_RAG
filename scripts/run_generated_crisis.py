"""Generated crisis acceptance: real RAG/DeepSeek, strictly in-memory actuators.

No LiveTransport, Dome address or Dome credential is passed to any transport.
The only external call is official DeepSeek with five selected synthetic nodes.
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

from kupol_agent.chat import select_chat_telemetry
from kupol_agent.credentials import load_team_keys
from kupol_agent.engine import Agent, Config, MockTransport, demo_snapshot
from kupol_agent.llm import DeepSeekPlanner
from kupol_agent.memory import CaseStore
from kupol_agent.retrieval import DocumentIndex, HybridRetriever


class CrisisPlanner(DeepSeekPlanner):
    def __init__(self, key, frame):
        super().__init__(api_key=key, base_url='https://api.deepseek.com', model='deepseek-flash')
        self.frame = frame
        self.calls = []
        self.validated = None

    def _request(self, payload):
        started = time.monotonic()
        record = {'destination': 'https://api.deepseek.com/chat/completions',
                  'request_bytes': len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))}
        self.calls.append(record)
        try:
            result = super()._request(payload)
            record['ok_http'] = True
            return result
        except Exception as exc:
            record.update(ok_http=False, error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            record['latency_ms'] = round((time.monotonic() - started) * 1000)

    def choose(self, report, candidates, context):
        selected = select_chat_telemetry(
            'fume_extraction_01 smoke_detector_01 smoke_detector_02',
            self.frame, {}, context)
        assert selected['selection']['selected_node_count'] <= 5
        report = {**report, 'selected_synthetic_telemetry': selected,
                  'test_scope': 'Generated local crisis; actuator is a local mock, no Dome commands.',
                  'trusted_operation_context': {'operator_activity': 'SOLDERING',
                      'required_fume': True, 'emergency_stop': False, 'trusted': True}}
        result = super().choose(report, candidates, context)
        self.validated = result
        return result


class GuardedMock(MockTransport):
    def __init__(self, snapshot, planner):
        super().__init__(snapshot)
        self.planner = planner
        self.effect_wall_observations = []

    def can_control(self, request):
        ready = isinstance(self.planner.validated, dict) and self.planner.validated.get('runbook_id') == 'start_fume'
        return ready, 'REQUIRES_VALIDATED_DEEPSEEK_FOR_THIS_ACCEPTANCE'


async def main():
    run_name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6]
    folder = ROOT / 'output/crisis_synthetic' / run_name
    folder.mkdir(parents=True)
    frame = demo_snapshot('fume_stopped')
    node_ids = {'fume_extraction_01', 'smart_panel_01', 'dome_automation_01',
                'smoke_detector_01', 'smoke_detector_02'}
    frame['data']['nodes'] = {name: node for name, node in frame['data']['nodes'].items() if name in node_ids}
    stamp = time.time()
    frame['timestamp'] = stamp
    for node in frame['data']['nodes'].values():
        node['updated_at'] = stamp
    # These are explicit test limits, not proposed physical safety thresholds.
    config = Config(approved_live=True, min_airflow_pct=30,
                    verification_window_s=8, verification_observations=2,
                    log_dir=str(folder / 'logs'))
    key = load_team_keys(ROOT / 'Dome_RAG-main.zip', 4)['deepseek_api_key']
    planner = CrisisPlanner(key, deepcopy(frame))
    store = CaseStore(folder / 'cases.sqlite')
    retriever = HybridRetriever(DocumentIndex([ROOT / 'kupol_agent/knowledge']), store)
    environment = 'synthetic:generated_fume:' + run_name
    transport = GuardedMock(frame, planner)
    agent = Agent(store, retriever, planner=planner, config=config,
                  environment=environment, mode='simulate')
    started = time.monotonic()
    report = {'schema_version': 1, 'run': run_name,
              'scope': 'SYNTHETIC_LOCAL_ACTUATOR_WITH_REAL_DEEPSEEK_RAG',
              'scenario': 'FUME_STOPPED during declared soldering; power present, motor OK, no smoke, protections ARMED',
              'physical_equipment_tested': False, 'dome_commands_sent': 0,
              'environment': environment, 'passed': False}
    frames = []
    try:
        first = await agent.process(frame, transport)
        fault = next((fault for fault in first['faults'] if fault['code'] == 'FUME_STOPPED'), None)
        report['detected_fault'] = fault['code'] if fault else None
        report['retrieved_sources'] = [{'id': hit['id'], 'source': Path(str(hit.get('source', ''))).name,
                                        'section': hit.get('section'), 'category': hit.get('category')}
                                       for hit in (fault or {}).get('retrieved', [])]
        report['planner'] = (fault or {}).get('planner')
        report['gate'] = (fault or {}).get('gate')
        report['initial_outcome'] = (fault or {}).get('outcome')
        outcome = None
        if transport.sent:
            # Use actual wall time between observations. Three observations
            # span at least 8 seconds after the first confirmed-effect frame.
            for _ in range(3):
                await asyncio.sleep(4.1)
                now = time.time()
                update = transport.tick(now - transport.snapshot['timestamp'])
                result = await agent.process(update, transport)
                node = update['data']['nodes']['fume_extraction_01']
                frames.append({'timestamp': update['timestamp'], 'updated_at': node['updated_at'],
                               'enabled': node['enabled'], 'fan_rpm': node['fan_rpm'],
                               'airflow_pct': node['airflow_pct'],
                               'wall_elapsed_s': round(time.monotonic() - started, 3)})
                finished = [row for row in result['outcomes'] if row.get('outcome') == 'VERIFIED_SUCCESS']
                if finished:
                    outcome = finished[-1]
        report['verification_frames'] = frames
        report['simulated_actuator_commands'] = transport.sent
        report['outcome'] = outcome
        report['memory_stats'] = store.stats('FUME_STOPPED', 'start_fume', '1.0', environment)
        report['searchable_memory_cases'] = len(store.search_cases('FUME_STOPPED start_fume', environment))
        report['effect_observation_span_s'] = round(frames[-1]['timestamp'] - frames[0]['timestamp'], 3) if len(frames) > 1 else 0
        report['passed'] = bool(fault and planner.validated and planner.validated.get('runbook_id') == 'start_fume'
            and len(planner.calls) == 1 and len(transport.sent) == 1 and outcome
            and len(frames) >= 2 and report['effect_observation_span_s'] >= 8
            and report['memory_stats']['successes'] == 1 and report['searchable_memory_cases'] >= 1)
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=planner._safe(str(exc)))
    finally:
        await agent.shutdown()
        store.close()
        report['provider_calls'] = planner.calls
        report['duration_s'] = round(time.monotonic() - started, 3)
        report['served_model_note'] = 'Training API alias; not evidence of exact final-event V4 Flash compliance.'
        text = json.dumps(planner._safe(report), ensure_ascii=False, indent=2) + '\n'
        assert key not in text
        (folder / 'result.json').write_text(text, encoding='utf-8')
        (ROOT / 'output/generated_crisis_result.json').write_text(text, encoding='utf-8')
        print(json.dumps({name: report.get(name) for name in ('passed', 'detected_fault', 'initial_outcome',
            'effect_observation_span_s', 'duration_s', 'error_type', 'error')}, ensure_ascii=False))
        print(str(folder / 'result.json'))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
