import asyncio
import io
import unittest
from unittest.mock import patch

from aiohttp import CookieJar, FormData
from aiohttp.test_utils import TestClient, TestServer

from kupol_agent.control import MAX_UPLOAD_BYTES
from kupol_agent.web import create_app


class FakeRuntime:
    def __init__(self):
        self.started = self.closed = False
        self.connected = []
        self.messages, self.uploads, self.jobs, self.queues = [], [], [], []
        self.message_kinds = []
        self.controls = []
        self.control_result = {'status': 'STATE_CONFIRMED', 'response': {'success': True}, 'verified': True}
        self.autonomous = False
        self.autonomy_changes = []
        self.cleared = False
        self.work_context = {'required_fume': False, 'declared': False}
        self.work_context_changes = []
        self.autonomy_configurations = []
        self.autonomy_settings = {'min_airflow_pct': None, 'min_supply_flow_m3_h': None,
                                  'min_cooling_flow_l_min': None, 'confirmed': False}

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    def snapshot(self):
        return {'connection': {'state': 'disconnected', 'role': None}, 'autonomous': self.autonomous,
                'work_context': dict(self.work_context),
                'autonomy_settings': dict(self.autonomy_settings),
                'manual_controls': {'smart_panel_01': {'title': 'Щиток', 'controls': ['line_on', 'line_off']}}}

    async def connect(self, payload):
        self.connected.append(payload)
        return {'connected': True}

    async def disconnect(self):
        return {'connected': False}

    async def chat(self, message, *, message_kind='request'):
        self.messages.append(message)
        self.message_kinds.append(message_kind)
        return {'answer': 'Ответ RAG', 'commands': [], 'sources': ['doc:test']}

    async def upload(self, node_id, filename, content, *, organizer_prepared=False):
        self.uploads.append((node_id, filename, content, organizer_prepared))
        return {'uploaded': filename, 'node_id': node_id}

    async def manual_control(self, request):
        self.controls.append(request)
        return {'request': request, 'source': 'manual_api', **self.control_result}

    async def files(self, node_id):
        return {'files': [{'name': 'part.gcode'}], 'node_id': node_id}

    async def start_job(self, node_id, file_name):
        self.jobs.append((node_id, file_name))
        return {'started': file_name}

    async def set_autonomy(self, enabled):
        self.autonomy_changes.append(enabled)
        self.autonomous = enabled
        return {'autonomous': enabled}

    async def configure_autonomy(self, payload):
        self.autonomy_configurations.append(payload)
        self.autonomy_settings = {key: value for key, value in payload.items() if key != 'safety_confirmed'}
        self.autonomy_settings['confirmed'] = True
        return self.snapshot()

    async def clear_directives(self):
        self.cleared = True
        self.work_context = {'required_fume': False, 'declared': False}
        return {'cleared': True}

    async def set_work_context(self, required_fume):
        self.work_context_changes.append(required_fume)
        self.work_context = {'required_fume': required_fume, 'declared': True}
        return self.snapshot()

    def history_summary(self):
        return {'episodes': 12, 'source_revision': 'TEST'}

    def subscribe(self):
        queue = asyncio.Queue()
        self.queues.append(queue)
        return queue

    def unsubscribe(self, queue):
        self.queues.remove(queue)


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = FakeRuntime()
        self.client = TestClient(TestServer(create_app(self.runtime)), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        session = await self.client.get('/api/session')
        self.token = (await session.json())['token']
        self.headers = {'X-Cupol-Token': self.token}

    async def asyncTearDown(self):
        await self.client.close()
        self.assertTrue(self.runtime.closed)

    async def test_lifecycle_and_authenticated_state(self):
        self.assertTrue(self.runtime.started)
        response = await self.client.get('/api/state')
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['connection']['state'], 'disconnected')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')

    async def test_post_needs_csrf_and_exact_origin(self):
        response = await self.client.post('/api/connect', json={'address': 'ws://test'})
        self.assertEqual(response.status, 403)
        response = await self.client.post('/api/connect', json={'address': 'ws://test'},
                                          headers={**self.headers, 'Origin': 'https://evil.example'})
        self.assertEqual(response.status, 403)
        self.assertEqual(self.runtime.connected, [])
        response = await self.client.post('/api/connect', json={'address': 'ws://test'}, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(len(self.runtime.connected), 1)

    async def test_dns_rebinding_host_is_rejected(self):
        response = await self.client.get('/api/session', headers={'Host': 'evil.example'})
        self.assertEqual(response.status, 403)

    async def test_new_client_without_cookie_cannot_read_state(self):
        self.client.session.cookie_jar.clear()
        response = await self.client.get('/api/state')
        self.assertEqual(response.status, 401)

    async def test_chat_and_clear_directives(self):
        response = await self.client.post('/api/chat', json={'message': 'Покажи причины'}, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['sources'], ['doc:test'])
        self.assertEqual(self.runtime.messages, ['Покажи причины'])
        self.assertEqual(self.runtime.message_kinds, ['request'])
        response = await self.client.post('/api/directives/clear', json={}, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertTrue(self.runtime.cleared)

    async def test_chat_explicit_directive_and_invalid_kind(self):
        response=await self.client.post('/api/chat', json={'message':'Не запускай производство без разрешения',
            'message_kind':'directive'}, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.runtime.message_kinds, ['directive'])
        for kind in (None, '', 'command', [], True):
            with self.subTest(kind=kind):
                response=await self.client.post('/api/chat', json={'message':'Включи освещение',
                    'message_kind':kind}, headers=self.headers)
                self.assertEqual(response.status, 400)
        self.assertEqual(len(self.runtime.messages), 1)

    async def test_autonomy_toggle_is_explicit_session_authorization(self):
        response = await self.client.post('/api/autonomy', json={'enabled': True}, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.json())['autonomous'])
        response = await self.client.post('/api/autonomy', json={'enabled': False}, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertFalse((await response.json())['autonomous'])
        self.assertEqual(self.runtime.autonomy_changes, [True, False])
        self.assertEqual(self.runtime.controls, [])
        self.assertEqual(self.runtime.messages, [])

    async def test_autonomy_requires_exact_boolean_and_csrf(self):
        for payload in ({}, {'enabled': 1}, {'enabled': None}, {'enabled': 'true'},
                        {'enabled': True, 'operator_authorized': True}):
            with self.subTest(payload=payload):
                response = await self.client.post('/api/autonomy', json=payload, headers=self.headers)
                self.assertEqual(response.status, 400)
        response = await self.client.post('/api/autonomy', json={'enabled': True})
        self.assertEqual(response.status, 403)
        self.assertEqual(self.runtime.autonomy_changes, [])

    async def test_autonomy_configuration_saves_operator_values_without_enabling(self):
        payload = {'min_airflow_pct': 38, 'min_supply_flow_m3_h': 145.5,
                   'min_cooling_flow_l_min': 2.2, 'safety_confirmed': True}
        response = await self.client.post('/api/autonomy/config', json=payload, headers=self.headers)
        self.assertEqual(response.status, 200)
        state = await response.json()
        self.assertTrue(state['autonomy_settings']['confirmed'])
        self.assertEqual(state['autonomy_settings']['min_supply_flow_m3_h'], 145.5)
        self.assertFalse(state['autonomous'])
        self.assertEqual(self.runtime.autonomy_configurations, [payload])
        self.assertEqual(self.runtime.autonomy_changes, [])
        self.assertEqual(self.runtime.controls, [])
        self.assertEqual(self.runtime.messages, [])

    async def test_autonomy_configuration_rejects_missing_unconfirmed_or_invalid_values(self):
        valid = {'min_airflow_pct': 38, 'min_supply_flow_m3_h': 145.5,
                 'min_cooling_flow_l_min': 2.2, 'safety_confirmed': True}
        invalid = [{}, {**valid, 'extra': 1}, {**valid, 'safety_confirmed': False},
                   {**valid, 'safety_confirmed': 1}, {**valid, 'min_airflow_pct': 101},
                   {**valid, 'min_airflow_pct': True}, {**valid, 'min_supply_flow_m3_h': 0},
                   {**valid, 'min_cooling_flow_l_min': -1}, {**valid, 'min_airflow_pct': '38'},
                   {**valid, 'min_cooling_flow_l_min': float('inf')}]
        for payload in invalid:
            with self.subTest(payload=payload):
                response = await self.client.post('/api/autonomy/config', json=payload, headers=self.headers)
                self.assertEqual(response.status, 400)
        self.assertEqual(self.runtime.autonomy_configurations, [])

    async def test_autonomy_configuration_requires_session_csrf_and_same_origin(self):
        payload = {'min_airflow_pct': 38, 'min_supply_flow_m3_h': 145.5,
                   'min_cooling_flow_l_min': 2.2, 'safety_confirmed': True}
        response = await self.client.post('/api/autonomy/config', json=payload)
        self.assertEqual(response.status, 403)
        response = await self.client.post('/api/autonomy/config', json=payload,
            headers={**self.headers, 'Origin': 'https://unrelated.example'})
        self.assertEqual(response.status, 403)
        self.client.session.cookie_jar.clear()
        response = await self.client.post('/api/autonomy/config', json=payload, headers=self.headers)
        self.assertEqual(response.status, 401)
        self.assertEqual(self.runtime.autonomy_configurations, [])

    async def test_work_context_is_a_declared_future_instruction_without_commands(self):
        for value in (True, False):
            response = await self.client.post('/api/work-context', json={'required_fume': value}, headers=self.headers)
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())['work_context'], {'required_fume': value, 'declared': True})
        self.assertEqual(self.runtime.work_context_changes, [True, False])
        self.assertEqual(self.runtime.controls, [])
        self.assertEqual(self.runtime.messages, [])
        self.assertEqual(self.runtime.autonomy_changes, [])

    async def test_clearing_directives_also_clears_declared_work_context(self):
        await self.client.post('/api/work-context', json={'required_fume': True}, headers=self.headers)
        response = await self.client.post('/api/directives/clear', json={}, headers=self.headers)
        self.assertEqual(response.status, 200)
        state = await (await self.client.get('/api/state')).json()
        self.assertEqual(state['work_context'], {'required_fume': False, 'declared': False})

    async def test_work_context_requires_exact_boolean_csrf_and_origin(self):
        for payload in ({}, {'required_fume': None}, {'required_fume': 1}, {'required_fume': 'true'},
                        {'required_fume': True, 'emergency_stop': False}):
            response = await self.client.post('/api/work-context', json=payload, headers=self.headers)
            self.assertEqual(response.status, 400)
        response = await self.client.post('/api/work-context', json={'required_fume': True})
        self.assertEqual(response.status, 403)
        response = await self.client.post('/api/work-context', json={'required_fume': True},
            headers={**self.headers, 'Origin': 'https://unrelated.example'})
        self.assertEqual(response.status, 403)
        self.assertEqual(self.runtime.work_context_changes, [])

    async def test_direct_control_dispatches_only_to_manual_executor(self):
        command = {'type': 'control', 'node_id': 'smart_panel_01', 'action': 'line_on', 'value': 7}
        response = await self.client.post('/api/control', json={'request': command}, headers=self.headers)
        self.assertEqual(response.status, 200)
        result = await response.json()
        self.assertEqual(result['request'], command)
        self.assertEqual(result['status'], 'STATE_CONFIRMED')
        self.assertEqual(result['source'], 'manual_api')
        self.assertEqual(self.runtime.controls, [command])
        self.assertEqual(self.runtime.messages, [])
        self.assertEqual(self.runtime.message_kinds, [])
        state = await (await self.client.get('/api/state')).json()
        self.assertEqual(state['manual_controls']['smart_panel_01']['controls'], ['line_on', 'line_off'])

    async def test_direct_control_preserves_uncertain_or_rejected_outcomes(self):
        command = {'type': 'control', 'node_id': 'fume_extraction_01', 'action': 'turn_on'}
        for status in ('ACKNOWLEDGED_ONLY', 'UNKNOWN', 'REJECTED'):
            self.runtime.control_result = {'status': status, 'verified': False, 'response': {'success': None}}
            response = await self.client.post('/api/control', json={'request': command}, headers=self.headers)
            self.assertEqual(response.status, 200)
            result = await response.json()
            self.assertEqual(result['status'], status)
            self.assertFalse(result['verified'])
        self.assertEqual(len(self.runtime.controls), 3)
        self.assertEqual(self.runtime.messages, [])

    async def test_direct_control_requires_exact_shape_and_bounded_value(self):
        command = {'type': 'control', 'node_id': 'smart_panel_01', 'action': 'line_on', 'value': 7}
        malformed = [{}, {'request': command, 'message': 'extra'}, {'request': []},
            {'request': {**command, 'type': 'operator_control'}},
            {'request': {**command, 'request_id': 'caller-cannot-set'}},
            {'request': {'type': 'control', 'action': 'line_on'}},
            {'request': {**command, 'node_id': True}},
            {'request': {**command, 'value': 'x' * 5000}}]
        for payload in malformed:
            with self.subTest(payload_fields=list(payload)):
                response = await self.client.post('/api/control', json=payload, headers=self.headers)
                self.assertEqual(response.status, 400)
        self.assertEqual(self.runtime.controls, [])

    async def test_direct_control_requires_session_csrf_and_same_origin(self):
        payload = {'request': {'type': 'control', 'node_id': 'smart_panel_01', 'action': 'line_off', 'value': 7}}
        response = await self.client.post('/api/control', json=payload)
        self.assertEqual(response.status, 403)
        response = await self.client.post('/api/control', json=payload,
            headers={**self.headers, 'Origin': 'https://unrelated.example'})
        self.assertEqual(response.status, 403)
        self.client.session.cookie_jar.clear()
        response = await self.client.post('/api/control', json=payload, headers=self.headers)
        self.assertEqual(response.status, 401)
        self.assertEqual(self.runtime.controls, [])

    async def test_upload_transfers_file_without_starting_job(self):
        form = FormData()
        form.add_field('node_id', 'printer_3d_01')
        form.add_field('organizer_prepared', 'true')
        form.add_field('file', b'G21\nG1 X1\n', filename='part.gcode', content_type='text/plain')
        response = await self.client.post('/api/upload', data=form, headers=self.headers)
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(self.runtime.uploads, [('printer_3d_01', 'part.gcode', b'G21\nG1 X1\n', True)])
        self.assertEqual(self.runtime.jobs, [])

    async def test_oversized_file_is_rejected_before_runtime(self):
        form = FormData()
        form.add_field('node_id', 'printer_3d_01')
        form.add_field('organizer_prepared', 'true')
        form.add_field('file', io.BytesIO(b'x' * 1025), filename='part.gcode')
        with patch('kupol_agent.web.MAX_UPLOAD_BYTES', 1024):
            response = await self.client.post('/api/upload', data=form, headers=self.headers)
        self.assertEqual(response.status, 413)
        self.assertEqual(self.runtime.uploads, [])

    async def test_upload_requires_explicit_organizer_file_declaration(self):
        for declaration in (None, 'false', 'TRUE', '1'):
            form = FormData()
            form.add_field('node_id', 'printer_3d_01')
            if declaration is not None:
                form.add_field('organizer_prepared', declaration)
            form.add_field('file', b'G21\n', filename='part.gcode')
            response = await self.client.post('/api/upload', data=form, headers=self.headers)
            self.assertEqual(response.status, 400)
        self.assertEqual(self.runtime.uploads, [])

    async def test_list_and_start_are_separate_explicit_requests(self):
        response = await self.client.get('/api/files?node_id=cnc_01')
        self.assertEqual(response.status, 200)
        self.assertEqual(self.runtime.jobs, [])
        response = await self.client.post('/api/job/start', json={'node_id': 'cnc_01', 'file_name': 'part.nc'}, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.runtime.jobs, [('cnc_01', 'part.nc')])

    async def test_websocket_is_read_only_and_emits_runtime_events(self):
        socket = await self.client.ws_connect('/api/stream')
        first = await socket.receive_json(timeout=2)
        self.assertEqual(first['type'], 'state')
        await self.runtime.queues[0].put({'type': 'event', 'data': {'kind': 'diagnosis', 'message': 'Причина'}})
        event = await socket.receive_json(timeout=2)
        self.assertEqual(event['data']['message'], 'Причина')
        await socket.send_json({'type': 'control', 'action': 'line_on'})
        await socket.receive(timeout=2)
        await socket.close()
        self.assertEqual(self.runtime.connected, [])

    async def test_invalid_json_and_wrong_device_do_not_dispatch(self):
        response = await self.client.post('/api/chat', data='not JSON', headers={**self.headers, 'Content-Type': 'application/json'})
        self.assertEqual(response.status, 400)
        response = await self.client.get('/api/files?node_id=smart_panel_01')
        self.assertEqual(response.status, 400)
        self.assertEqual(self.runtime.messages, [])


if __name__ == '__main__':
    unittest.main()
