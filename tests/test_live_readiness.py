"""Safe GUI configuration, session revocation and reported diesel faults."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from kupol_agent.control import ControlError
from kupol_agent.engine import Agent, COMMANDS, Config, MockTransport, demo_snapshot, diagnose
from kupol_agent.memory import CaseStore
from kupol_agent.monitoring import TemporalMonitor
from kupol_agent.retrieval import DocumentIndex, HybridRetriever
from kupol_agent.runtime import _AutoTransport
from kupol_agent.transport import LiveTransport, TransportError
import test_runtime as runtime_fixtures
from test_runtime import FakeChat, FakeTransport
from test_transport import FakeSocket


CRITERIA={'min_airflow_pct':10,'min_supply_flow_m3_h':100,'min_cooling_flow_l_min':2,'safety_confirmed':True}


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture=runtime_fixtures.RuntimeTests(methodName='test_rag_manual_on_off_and_secret_free_journal')
        await self.fixture.asyncSetUp()
        self.runtime=await self.fixture.runtime()

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    async def test_explicit_configuration_saves_only_numbers_and_does_not_enable_or_write(self):
        runtime=self.runtime
        self.assertIsNone(runtime.context.get('emergency_stop'))
        runtime._frame['context']={'trusted':True,'emergency_stop':False,'approved_live':True,
                                   '_session_execution_approved':True}
        with self.assertRaises(ValueError):
            await runtime.set_autonomy(True)
        result=await runtime.configure_autonomy(dict(CRITERIA))
        self.assertTrue(result['autonomy_settings']['confirmed'])
        self.assertFalse(result['autonomous'])
        self.assertTrue(runtime.config.approved_live)
        self.assertIs(runtime.context['emergency_stop'],False)
        self.assertNotIn('source_origins',runtime.context)
        self.assertIsNone(runtime.config.source_origins)
        stored=json.loads((self.fixture.root/'data/autonomy_settings.json').read_text(encoding='utf-8'))
        self.assertEqual(set(stored),set(CRITERIA)-{'safety_confirmed'})
        self.assertNotIn('approved_live',stored)
        self.assertFalse(FakeTransport.instances[-1].sent)
        self.assertEqual(FakeChat.calls,[])
        await runtime.set_autonomy(True)
        self.assertTrue(runtime.autonomous)
        self.assertEqual(len(COMMANDS),5)
        proxy=_AutoTransport(runtime,runtime._manual_epoch,runtime._session_id,runtime._frame['timestamp'])
        allowed,_=proxy.can_control({'type':'control','node_id':'dizel_1','action':'turn_on'})
        self.assertFalse(allowed)

    async def test_invalid_criteria_or_unconfirmed_safety_never_grants_approval(self):
        invalid=[]
        for value in (True,0,-1,'10',float('nan'),float('inf')):
            invalid.append({**CRITERIA,'min_supply_flow_m3_h':value})
        invalid.extend([{**CRITERIA,'min_airflow_pct':101},{**CRITERIA,'safety_confirmed':False},
                        {**CRITERIA,'emergency_stop':False},{'safety_confirmed':True}])
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    await self.runtime.configure_autonomy(payload)
        self.assertFalse(self.runtime.config.approved_live)
        self.assertFalse((self.fixture.root/'data/autonomy_settings.json').exists())

    async def test_unknown_smoke_plc_malformed_data_and_explicit_stop_block_confirmation(self):
        runtime=self.runtime
        base=deepcopy(runtime._frame)
        mutations=[lambda frame:frame['data']['nodes']['smoke_detector_01'].update(smoke_detected=None),
            lambda frame:frame['data']['nodes']['smoke_detector_02'].update(status='UNKNOWN'),
            lambda frame:frame['data']['nodes']['dome_automation_01'].update(interlocks={}),
            lambda frame:frame['data']['nodes']['dome_automation_01']['interlocks']['IL_SMOKE'].update(state='TRIPPED'),
            lambda frame:frame.update(data=None),lambda frame:frame.update(data=[])]
        for mutation in mutations:
            runtime._frame=deepcopy(base)
            mutation(runtime._frame)
            with self.assertRaises(ValueError):
                await runtime.configure_autonomy(dict(CRITERIA))
        runtime._frame=base
        runtime.context['emergency_stop']=True
        with self.assertRaises(ValueError):
            await runtime.configure_autonomy(dict(CRITERIA))
        self.assertIs(runtime.context['emergency_stop'],True)
        self.assertFalse(runtime.config.approved_live)
        self.assertFalse(FakeTransport.instances[-1].sent)

    async def test_disconnect_and_restart_retain_numbers_but_never_confirmation_or_rearming(self):
        runtime=self.runtime
        await runtime.configure_autonomy(dict(CRITERIA))
        await runtime.set_autonomy(True)
        old=_AutoTransport(runtime,runtime._manual_epoch,runtime._session_id,runtime._frame['timestamp'])
        await runtime.disconnect()
        self.assertFalse(runtime.autonomous)
        self.assertFalse(runtime.config.approved_live)
        self.assertIsNone(runtime.context.get('emergency_stop'))
        self.assertFalse(old.allow_control)
        await runtime.connect({'address':'192.0.2.10:8766','autonomous':True})
        for _ in range(100):
            if runtime._frame is not None:
                break
            await asyncio.sleep(.005)
        self.assertFalse(runtime.autonomous)
        with self.assertRaises(ValueError):
            await runtime.set_autonomy(True)
        await runtime.close()
        restarted=await self.fixture.runtime()
        self.assertEqual(restarted.config.min_airflow_pct,10)
        self.assertFalse(restarted.config.approved_live)
        self.assertFalse(restarted.snapshot()['autonomy_settings']['confirmed'])

    async def test_session_change_during_configuration_preemption_invalidates_confirmation(self):
        async def changed():
            self.runtime._session_id+=1
            self.runtime._frame_session_id=self.runtime._session_id
        self.runtime._preempt_analysis=changed
        with self.assertRaises(ValueError):
            await self.runtime.configure_autonomy(dict(CRITERIA))
        self.assertFalse(self.runtime.config.approved_live)
        self.assertFalse(self.runtime.snapshot()['autonomy_settings']['confirmed'])

    async def test_work_context_is_one_persistent_human_item_without_model_or_commands(self):
        runtime=self.runtime
        for value in (True,False,True):
            await runtime.set_work_context(value)
        self.assertEqual(runtime.snapshot()['work_context'],{'required_fume':True,'declared':True})
        self.assertEqual(sum(item.get('context_kind')=='work' for item in runtime._directives),1)
        self.assertEqual(FakeChat.calls,[])
        self.assertFalse(FakeTransport.instances[-1].sent)
        await runtime.close()
        restarted=await self.fixture.runtime()
        self.assertEqual(restarted.snapshot()['work_context'],{'required_fume':True,'declared':True})
        await restarted.clear_directives()
        self.assertEqual(restarted.snapshot()['work_context'],{'required_fume':False,'declared':False})
        for value in (1,None,'true'):
            with self.assertRaises(ValueError):
                await restarted.set_work_context(value)

    async def test_short_reported_generator_failure_is_journalled_without_a_repair_claim(self):
        runtime=self.runtime
        runtime._manual_pending=1  # Keep optional model analysis out of this short pulse.
        frame=deepcopy(runtime._frame)
        frame['timestamp']+=4
        for node in frame['data']['nodes'].values():
            node['updated_at']=frame['timestamp']
        frame['data']['nodes']['dizel_1']={'online':True,'status':'OK','updated_at':frame['timestamp'],
            'state':'FAULT','fault_code':'START_FAILURE','fault_reason':'CRANK','start_attempts':2,'lockout_remaining_s':0}
        stale=deepcopy(frame)
        stale['_received_monotonic']=time.monotonic()-60
        await runtime._consume(stale)
        self.assertFalse(any(row['fault_code']=='GENERATOR_START_FAILURE' for row in runtime._store.recent_incidents()))
        frame['timestamp']+=4
        frame['_received_monotonic']=time.monotonic()
        for node in frame['data']['nodes'].values():
            node['updated_at']=frame['timestamp']
        await runtime._consume(frame)
        self.assertEqual(sum(fault['code']=='GENERATOR_START_FAILURE' for fault in runtime.snapshot()['diagnostics']['faults']),1)
        frame=deepcopy(frame)
        frame['timestamp']+=4
        for node in frame['data']['nodes'].values():
            node['updated_at']=frame['timestamp']
        frame['data']['nodes']['dizel_1'].update(state='ON',fault_code=None,fault_reason=None)
        await runtime._consume(frame)
        runtime._manual_pending=0
        incident=next(row for row in runtime._store.recent_incidents() if row['fault_code']=='GENERATOR_START_FAILURE')
        self.assertEqual(incident['state'],'CONDITION_CLEARED_UNATTRIBUTED')
        self.assertEqual(runtime._store.attempts_for_incident(incident['id']),[])
        self.assertFalse(FakeTransport.instances[-1].sent)

    async def test_post_limiter_guard_rechecks_latest_safety_and_all_fume_invariants(self):
        runtime=self.runtime
        original=runtime._transport
        socket=FakeSocket()
        transport=LiveTransport('ws://synthetic.invalid:8766','LOCAL_SYNTHETIC_ONLY',max_rate=5)
        with patch.object(transport,'_connect',return_value=socket):
            await transport.__aenter__()
        runtime._transport=transport
        base=deepcopy(runtime._frame)
        transport.latest_frame=deepcopy(base)
        try:
            await runtime.configure_autonomy(dict(CRITERIA))
            await runtime.set_work_context(True)
            await runtime.set_autonomy(True)
            runtime._planned_epoch=runtime._manual_epoch
            original_proxy=_AutoTransport(runtime,runtime._manual_epoch,runtime._session_id,base['timestamp'])
            with self.assertRaises(ControlError):
                await original_proxy.send({'type':'control','node_id':'fume_extraction_01',
                    'action':'turn_on','request_id':'forged-original-field'})
            mutations=[lambda:runtimes_mutate_latest(transport,'plc'),lambda:runtimes_mutate_latest(transport,'smoke'),
                lambda:runtimes_mutate_latest(transport,'alarm'),lambda:runtimes_mutate_latest(transport,'motor'),
                lambda:runtimes_mutate_latest(transport,'power'),lambda:setattr(runtime,'_hazard_latched',True),
                lambda:setattr(runtime,'_manual_epoch',runtime._manual_epoch+1),lambda:runtime.context.update(emergency_stop=True)]
            for mutation in mutations:
                runtime._hazard_latched=False
                runtime.context['emergency_stop']=False
                runtime._frame=deepcopy(base)
                transport.latest_frame=deepcopy(base)
                transport._last_send=time.monotonic()  # A recent send forces the normal limiter wait.
                runtime._planned_epoch=runtime._manual_epoch
                proxy=_AutoTransport(runtime,runtime._manual_epoch,runtime._session_id,base['timestamp'])
                calls=[]
                original_guard=proxy._pre_send_guard
                def counted_guard(request):
                    calls.append(1)
                    return original_guard(request)
                proxy._pre_send_guard=counted_guard
                changed=[]
                def change():
                    changed.append(1)
                    mutation()
                asyncio.get_running_loop().call_later(.01,change)
                with self.assertRaises(TransportError):
                    await proxy.send({'type':'control','node_id':'fume_extraction_01','action':'turn_on'})
                self.assertTrue(changed)
                self.assertGreaterEqual(len(calls),2)
                self.assertIsNone(transport.control_guard)
                self.assertFalse(transport.allow_control)
            self.assertEqual(sum(request['type']=='control' for _,request in socket.sent),0)
        finally:
            runtime._transport=original
            await transport.__aexit__(None,None,None)

    async def test_memory_environment_separates_user_designated_virtual_host_without_storing_ip(self):
        runtime=self.runtime
        self.assertEqual(runtime.environment,'live:training:team4')
        runtime._connection['address']='wss://janus-server.a.2na.am:443'
        self.assertEqual(runtime.environment,'virtual:training:team4')
        self.assertNotIn('janus',runtime.environment)
        self.assertNotIn('192.',runtime.environment)


def runtimes_mutate_latest(transport,kind):
    nodes=transport.latest_frame['data']['nodes']
    if kind=='plc':
        nodes['dome_automation_01']['interlocks']['IL_CO2']['state']='PENDING'
    elif kind=='smoke':
        nodes['smoke_detector_01']['smoke_detected']=True
    elif kind=='alarm':
        nodes['fume_extraction_01']['alarm']=True
    elif kind=='motor':
        nodes['fume_extraction_01']['motor_state']='FAULT'
    else:
        nodes['smart_panel_01']['lines'][0]['state']='OFF'


class GeneratorDiagnosisTests(unittest.IsolatedAsyncioTestCase):
    async def test_generator_fault_is_escalation_only_even_in_execute_mode(self):
        frame=demo_snapshot('healthy')
        frame['data']['nodes']['dizel_1']={'online':True,'status':'OK','updated_at':1000,
            'state':'FAULT','fault_code':'START_FAILURE','fault_reason':'CRANK','start_attempts':2,'lockout_remaining_s':0}
        store=CaseStore(':memory:')
        retriever=HybridRetriever(DocumentIndex([Path(__file__).parents[1]/'kupol_agent/knowledge/runbooks.md']),store)
        agent=Agent(store,retriever,config=Config(approved_live=True),environment='simulation',mode='execute')
        transport=MockTransport(frame)
        transport.allow_control=True
        try:
            report=await agent.process(frame,transport)
            fault=next(item for item in report['faults'] if item['code']=='GENERATOR_START_FAILURE')
            self.assertEqual(fault['selected_runbook'],'inspect_generator_start_failure')
            self.assertEqual(fault['severity'],'warning')
            self.assertEqual(fault['proposed_actions'],[])
            self.assertEqual(transport.sent,[])
            self.assertIn('CRANK обозначает этап',fault['message'])
            self.assertTrue(any('inspect_generator_start_failure' in item['text'] for item in fault['retrieved']))
            self.assertEqual(store.stats(fault['code'],'inspect_generator_start_failure','1.0','simulation')['verified_attempts'],0)
        finally:
            await agent.shutdown()
            store.close()

    async def test_only_fresh_reported_failure_is_diagnosed_and_power_loss_raises_priority(self):
        frame=demo_snapshot('healthy')
        nodes=frame['data']['nodes']
        nodes['dizel_1']={'online':True,'status':'OK','updated_at':1000,'state':'FAULT','fault_code':'START_FAILURE','fault_reason':'CRANK'}
        self.assertEqual(next(f for f in TemporalMonitor().observe(frame)['faults'] if f['code']=='GENERATOR_START_FAILURE')['severity'],'warning')
        nodes['smart_panel_01']['bus_powered']=False
        self.assertEqual(next(f for f in diagnose(nodes,1000,{},Config()) if f['code']=='GENERATOR_START_FAILURE')['severity'],'critical')
        nodes['dizel_1']['updated_at']=900
        self.assertFalse(any(f['code']=='GENERATOR_START_FAILURE' for f in diagnose(nodes,1000,{},Config())))


if __name__=='__main__':
    unittest.main()
