"""A synthetic non-failure trend; no network, real telemetry or controls."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
from kupol_agent.runtime import DomeRuntime
from test_runtime import FakeChat,FakePlanner,FakeTransport


async def ready(runtime):
    await runtime.start()
    runtime.retriever.needs_rerank=lambda hits:False
    await runtime.connect({'address':'ws://synthetic.invalid:8766'})
    for _ in range(300):
        if runtime._frame is not None:
            return
        await asyncio.sleep(.005)
    raise AssertionError('No synthetic frame')


async def probe():
    with tempfile.TemporaryDirectory() as folder:
        base=Path(folder)
        with patch.dict(os.environ,{'DOME_KEY':'SYNTHETIC_ONLY','DEEPSEEK_API_KEY':'SYNTHETIC_ONLY_MODEL',
                'DOME_URL':'','AGENT_LOG_DIR':str(base/'logs')}):
            FakeTransport.instances=[]
            FakeTransport.next_role='team'
            FakeTransport.reject_queries=False
            FakeTransport.fail_after_send=False
            FakeChat.calls=[]
            FakeChat.slow_on=False
            def make():
                return DomeRuntime(data_dir=base/'data', keys_archive=base/'absent.zip',
                    transport_factory=FakeTransport,planner_factory=FakePlanner,chat_factory=FakeChat,reconnect_delay=.01)
            runtime=make()
            try:
                await ready(runtime)
                base_frame=deepcopy(runtime._frame)
                detected=[]
                currents=[.21,.24,.27,.30]
                for index,current in enumerate(currents,1):
                    frame=deepcopy(base_frame)
                    frame['timestamp']=1000+index*4
                    frame['_received_monotonic']=time.monotonic()
                    for node in frame['data']['nodes'].values():
                        node['updated_at']=frame['timestamp']
                    frame['data']['nodes']['solar_inverter_01'].update(fan_mode='ON',fan_state='ON',
                        fan_current_a=current,fan_rpm=1800,inverter_temp_c=40,load_power_w=200)
                    await runtime._consume(frame)
                    detected=[fault for fault in runtime._temporal_result['faults']
                              if fault['code']=='INVERTER_FAN_CURRENT_RISING'] or detected
                for index in range(5,8):
                    frame=deepcopy(frame)
                    frame['timestamp']=1000+index*4
                    frame['_received_monotonic']=time.monotonic()
                    for node in frame['data']['nodes'].values():
                        node['updated_at']=frame['timestamp']
                    frame['data']['nodes']['solar_inverter_01']['fan_current_a']=.30
                    await runtime._consume(frame)
                cleared=not any(f['code']=='INVERTER_FAN_CURRENT_RISING' for f in runtime._temporal_result['faults'])
                question='Какие ранее зафиксированы наблюдения о токе вентилятора solar_inverter_01?'
                await runtime.chat(question)
                call=next(item for item in reversed(FakeChat.calls) if item.get('message')==question)
                context_before_restart=any(item.get('kind')=='observation' for item in call['context'])
                observed_memory_supported=callable(getattr(runtime._store,'search_observations',None))
                await runtime.close()
                runtime=make()
                await ready(runtime)
                await runtime.chat(question)
                call=next(item for item in reversed(FakeChat.calls) if item.get('message')==question)
                context_after_restart=any(item.get('kind')=='observation' for item in call['context'])
                records=runtime._store.search_observations('solar_inverter_01',runtime.environment) if observed_memory_supported else []
                return {'scope':'isolated_synthetic_no_failure_trend','network_calls':0,'provider_calls':0,
                    'equipment_controls':sum(len(item.sent) for item in FakeTransport.instances),
                    'series':{'fan_current_a':currents,'fan_state':'ON','fan_mode':'ON',
                              'fan_rpm':1800,'inverter_temp_c':40,'load_power_w':200},
                    'warning_detected':bool(detected),'warning_severity':detected[-1]['severity'] if detected else None,
                    'warning_cleared_after_flat_samples':cleared,'observational_memory_supported':observed_memory_supported,
                    'observation_in_next_chat_context':context_before_restart,
                    'observation_in_chat_after_restart':context_after_restart,
                    'retained_observation_count':len(records),
                    'rag_observation_text_bytes':sum(len(row['text'].encode('utf-8')) for row in records),
                    'stored_outcomes':[row['outcome'] for row in records],
                    'repair_case_count':len(runtime._store.search_cases('fan_current_a',runtime.environment))}
            finally:
                await runtime.close()


if __name__=='__main__':
    result=asyncio.run(probe())
    path=ROOT/'output/precursor_test_result.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    existing=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    phase='after'
    existing.update(generated_at=datetime.now(timezone.utc).isoformat(),**{phase:result})
    path.write_text(json.dumps(existing,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
