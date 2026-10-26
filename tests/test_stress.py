"""Synthetic overload/failure tests: no network, provider or equipment access.

Run this file directly to also write measured output/stress_results.json.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from kupol_agent.control import ControlError
from kupol_agent.runtime import DomeRuntime
from kupol_agent.transport import LiveTransport
from test_runtime import FakeChat, FakePlanner, FakeRetriever, FakeTransport
from test_transport import FakeSocket


MEASUREMENTS = []


async def until(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Synthetic stress condition did not complete')
        await asyncio.sleep(.002)


class GatedChat(FakeChat):
    entered = 0
    release = None
    counter_lock = threading.Lock()

    def reply(self, *args, **kwargs):
        with type(self).counter_lock:
            type(self).entered += 1
        if not type(self).release.wait(15):
            raise AssertionError('Synthetic model gate timed out')
        return super().reply(*args, **kwargs)


class StressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            'DOME_KEY':'LOCAL_SYNTHETIC_DOME_KEY', 'DEEPSEEK_API_KEY':'LOCAL_SYNTHETIC_MODEL_KEY',
            'DOME_URL':'', 'DEEPSEEK_FINAL_MODEL':'', 'DEEPSEEK_FINAL_SERVED_MODEL':'',
            'AGENT_LOG_DIR':str(self.root/'logs')})
        self.environment.start()
        FakeTransport.instances = []
        FakeTransport.next_role = 'team'
        FakeTransport.reject_queries = False
        FakeTransport.fail_after_send = False
        FakeChat.calls, FakeChat.slow_on = [], False
        GatedChat.entered, GatedChat.release = 0, threading.Event()
        self.runtimes, self.transports, self.background = [], [], []

    async def asyncTearDown(self):
        GatedChat.release.set()
        for task in self.background:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.background, return_exceptions=True)
        for runtime in self.runtimes:
            await runtime.close()
        for transport in self.transports:
            await transport.__aexit__(None, None, None)
        self.environment.stop()
        self.temporary.cleanup()

    def measure(self, case, started, **facts):
        MEASUREMENTS.append({'case':case, 'passed':True,
            'duration_s':round(time.monotonic()-started, 6), **facts})

    async def runtime(self, chat_factory=FakeChat):
        runtime = DomeRuntime(data_dir=self.root/'data', keys_archive=self.root/'absent.zip',
            transport_factory=FakeTransport, planner_factory=FakePlanner, chat_factory=chat_factory,
            # Match a streaming server while tolerating asyncio debug/audit I/O
            # in this synthetic stress loop; the product still uses 12 seconds.
            retriever=FakeRetriever(), reconnect_delay=.01, verification_timeout=2.0)
        self.runtimes.append(runtime)
        await runtime.start()
        await runtime.connect({'address':'ws://synthetic.invalid:8766'})
        await until(lambda:runtime._frame is not None and runtime._connection['state']=='connected')
        return runtime

    async def transport(self, on_request=None):
        socket = FakeSocket(on_request=on_request)
        events = []
        transport = LiveTransport('ws://synthetic.invalid:8766', 'LOCAL_SYNTHETIC_KEY',
            max_rate=5, logger=lambda kind, **fields:events.append((kind, fields)))
        self.transports.append(transport)
        with patch.object(transport, '_connect', return_value=socket):
            await transport.__aenter__()
        return transport, socket, events

    async def test_burst_5000_frames_keeps_latest_two(self):
        transport, socket, events = await self.transport()
        started = time.monotonic()
        count = 5000
        for stamp in range(2, count+2):
            socket.feed({'type':'telemetry', 'timestamp':stamp, 'role':'team', 'data':{'nodes':{}}})
        await until(lambda:transport.latest_frame.get('timestamp') == count+1)
        self.assertEqual(transport._frames.maxsize, 2)
        self.assertEqual(transport._frames.qsize(), 2)
        frames = transport.frames()
        retained = [(await anext(frames))['timestamp'], (await anext(frames))['timestamp']]
        self.assertEqual(retained, [count, count+1])
        drops = sum(fields.get('dropped', 0) for kind, fields in events if kind=='transport_queue_drop')
        self.assertEqual(drops, count-1)  # Includes the initial auth-time telemetry frame.
        self.assertFalse(transport._reader_task.done())
        self.measure('transport_burst', started, frames_injected=count, retained_timestamps=retained,
            queue_capacity=transport._frames.maxsize, dropped_old_frames=drops, receiver_alive=True)

    async def test_twelve_parallel_requests_correlate_reverse_responses(self):
        waiting = []
        count = 12
        async def respond(socket, request):
            waiting.append(request)
            if len(waiting) == count:
                # Irrelevant/duplicate replies must not satisfy another request.
                socket.feed({'type':'ping_response','request_id':'not-pending','marker':-1})
                for request in reversed(waiting):
                    socket.feed({'type':'ping_response','success':True,
                        'request_id':request['request_id'],'marker':request['marker']})
        transport, socket, _ = await self.transport(respond)
        started = time.monotonic()
        results = await asyncio.gather(*(transport.send({'type':'ping','marker':index}) for index in range(count)))
        self.assertEqual([response['marker'] for response in results], list(range(count)))
        sent = [(stamp, request) for stamp, request in socket.sent if request['type']=='ping']
        gaps = [b[0]-a[0] for a,b in zip(sent, sent[1:])]
        self.assertEqual(len(sent), count)
        self.assertEqual(transport._pending, {})
        self.assertGreaterEqual(min(gaps), .195)
        self.measure('parallel_reverse_responses', started, concurrent_requests=count,
            matching_responses=count, pending_remaining=0, min_send_gap_s=round(min(gaps),6),
            configured_max_rate_per_s=5)

    async def test_burst_and_smoke_preempt_slow_model_without_control(self):
        runtime = await self.runtime(GatedChat)
        analysis_release, analysis_entered = asyncio.Event(), asyncio.Event()
        async def blocked_analysis(*args, **kwargs):
            analysis_entered.set()
            await analysis_release.wait()
            return {'faults':[], 'outcomes':[]}
        runtime._agent.process = blocked_analysis
        transport = FakeTransport.instances[-1]
        queue = runtime.subscribe()  # Deliberately do not drain the UI subscriber.
        pending = asyncio.create_task(runtime.chat('Включи аварийное освещение'))
        self.background.append(pending)
        await until(lambda:GatedChat.entered == 1)
        count = 500
        started = time.monotonic()
        for index in range(count):
            if index == count//2:
                transport.push(lambda nodes:nodes['smoke_detector_02'].update(smoke_detected=True))
            elif index == count//2+1:
                transport.push(lambda nodes:nodes['smoke_detector_02'].update(smoke_detected=False))
            else:
                transport.push(lambda nodes:None)
        expected = 1000+4*count
        await until(lambda:runtime._frame['timestamp'] == expected)
        self.assertTrue(runtime._hazard_latched)
        self.assertFalse(pending.done())
        self.assertEqual(runtime._analysis_queue.maxsize, 1)
        self.assertLessEqual(runtime._analysis_queue.qsize(), 1)
        self.assertLessEqual(queue.qsize(), 16)
        self.assertFalse(transport.sent)
        elapsed_before_release = time.monotonic()-started
        GatedChat.release.set()
        analysis_release.set()
        with self.assertRaises(ControlError):
            await pending
        self.assertFalse(transport.sent)
        self.assertTrue((self.root/'data/safety.json').is_file())
        self.measure('runtime_burst_with_delayed_model_and_transient_smoke', started,
            frames_injected=count, newest_timestamp=expected, latest_frame_received_before_model_release=True,
            processing_to_latest_s=round(elapsed_before_release,6), analysis_queue_capacity=1,
            ui_subscriber_capacity=16, hazard_latched=True, controls_sent=0)
        runtime.unsubscribe(queue)

    async def test_twenty_four_conflicting_plans_only_latest_executes(self):
        runtime = await self.runtime(GatedChat)
        started = time.monotonic()
        count = 24
        pending = [asyncio.create_task(runtime.chat('Включи аварийное освещение' if index%2==0 else 'Выключи аварийное освещение')) for index in range(count)]
        self.background.extend(pending)
        # Some hosts have fewer executor workers than submitted chats. Wait for
        # all manual epochs to exist, not for every queued model worker to run.
        await until(lambda:runtime._manual_pending == count and GatedChat.entered > 0)
        GatedChat.release.set()
        results = await asyncio.gather(*pending)
        statuses = [result['commands'][0]['status'] for result in results]
        self.assertEqual(statuses.count('SUPERSEDED'), count-1)
        self.assertEqual(statuses[-1], 'STATE_CONFIRMED')
        verified=results[-1]['commands'][0]
        self.assertTrue(verified['response']['success'])
        self.assertTrue(verified['verified'])
        self.assertGreater(verified['observed']['updated_at'],1000)
        self.assertEqual(next(line for line in verified['observed']['lines'] if line['line']==7)['state'],'OFF')
        controls = [request for transport in FakeTransport.instances for request in transport.sent]
        self.assertEqual(controls, [{'type':'control','node_id':'smart_panel_01','action':'line_off','value':7}])
        self.assertEqual(runtime._manual_pending, 0)
        self.assertEqual(runtime.snapshot()['directives']['items'], [])
        self.assertEqual(runtime.snapshot()['directives']['overrides'], [])
        self.assertFalse((self.root/'data/directives.json').exists())
        self.assertTrue(all(call['guidance'] == [] and call['history'] == [] for call in GatedChat.calls))
        self.measure('conflicting_manual_plan_burst', started, concurrent_plans=count,
            superseded_plans=count-1, controls_sent=1, final_action='line_off', final_state='OFF',
            confirmed_updated_at=verified['observed']['updated_at'],ack_success=True,
            persisted_directives=0, persisted_overrides=0, previous_commands_in_future_model_context=0)

    async def test_ten_unknown_outcomes_reconnect_without_replay(self):
        runtime = await self.runtime()
        started = time.monotonic()
        count, session_ids = 10, []
        for index in range(count):
            previous_instances = len(FakeTransport.instances)
            session_ids.append(runtime._session_id)
            FakeTransport.fail_after_send = True
            result = await runtime.chat('Включи аварийное освещение')
            self.assertEqual(result['commands'][0]['status'], 'UNKNOWN')
            await until(lambda:len(FakeTransport.instances)>previous_instances and runtime._frame is not None
                and runtime._connection['state']=='connected')
            self.assertEqual(sum(len(item.sent) for item in FakeTransport.instances), index+1)
            # Old-session analysis work is discarded even after reconnection.
            process_calls = []
            async def record_process(*args, **kwargs):
                process_calls.append(1)
                return {'faults':[], 'outcomes':[]}
            runtime._agent.process = record_process
            await asyncio.sleep(.005)
            process_calls.clear()
            runtime._analysis_queue.put_nowait((session_ids[-1],deepcopy(runtime._frame),{'faults':[],'metrics':{},'changed':False}))
            await asyncio.sleep(.005)
            self.assertEqual(process_calls, [])
        await asyncio.sleep(.05)
        total_sent = sum(len(item.sent) for item in FakeTransport.instances)
        self.assertEqual(total_sent, count)
        self.assertEqual(len(set(session_ids)), count)
        self.measure('unknown_outcome_reconnect_cycles', started, explicit_requests=count,
            reconnects=len(FakeTransport.instances)-1, controls_sent=total_sent,
            automatic_replays=0, old_session_analysis_rejected=count)


if __name__ == '__main__':
    started = time.monotonic()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(StressTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {'schema_version':1, 'generated_at':datetime.now(timezone.utc).isoformat(),
        'scope':'isolated_synthetic_transport_and_runtime', 'network_calls':0,
        'provider_calls':0, 'real_or_virtual_equipment_commands':0,
        'tests_run':result.testsRun, 'successful':result.wasSuccessful(),
        'failures':len(result.failures), 'errors':len(result.errors),
        'total_duration_s':round(time.monotonic()-started,6), 'measurements':MEASUREMENTS,
        'limits':['Synthetic sockets, telemetry and model gates; not server load testing',
                  'Timing is local observation, not a throughput/SLA guarantee']}
    output = ROOT/'output/stress_results.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'result':str(output),'successful':report['successful'],'tests_run':result.testsRun}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
