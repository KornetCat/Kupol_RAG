import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from kupol_agent import cli
from kupol_agent.engine import Agent, Config, demo_snapshot
from kupol_agent.locking import ExecutionLock
from kupol_agent.memory import CaseStore
from kupol_agent.retrieval import DocumentIndex, HybridRetriever
from kupol_agent.transport import TransportError


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_hazard_latch_survives_reconnect_and_pending_is_recovered(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            config = folder/'config.json'
            config.write_text(json.dumps({'approved_live': True, 'min_airflow_pct': 10,
                                         'min_cooling_flow_l_min': 1, 'min_supply_flow_m3_h': 1}), encoding='utf-8')
            context = folder/'context.json'
            context.write_text(json.dumps({'trusted': True, 'emergency_stop': False}), encoding='utf-8')
            store = CaseStore(':memory:')
            incident = store.open_incident('test_pending', 'TEST', ['air'], [], 'station:test:team4', 1000)
            pending = store.begin_attempt(incident, 'start_fume', '1.0', 'station:test:team4', 'execute', [{'action':'turn_on'}], 1000)
            transports = []
            flags_during_frames = []

            class FakeTransport:
                def __init__(self, url, key, logger=None, max_rate=4, allow_control=False):
                    self.allow_control = allow_control
                    self.logger = logger
                    self.role, self.team, self.catalog = 'team', 4, {}
                    transports.append(self)
                async def __aenter__(self):
                    self.logger('connection', event='auth', role='team', team=4)
                    return self
                async def __aexit__(self, *args):
                    pass
                async def frames(self):
                    if len(transports) > 2:
                        raise AssertionError('Unexpected extra reconnect')
                    frame = demo_snapshot(fault='healthy')
                    frame['timestamp'] += len(transports) * 4
                    if len(transports) == 1:
                        frame['data']['nodes']['smoke_detector_01']['smoke_detected'] = True
                        self.logger('incoming', payload=frame)
                    flags_during_frames.append(self.allow_control)
                    yield frame
                    if len(transports) == 1:
                        raise TransportError('test reconnect')
                    raise asyncio.TimeoutError
                async def send(self, request):
                    raise AssertionError('No API write may be sent in this test')

            args = SimpleNamespace(config=config, context=context, execute=True, llm=True,
                keys_archive=None, team=4, dome_url='wss://example.invalid', environment='station:test:team4', duration=30)
            retriever = HybridRetriever(DocumentIndex([]), store)
            with patch.dict(os.environ, {'DOME_KEY':'TEST_ONLY'}), \
                 patch('kupol_agent.transport.LiveTransport', FakeTransport), \
                 patch.object(cli.asyncio, 'sleep', new=AsyncMock()), redirect_stdout(io.StringIO()):
                result = await cli._monitor(args, store, retriever, None, Agent, Config)
            self.assertEqual(result, 0)
            self.assertEqual(flags_during_frames, [False, False])
            self.assertEqual(store.attempts_for_incident(incident)[0]['state'], 'UNKNOWN')
            store.close()

    async def test_execute_rejects_unapproved_config_before_transport(self):
        store = CaseStore(':memory:')
        args = SimpleNamespace(config=None, context=None, execute=True, llm=True)
        with self.assertRaises(ValueError):
            await cli._monitor(args, store, None, None, Agent, Config)
        store.close()


class LockTests(unittest.TestCase):
    def test_executor_lock_is_exclusive_and_released(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'agent.lock'
            with ExecutionLock(path):
                with self.assertRaises(RuntimeError):
                    with ExecutionLock(path):
                        pass
            with ExecutionLock(path):
                pass
