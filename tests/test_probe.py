import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import check_dome
from kupol_agent.transport import TransportError


class AddressTests(unittest.TestCase):
    def test_local_address_and_rejected_credential_url(self):
        self.assertEqual(check_dome.normalize_address('192.168.8.126:8765'), 'ws://192.168.8.126:8765')
        self.assertEqual(check_dome.normalize_address('wss://host:443/api'), 'wss://host:443/api')
        for value in ('http://host', 'ws://user:secret@host', 'ws://host:bad', 'ws://host?key=secret', ''):
            with self.assertRaises(ValueError):
                check_dome.normalize_address(value)

    def test_report_and_parameters_redact_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            report = dict(success=False, address='ws://host:8765', started_at='now', stage='websocket',
                          tcp_ok=True, websocket_ok=False, authorized=False, frames=0, nodes=0,
                          error='echo PRIVATE_TEST_KEY', nested={'authorization': 'secret'})
            with redirect_stdout(io.StringIO()):
                check_dome.finish_report(report, folder, ['PRIVATE_TEST_KEY'])
            self.assertNotIn('PRIVATE_TEST_KEY', (folder/'report.json').read_text(encoding='utf-8'))
            self.assertNotIn('"secret"', (folder/'report.json').read_text(encoding='utf-8'))
            self.assertIn('НЕУСПЕШНО', (folder/'report.txt').read_text(encoding='utf-8'))
        nodes = check_dome.sanitize({'node': {'key': 'bad', 'enabled': False, 'power_w': 1.2}}, [])
        rendered = check_dome._parameters(nodes, {'node': {'title': 'Устройство'}})
        self.assertIn('Устройство [node]', rendered)
        self.assertIn('enabled: нет', rendered)
        self.assertNotIn('bad', rendered)


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def run_probe(self, transport_type, duration=.01):
        report = dict(stage='tcp', frames=0, nodes=0, invalid_frames=0)
        writer = unittest.mock.Mock()
        writer.wait_closed = AsyncMock()
        writer.get_extra_info.return_value = ('192.168.8.100', 1234)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with patch('check_dome.asyncio.open_connection', new=AsyncMock(return_value=(None, writer))), redirect_stdout(io.StringIO()):
                try:
                    await check_dome.probe('ws://192.168.8.126:8765', 'PRIVATE_TEST_KEY', 4,
                                           duration, report, folder, ['PRIVATE_TEST_KEY'], transport_type)
                except Exception as exc:
                    return report, exc, {}
            return report, None, {p.name: p.read_text(encoding='utf-8') for p in folder.iterdir()}

    async def test_new_telemetry_saved_without_controls_or_secrets(self):
        class Session:
            role, team = 'team', 4
            catalog = {'node': {'title': 'Узел'}}
            def __init__(self, url, key, **kwargs):
                if kwargs['allow_control']:
                    raise AssertionError('Read-only required')
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def frames(self):
                yield {'type': 'telemetry', 'timestamp': 123, '_received_monotonic': 100,
                       'data': {'nodes': {'node': {'online': True, 'voltage_v': 24,
                                                  'description': 'PRIVATE_TEST_KEY'}}}}
                await asyncio.sleep(1)
        report, error, files = await self.run_probe(Session)
        self.assertIsNone(error)
        self.assertEqual(report['frames'], 1)
        self.assertEqual(report['stage'], 'complete')
        self.assertEqual(report['online_nodes'], 1)
        self.assertNotIn('PRIVATE_TEST_KEY', ''.join(files.values()))
        self.assertNotIn('_received_monotonic', files['latest_telemetry.json'])
        self.assertEqual(json.loads(files['latest_telemetry.json'])['data']['nodes']['node']['voltage_v'], 24)

    async def test_wrong_team_never_reads_telemetry(self):
        class Session:
            role, team = 'team', 3
            catalog = {}
            def __init__(self, *args, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def frames(self):
                raise AssertionError('Wrong team must not proceed')
        report, error, _ = await self.run_probe(Session)
        self.assertIsInstance(error, TransportError)
        self.assertEqual(report['frames'], 0)
        self.assertIn('команды 4', str(error))

    async def test_authorized_but_silent_server_is_failure(self):
        class Session:
            role, team, catalog = 'team', 4, {}
            def __init__(self, *args, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def frames(self):
                await asyncio.sleep(1)
                yield {}
        report, error, _ = await self.run_probe(Session)
        self.assertIsInstance(error, TransportError)
        self.assertEqual(report['frames'], 0)
        self.assertIn('не поступила', str(error))


if __name__ == '__main__':
    unittest.main()
