import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from kupol_agent.control import ControlError
from kupol_agent.engine import demo_snapshot
from kupol_agent.llm import PlannerError
from kupol_agent.runtime import DomeRuntime, _AutoTransport
from kupol_agent.transport import TransportError


class FakeRetriever:
    def __init__(self):
        self.calls = []
    def retrieve(self, query, environment, limit=8, **kwargs):
        self.calls.append((query, environment))
        return [{'id':'doc:fixture','kind':'document','source':'fixture.md','text':'Аварийное освещение: линия 7; свежая телеметрия обязательна.'}]


class FakePlanner:
    def __init__(self, **kwargs):
        self.model = kwargs.get('model','deepseek-flash')
    def choose(self, report, candidates, context):
        return {'runbook_id':None,'diagnosis':'Только наблюдение','evidence_ids':[],'checks':[], 'served_model':self.model}


class FakeChat:
    calls = []
    slow_on = False
    def __init__(self, **kwargs):
        self.model = kwargs.get('model','deepseek-flash')
    def reply(self, message, history, telemetry, catalog, context, *, operator_guidance=None, message_kind='request'):
        type(self).calls.append({'message':message,'history':deepcopy(history),'context':context,
                                'guidance':deepcopy(operator_guidance),'message_kind':message_kind})
        if type(self).slow_on and 'включи' in message.casefold():
            time.sleep(.15)
        commands = []
        if message_kind == 'request' and ('включи' in message.casefold() or 'выключи' in message.casefold()):
            commands = [{'type':'control','node_id':'smart_panel_01','action':'line_off' if 'выключи' in message.casefold() else 'line_on','value':7}]
        return {'answer':'План сформирован по источнику','commands':commands,'sources':['doc:fixture'],
                'served_model':self.model,'usage':{'prompt_tokens':10,'completion_tokens':5},'latency_ms':1}
    def review_upload(self, metadata, telemetry, catalog, context, *, operator_guidance=None):
        type(self).calls.append({'upload_metadata':deepcopy(metadata),'context':context,'guidance':operator_guidance})
        return {'approved':True,'reason':'Метаданные и каталог согласованы','sources':['doc:fixture'],
                'served_model':self.model,'usage':{'prompt_tokens':10,'completion_tokens':5},'latency_ms':1}


class FakeTransport:
    instances = []
    next_role = 'team'
    reject_queries = False
    fail_after_send = False
    def __init__(self, url, key, logger=None, max_rate=4, allow_control=False):
        self.url, self.key, self.logger = url, key, logger
        self.allow_control = allow_control
        self.role, self.team = type(self).next_role, 4
        self.catalog = {
            'smart_panel_01':{'controls':['line_on','line_off'],'operator_controls':[],'service_actions':[],'queries':[]},
            'printer_3d_01':{'controls':['upload_file','start_job'],'operator_controls':[],'service_actions':[],'queries':['list_files']},
            'cnc_01':{'controls':['start_job'],'operator_controls':[],'service_actions':[],'queries':['list_files']},
        }
        self.snapshot = demo_snapshot('healthy')
        nodes=self.snapshot['data']['nodes']
        nodes['smart_panel_01']['lines'].append({'line':7,'name':'Аварийное освещение','state':'OFF',
            'voltage_v':239.2,'power_w':0,'protection_state':'ARMED','alarm':False,'lockout_remaining_s':0,'lockout_reason':None})
        nodes['printer_3d_01'].update(files=['existing.gcode'],file_name=None,homed=True,lockout_remaining_s=0)
        nodes['cnc_01'].update(files=['board.nc'],file_name=None,homed=True,lockout_remaining_s=0)
        self.latest_frame = None
        self.sent=[]
        self.queue=asyncio.Queue()
        self.closed=False
        self._heartbeat=None
        self.heartbeat_frames=0
        type(self).instances.append(self)
    async def __aenter__(self):
        self.logger('connection',event='auth',role=self.role,team=4)
        if self.role=='team':
            self.push()
        return self
    async def __aexit__(self,*args):
        self.closed=True
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            self._heartbeat=None
    def push(self, mutator=None):
        if self.closed:
            return
        if mutator:
            self.snapshot['timestamp'] += 4
            for node in self.snapshot['data']['nodes'].values():
                node['updated_at']=self.snapshot['timestamp']
            mutator(self.snapshot['data']['nodes'])
        record=deepcopy(self.snapshot)
        record['_received_monotonic']=time.monotonic()
        self.latest_frame=record
        self.queue.put_nowait(record)
    def _telemetry_tick(self):
        if self.closed:
            return
        self.heartbeat_frames+=1
        # A new sensor observation, not a replay of the previous command.
        self.push(lambda nodes:None)
        self._heartbeat=asyncio.get_running_loop().call_later(.05,self._telemetry_tick)
    async def frames(self):
        while True:
            record=await self.queue.get()
            if record is None:
                raise TransportError('Simulated disconnect')
            self.logger('incoming',payload=record)
            yield record
    async def send(self, request):
        if request['type']=='control':
            if not self.allow_control:
                raise TransportError('Writes disabled')
            self.sent.append(deepcopy(request))
            if type(self).fail_after_send:
                type(self).fail_after_send=False
                self.queue.put_nowait(None)
                raise TransportError('Connection lost after send')
            def mutate(nodes):
                if request['node_id']=='smart_panel_01':
                    next(line for line in nodes['smart_panel_01']['lines'] if line['line']==7)['state']='ON' if request['action']=='line_on' else 'OFF'
                elif request['action']=='upload_file':
                    nodes[request['node_id']]['files'].append(request['value']['file_name'])
            asyncio.get_running_loop().call_later(.01,self.push,mutate)
            if self._heartbeat is None:
                self._heartbeat=asyncio.get_running_loop().call_later(.05,self._telemetry_tick)
            return {'success':True,'type':'control_response','mode':'real'}
        if request.get('query')=='list_files':
            if type(self).reject_queries:
                return {'success':False,'error':'read denied'}
            return {'success':True,'result':list(self.snapshot['data']['nodes'][request['node_id']]['files'])}
        return {'success':True,'nodes':list(self.catalog.values())}


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_rerank_contention_does_not_delay_operator(self):
        runtime = await self.runtime()
        runtime.retriever.needs_rerank = lambda hits: True
        runtime.retriever.rerank = lambda *args: self.fail('A busy optional reranker must not be entered')
        hits = [{'id':'doc:test','text':'current local evidence'}]
        runtime._rerank_lock.acquire()
        try:
            actual = await asyncio.wait_for(asyncio.to_thread(runtime._rerank_sync, 'operator request', hits, runtime._planner), .5)
            self.assertEqual(actual, hits)
        finally:
            runtime._rerank_lock.release()

    async def test_automatic_advisory_remains_visible_until_fault_changes(self):
        runtime = await self.runtime()
        async def wait_for_analysis(expected_calls=None):
            deadline=asyncio.get_running_loop().time()+3
            while True:
                processed=(expected_calls is None or runtime._agent.process.await_count >= expected_calls)
                if processed and runtime._analysis_task is None and runtime._analysis_queue.empty():
                    return
                if asyncio.get_running_loop().time() >= deadline:
                    self.fail('Synthetic analysis pass did not complete before the bounded deadline')
                await asyncio.sleep(.005)
        await wait_for_analysis()
        runtime._agent.process = AsyncMock(return_value={'faults':[], 'outcomes':[]})
        faults = [{'code':'FAN_EFFECT_MISSING','target':'solar_inverter_01','message':'No fan effect',
                   'severity':'warning','subsystems':['energy'],'evidence':[]}]
        runtime._last_advisory_signature = (('FAN_EFFECT_MISSING','solar_inverter_01',()),)
        runtime._last_advisory = time.monotonic()
        runtime._diagnostics['advisory'] = {'answer':'Earlier grounded explanation','sources':[{'id':'doc:fixture'}]}
        runtime._analysis_queue.put_nowait((runtime._session_id, deepcopy(runtime._frame), {'faults':faults,'metrics':{},'changed':False}))
        await wait_for_analysis(1)
        self.assertEqual(runtime.snapshot()['diagnostics']['advisory']['answer'], 'Earlier grounded explanation')
        changed = deepcopy(faults)
        changed[0]['evidence'] = [{'id':'temporal:different_signal','value':1}]
        runtime._analysis_queue.put_nowait((runtime._session_id, deepcopy(runtime._frame), {'faults':changed,'metrics':{},'changed':True}))
        await wait_for_analysis(2)
        self.assertNotIn('advisory', runtime.snapshot()['diagnostics'])
        runtime._analysis_queue.put_nowait((runtime._session_id, deepcopy(runtime._frame), {'faults':[],'metrics':{},'changed':True}))
        await wait_for_analysis(3)
        self.assertNotIn('advisory', runtime.snapshot()['diagnostics'])

    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.environment=patch.dict(os.environ, {'DOME_KEY':'TEST_ONLY_DOME_PRIVATE','DEEPSEEK_API_KEY':'TEST_ONLY_DEEPSEEK_PRIVATE',
            'DOME_URL':'','DEEPSEEK_FINAL_MODEL':'','DEEPSEEK_FINAL_SERVED_MODEL':'','AGENT_LOG_DIR':str(self.root/'logs')})
        self.environment.start()
        FakeTransport.instances=[]
        FakeTransport.next_role='team'
        FakeTransport.reject_queries=False
        FakeTransport.fail_after_send=False
        FakeChat.calls=[]
        FakeChat.slow_on=False
        self.runtimes=[]
    async def asyncTearDown(self):
        for runtime in self.runtimes:
            await runtime.close()
        self.environment.stop()
        self.temp.cleanup()
    async def runtime(self, **options):
        runtime=DomeRuntime(data_dir=self.root/'data',keys_archive=self.root/'absent.zip',transport_factory=FakeTransport,
            planner_factory=FakePlanner,chat_factory=FakeChat,retriever=FakeRetriever(),reconnect_delay=.02,
            # IsolatedAsyncio runs with debug instrumentation and synchronous
            # audit I/O; allow its scheduled post-ACK frame to be consumed.
            # The production verification timeout remains unchanged at 12 s.
            verification_timeout=2.0,**options)
        self.runtimes.append(runtime)
        await runtime.start()
        await runtime.connect({'address':'192.0.2.99:8766'})
        for _ in range(100):
            if runtime.snapshot()['connection']['state'] in {'connected','waiting'} and (runtime._frame is not None or FakeTransport.next_role=='guest'):
                break
            await asyncio.sleep(.005)
        return runtime
    async def test_rag_manual_on_off_and_secret_free_journal(self):
        runtime=await self.runtime()
        on=await runtime.chat('Включи аварийное освещение')
        off=await runtime.chat('Выключи аварийное освещение')
        self.assertEqual(on['commands'][0]['status'],'STATE_CONFIRMED')
        self.assertEqual(off['commands'][0]['status'],'STATE_CONFIRMED')
        transport=FakeTransport.instances[-1]
        self.assertEqual([r['action'] for r in transport.sent],['line_on','line_off'])
        self.assertFalse(transport.allow_control)
        self.assertEqual(len(runtime.retriever.calls),2)
        self.assertTrue(FakeChat.calls[0]['context'])
        self.assertEqual(FakeChat.calls[-1]['guidance'], [])
        self.assertEqual(FakeChat.calls[-1]['history'], [])
        self.assertEqual(runtime.snapshot()['directives']['items'], [])
        self.assertEqual(runtime.snapshot()['directives']['overrides'], [])
        self.assertFalse(on['commands'][0]['stable_repair_verified'])
        text=''.join(p.read_text(encoding='utf-8') for p in (self.root/'logs').glob('*.jsonl'))
        self.assertNotIn('TEST_ONLY_DOME_PRIVATE',text)
        self.assertNotIn('TEST_ONLY_DEEPSEEK_PRIVATE',text)
        rows=[json.loads(line) for line in text.splitlines()]
        self.assertTrue(all({'ts','run_id','seq','kind'}<=row.keys() for row in rows))
        self.assertTrue(all('ref' in row for row in rows if row['kind']=='action'))
    async def test_newer_request_supersedes_slow_older_plan(self):
        runtime=await self.runtime()
        FakeChat.slow_on=True
        older=asyncio.create_task(runtime.chat('Включи аварийное освещение'))
        await asyncio.sleep(.03)
        newer=await runtime.chat('Выключи аварийное освещение')
        old_result=await older
        self.assertEqual(old_result['commands'][0]['status'],'SUPERSEDED')
        self.assertEqual(newer['commands'][0]['status'],'STATE_CONFIRMED')
        self.assertEqual([r['action'] for r in FakeTransport.instances[-1].sent],['line_off'])
    async def test_reader_continues_and_smoke_blocks_a_pending_llm_command(self):
        runtime=await self.runtime()
        FakeChat.slow_on=True
        pending=asyncio.create_task(runtime.chat('Включи аварийное освещение'))
        await asyncio.sleep(.03)
        FakeTransport.instances[-1].push(lambda nodes:nodes['smoke_detector_02'].update(smoke_detected=True))
        await asyncio.sleep(.03)
        self.assertEqual(runtime._frame['timestamp'],1004)
        self.assertTrue(runtime.snapshot()['safety']['hazard_latched'])
        with self.assertRaises(ControlError):
            await pending
        self.assertFalse(FakeTransport.instances[-1].sent)
        self.assertTrue((self.root/'data/safety.json').is_file())
    async def test_upload_is_literal_confirmed_and_never_starts_job(self):
        runtime=await self.runtime()
        result=await runtime.upload('printer_3d_01','part.gcode',b'G1 X17.5 Y23\n',organizer_prepared=True)
        self.assertTrue(result['listed'])
        self.assertFalse(result['automatic_job_started'])
        self.assertEqual([r['action'] for r in FakeTransport.instances[-1].sent],['upload_file'])
        text=''.join(p.read_text(encoding='utf-8') for p in (self.root/'logs').glob('*.jsonl'))
        self.assertNotIn('G1 X17.5',text)
        self.assertFalse((self.root/'data/directives.json').exists())
        self.assertEqual(runtime.snapshot()['directives']['items'], [])
        self.assertEqual(runtime.snapshot()['directives']['overrides'], [])
        with self.assertRaises(ControlError):
            await runtime.upload('cnc_01','board.nc',b'G0 X1\n',organizer_prepared=True)
        self.assertEqual(len(FakeTransport.instances[-1].sent),1)
    async def test_one_off_command_does_not_persist_or_reach_future_guidance(self):
        runtime=await self.runtime()
        await runtime.chat('Включи аварийное освещение')
        await runtime.chat('Что показывает телеметрия?')
        self.assertEqual(FakeChat.calls[-1]['guidance'], [])
        self.assertEqual(FakeChat.calls[-1]['history'], [])
        self.assertFalse((self.root/'data/directives.json').exists())
        self.assertEqual(runtime.snapshot()['directives']['items'], [])
        self.assertEqual(runtime.snapshot()['directives']['overrides'], [])
        self.assertFalse(runtime.snapshot()['directives']['pending'])
        runtime.autonomous=True
        runtime._planned_epoch=runtime._manual_epoch
        proxy=_AutoTransport(runtime,runtime._manual_epoch,runtime._session_id,runtime._frame['timestamp'])
        allowed, reason=proxy.can_control({'type':'control','node_id':'smart_panel_01','action':'line_off','value':7})
        self.assertFalse(allowed)
        self.assertNotEqual(reason,'PERSISTENT_OPERATOR_OVERRIDE')
        self.assertEqual([message['message_kind'] for message in runtime.snapshot()['messages']], ['request']*4)

    async def test_explicit_directive_persists_after_restart_without_executing_commands(self):
        runtime=await self.runtime()
        instruction='Не выключай аварийное освещение без нового разрешения.'
        answer=await runtime.chat(instruction, message_kind='directive')
        self.assertEqual(answer['message_kind'], 'directive')
        self.assertEqual(answer['commands'], [])
        self.assertFalse(FakeTransport.instances[-1].sent)
        self.assertEqual(FakeChat.calls[-1]['message_kind'], 'directive')
        self.assertEqual(FakeChat.calls[-1]['guidance'][0]['text'], instruction)
        stored=json.loads((self.root/'data/directives.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['items'][0]['message_kind'], 'directive')
        self.assertEqual(stored['items'][0]['text'], instruction)
        self.assertEqual(stored['overrides'], [])
        self.assertNotIn('192.0.2.99',json.dumps(stored))
        await runtime.close()
        restarted=await self.runtime()
        await restarted.chat('Что показывает телеметрия?')
        self.assertEqual(FakeChat.calls[-1]['guidance'][0]['text'], instruction)
        self.assertEqual(FakeChat.calls[-1]['history'], [])
        self.assertEqual(restarted.snapshot()['directives']['items'][0]['text'], instruction)
        await restarted.clear_directives()
        self.assertEqual(restarted.snapshot()['directives']['items'], [])

    async def test_directive_cannot_execute_even_if_client_proposes_command(self):
        runtime=await self.runtime()
        def rogue_reply(*args, **kwargs):
            return {'answer':'Unsafe proposed command', 'commands':[{'type':'control','node_id':'smart_panel_01','action':'line_on','value':7}],
                    'sources':[], 'served_model':'deepseek-flash'}
        runtime._chat_client.reply=rogue_reply
        with self.assertRaises(ControlError):
            await runtime.chat('Включи освещение и сохраняй это правило.', message_kind='directive')
        self.assertFalse(FakeTransport.instances[-1].sent)
        self.assertEqual(len(runtime.snapshot()['directives']['items']), 1)
        self.assertEqual(runtime._directive_pending, 0)

    async def test_invalid_message_kind_does_not_mutate_epoch_or_persistence(self):
        runtime=await self.runtime()
        epoch=runtime._manual_epoch
        for kind in (None, '', 'command', 'DIRECTIVE', [], True):
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    await runtime.chat('Включи аварийное освещение', message_kind=kind)
        self.assertEqual(runtime._manual_epoch, epoch)
        self.assertEqual(runtime._manual_pending, 0)
        self.assertEqual(runtime._directive_pending, 0)
        self.assertEqual(runtime.snapshot()['messages'], [])
        self.assertFalse((self.root/'data/directives.json').exists())

    async def test_direct_manual_api_confirms_effect_without_model_or_persistent_guidance(self):
        runtime=await self.runtime()
        calls=len(FakeChat.calls)
        before_stamp=runtime._frame['timestamp']
        on=await runtime.manual_control({'type':'control','node_id':'smart_panel_01','action':'line_on','value':7})
        off=await runtime.manual_control({'type':'control','node_id':'smart_panel_01','action':'line_off','value':7})
        self.assertEqual(on['status'],'STATE_CONFIRMED')
        self.assertEqual(off['status'],'STATE_CONFIRMED')
        self.assertTrue(on['response']['success'])
        self.assertTrue(off['response']['success'])
        self.assertGreater(on['observed']['updated_at'],before_stamp)
        self.assertGreater(off['observed']['updated_at'],on['observed']['updated_at'])
        self.assertEqual(next(line for line in on['observed']['lines'] if line['line']==7)['state'],'ON')
        self.assertEqual(next(line for line in off['observed']['lines'] if line['line']==7)['state'],'OFF')
        self.assertEqual(len(FakeChat.calls),calls)
        self.assertEqual(runtime.snapshot()['directives']['items'],[])
        self.assertEqual(runtime.snapshot()['directives']['overrides'],[])
        self.assertFalse((self.root/'data/directives.json').exists())
        controls=runtime.snapshot()['manual_controls']
        self.assertIn('line_on',controls['smart_panel_01']['controls'])
        self.assertNotIn('upload_file',controls['printer_3d_01']['controls'])
        text=''.join(path.read_text(encoding='utf-8') for path in (self.root/'logs').glob('*.jsonl'))
        decisions=[json.loads(line) for line in text.splitlines() if json.loads(line)['kind']=='decision']
        self.assertEqual(sum(row.get('source')=='manual_api' for row in decisions),2)

    async def test_direct_manual_api_reuses_safety_gates_and_rejects_unreviewed_commands(self):
        runtime=await self.runtime()
        epoch=runtime._manual_epoch
        for request in ({'type':'query','node_id':'smart_panel_01','query':'list_files'},
                {'type':'control','node_id':'smart_panel_01','action':'reset_interlock'},
                {'type':'control','node_id':'printer_3d_01','action':'upload_file','value':{'file_name':'a.gcode','content':'G0'}}):
            with self.assertRaises(ControlError):
                await runtime.manual_control(request)
        self.assertEqual(runtime._manual_epoch,epoch)
        runtime._hazard_latched=True
        with self.assertRaises(ControlError):
            await runtime.manual_control({'type':'control','node_id':'smart_panel_01','action':'line_on','value':7})
        runtime._hazard_latched=False
        runtime._frame['_received_monotonic']=time.monotonic()-100
        with self.assertRaises(ControlError):
            await runtime.manual_control({'type':'control','node_id':'smart_panel_01','action':'line_on','value':7})
        self.assertFalse(FakeTransport.instances[-1].sent)
        self.assertEqual(FakeChat.calls,[])
        self.assertEqual(runtime._manual_pending,0)
        self.assertFalse((self.root/'data/directives.json').exists())

    async def test_legacy_guidance_is_retained_and_labelled_without_converting_new_requests(self):
        path=self.root/'data/directives.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'schema_version':1, 'items':[{'text':'Ранее сохранённое указание', 'at':'old'}],
                                   'overrides':[{'node_id':'smart_panel_01','action':'line_on','value':7}]}),encoding='utf-8')
        runtime=await self.runtime()
        self.assertTrue(runtime.snapshot()['directives']['items'][0]['legacy'])
        self.assertEqual(len(runtime.snapshot()['directives']['overrides']), 1)
        await runtime.chat('Что показывает телеметрия?')
        self.assertEqual(len(runtime.snapshot()['directives']['items']), 1)
        self.assertEqual(len(runtime.snapshot()['directives']['overrides']), 1)
        await runtime.clear_directives()
        self.assertEqual(runtime.snapshot()['directives']['items'], [])
        self.assertEqual(runtime.snapshot()['directives']['overrides'], [])
    async def test_guest_and_unapproved_model_endpoint_do_not_write(self):
        FakeTransport.next_role='guest'
        guest=await self.runtime()
        with self.assertRaises(ControlError):
            await guest.chat('Включи аварийное освещение')
        self.assertFalse(FakeTransport.instances[-1].sent)
        await guest.close()
        FakeTransport.next_role='team'
        with patch.dict(os.environ,{'DEEPSEEK_BASE_URL':'https://unapproved.invalid'}):
            runtime=await self.runtime(profile='competition')
        calls=len(FakeChat.calls)
        with self.assertRaises(PlannerError):
            await runtime.chat('Что показывает телеметрия?')
        self.assertEqual(len(FakeChat.calls),calls)
        self.assertFalse(runtime.policy.public_status()['ready'])
        self.assertFalse(FakeTransport.instances[-1].sent)
    async def test_reconnect_does_not_retry_unknown_manual_command(self):
        runtime=await self.runtime()
        FakeTransport.fail_after_send=True
        answer=await runtime.chat('Включи аварийное освещение')
        self.assertEqual(answer['commands'][0]['status'],'UNKNOWN')
        await asyncio.sleep(.1)
        self.assertGreaterEqual(len(FakeTransport.instances),2)
        self.assertEqual(sum(len(t.sent) for t in FakeTransport.instances),1)
        text=''.join(p.read_text(encoding='utf-8') for p in (self.root/'logs').glob('*.jsonl'))
        rows=[json.loads(line) for line in text.splitlines()]
        action=next(row for row in rows if row['kind']=='action' and row.get('response',{}).get('outcome')=='UNKNOWN')
        self.assertTrue(any(row['kind']=='verification' and row.get('ref')==action['seq'] for row in rows))
    async def test_files_rejection_and_invalid_cnc_file_cannot_start(self):
        runtime=await self.runtime()
        FakeTransport.reject_queries=True
        with self.assertRaises(ControlError):
            await runtime.files('cnc_01')
        FakeTransport.reject_queries=False
        FakeTransport.instances[-1].snapshot['data']['nodes']['cnc_01']['files']=[{'name':'board.nc','valid':False}]
        with self.assertRaises(ControlError):
            await runtime.start_job('cnc_01','board.nc')
        self.assertFalse(FakeTransport.instances[-1].sent)
    async def test_executor_lease_prevents_second_runtime_same_database(self):
        runtime=await self.runtime()
        other=DomeRuntime(data_dir=self.root/'data',keys_archive=self.root/'absent.zip',
            transport_factory=FakeTransport,planner_factory=FakePlanner,chat_factory=FakeChat,retriever=FakeRetriever())
        self.runtimes.append(other)
        with self.assertRaises(RuntimeError):
            await other.start()

    async def test_disconnected_and_old_session_frames_are_not_analyzed(self):
        runtime=await self.runtime()
        await asyncio.sleep(.02)
        process=AsyncMock(return_value={'faults':[],'outcomes':[]})
        runtime._agent.process=process
        frame=deepcopy(runtime._frame)
        temporal={'faults':[],'metrics':{},'changed':False}
        runtime._connection['state']='reconnecting'
        runtime._analysis_queue.put_nowait((runtime._session_id,frame,temporal))
        await asyncio.sleep(.02)
        self.assertEqual(process.await_count,0)
        runtime._connection['state']='connected'
        runtime._analysis_queue.put_nowait((runtime._session_id-1,frame,temporal))
        await asyncio.sleep(.02)
        self.assertEqual(process.await_count,0)
        runtime._analysis_queue.put_nowait((runtime._session_id,frame,temporal))
        await asyncio.sleep(.02)
        self.assertEqual(process.await_count,1)
        FakeTransport.instances[-1].push(lambda nodes:None)
        await asyncio.sleep(.02)
        self.assertEqual(runtime._frame['timestamp'],1004)
        runtime._analysis_queue.put_nowait((runtime._session_id,deepcopy(runtime._frame),temporal))
        await runtime.disconnect()
        self.assertTrue(runtime._analysis_queue.empty())
        await runtime.connect({'address':'192.0.2.100:8766'})
        for _ in range(100):
            if runtime._frame is not None:
                break
            await asyncio.sleep(.005)
        # A new server/session can restart its clock; an old timestamp must not
        # cause its first legitimate frame to be rejected as out of order.
        self.assertEqual(runtime._frame['timestamp'],1000)
        self.assertEqual(runtime._frame_session_id,runtime._session_id)

    async def test_profile_change_recovers_that_environment_pending_attempt(self):
        runtime=await self.runtime()
        environment='live:competition:team4'
        incident=runtime._store.open_incident('test-new-profile','TEST_PENDING',['power_distribution'],[],environment,1000)
        attempt=runtime._store.begin_attempt(incident,'test_manual','1.0',environment,'execute',
            [{'type':'control','node_id':'smart_panel_01','action':'line_on','value':7}],1000)
        self.assertEqual(runtime._store.attempts_for_incident(incident)[0]['state'],'PENDING')
        await runtime.connect({'address':'192.0.2.100:8766','profile':'competition'})
        self.assertEqual(runtime._store.attempts_for_incident(incident)[0]['state'],'UNKNOWN')
        self.assertEqual(runtime.environment,environment)
        self.assertFalse(any(t.sent for t in FakeTransport.instances))

    async def test_upload_ack_survives_followup_listing_failure_without_resend(self):
        runtime=await self.runtime()
        FakeTransport.reject_queries=True
        result=await runtime.upload('printer_3d_01','part.gcode',b'G1 X17.5\n',organizer_prepared=True)
        self.assertTrue(result['response']['success'])
        self.assertEqual(result['status'],'ACKNOWLEDGED_ONLY')
        self.assertFalse(result['listed'])
        self.assertFalse(result['verified'])
        self.assertIn('read denied',result['list_error'])
        self.assertFalse(result['automatic_job_started'])
        self.assertEqual([r['action'] for r in FakeTransport.instances[-1].sent],['upload_file'])

    async def test_failed_startup_releases_resources_and_can_be_retried(self):
        def broken_planner(**kwargs):
            raise PlannerError('fixture startup failure')
        runtime=DomeRuntime(data_dir=self.root/'data',keys_archive=self.root/'absent.zip',
            transport_factory=FakeTransport,planner_factory=broken_planner,chat_factory=FakeChat,retriever=FakeRetriever())
        self.runtimes.append(runtime)
        with self.assertRaises(PlannerError):
            await runtime.start()
        failed_run=runtime._run_id
        self.assertFalse(runtime._started)
        self.assertIsNone(runtime._store)
        self.assertIsNone(runtime._log)
        self.assertIsNone(runtime._lease)
        runtime.planner_factory=FakePlanner
        await runtime.start()
        self.assertTrue(runtime._started)
        self.assertNotEqual(runtime._run_id,failed_run)

    async def test_startup_connect_failure_cancels_workers_and_releases_lease(self):
        runtime=DomeRuntime(data_dir=self.root/'data',keys_archive=self.root/'absent.zip',
            transport_factory=FakeTransport,planner_factory=FakePlanner,chat_factory=FakeChat,retriever=FakeRetriever())
        self.runtimes.append(runtime)
        with patch.dict(os.environ,{'DOME_URL':'http://invalid-scheme'}):
            with self.assertRaises(ValueError):
                await runtime.start()
        self.assertFalse(runtime._started)
        self.assertIsNone(runtime._worker_task)
        self.assertIsNone(runtime._watchdog_task)
        self.assertIsNone(runtime._log)
        self.assertIsNone(runtime._store)
        self.assertIsNone(runtime._lease)
        await runtime.start()
        self.assertTrue(runtime._started)
