import copy
import time
import unittest

from kupol_agent.monitoring import TemporalMonitor


def frame(stamp=1000):
    def node(**fields):
        return {'online':True, 'status':'OK', 'updated_at':round(stamp,3), **fields}
    return {'timestamp':stamp, '_received_monotonic':time.monotonic(), 'data':{'nodes':{
        'solar_inverter_01':node(fan_mode='AUTO',fan_state='OFF',fan_current_a=0),
        'printer_3d_01':node(state='IDLE',real_state='IDLE',webhook_state='IDLE',progress_pct=0),
        'fuel_tank_01':node(water_content_ppm=20),
        'smart_panel_01':node(lines=[{'line':6,'state':'ON','leakage_ma':1}]),
        'climate_sensor_01':node(co2_ppm=800,sensor_state='OK'),
        'climate_sensor_02':node(co2_ppm=810,sensor_state='OK',reading_delay_s=900),
        'network_link_01':node(rx_power_dbm=-8),
    }}}


class MonitoringTests(unittest.TestCase):
    def test_rounding_and_normal_auto_fan_are_not_faults(self):
        observed = TemporalMonitor().observe(frame(1000.00075))
        self.assertEqual(observed['faults'], [])
        self.assertTrue(observed['changed'])

    def test_duplicate_and_out_of_order_do_not_mutate_window(self):
        monitor = TemporalMonitor(max_frames=3)
        monitor.observe(frame(1000))
        for stamp in (1000, 999):
            result = monitor.observe(frame(stamp))
            self.assertFalse(result['changed'])
            self.assertEqual(result['ignored'], 'DUPLICATE_OR_OUT_OF_ORDER')
        self.assertEqual(len(monitor.frames),1)
        for stamp in range(1001,1010):
            monitor.observe(frame(stamp))
        self.assertEqual(len(monitor.frames),3)

    def test_completion_conflict_and_delayed_co2_are_advisory(self):
        observed = frame()
        nodes = observed['data']['nodes']
        nodes['printer_3d_01'].update(state='COMPLETED',real_state='PRINTING',webhook_state='COMPLETED',progress_pct=33)
        nodes['climate_sensor_01']['co2_ppm']=3000
        result=TemporalMonitor().observe(observed)
        codes={f['code'] for f in result['faults']}
        self.assertIn('PRINTER_COMPLETION_CONFLICT',codes)
        self.assertIn('CO2_TIME_DISAGREEMENT',codes)
        self.assertNotIn('FALSE_ALARM',codes)
        self.assertTrue(all('evidence' in f and 'subsystems' in f for f in result['faults']))

    def test_fan_mismatch_fuel_and_critical_smoke(self):
        observed=frame()
        nodes=observed['data']['nodes']
        nodes['solar_inverter_01'].update(fan_mode='ON',fan_state='OFF',error_code='E09')
        nodes['fuel_tank_01']['water_content_ppm']=638
        nodes['smoke_detector_02']={'online':True,'status':'OK','updated_at':1000,'smoke_detected':True}
        codes={f['code']:f for f in TemporalMonitor().observe(observed)['faults']}
        for code in ('INVERTER_FAN_MISMATCH','PV_ISOLATION_ERROR','FUEL_CONTAMINATION','SMOKE_OR_FIRE'):
            self.assertIn(code,codes)
        self.assertEqual(codes['SMOKE_OR_FIRE']['severity'],'critical')

    def test_temporal_leakage_and_cooccurrence_do_not_claim_cause(self):
        monitor=TemporalMonitor()
        for i in range(4):
            observed=frame(1000+i*4)
            nodes=observed['data']['nodes']
            nodes['smart_panel_01']['lines'][0].update(state='ON' if i%2 else 'OFF', leakage_ma=10+i)
            nodes['climate_sensor_01']['sensor_state']='DEGRADED' if i%2 else 'OK'
            result=monitor.observe(observed)
        codes={f['code']:f for f in result['faults']}
        self.assertIn('LINE_LEAKAGE_RISING',codes)
        self.assertIn('LIGHT_SENSOR_COOCCURRENCE',codes)
        self.assertIn('гипотеза',codes['LIGHT_SENSOR_COOCCURRENCE']['message'])

    def test_missing_stale_null_values_and_reset(self):
        monitor=TemporalMonitor()
        self.assertEqual(monitor.observe(None)['faults'][0]['code'],'LIVE_DATA_UNAVAILABLE')
        self.assertEqual(monitor.observe({'timestamp':None,'data':{'nodes':None}})['faults'][0]['code'],'LIVE_DATA_INVALID')
        observed=frame()
        observed['_received_monotonic']=time.monotonic()-60
        observed['data']['nodes']['printer_3d_01']=None
        result=monitor.observe(observed)
        self.assertIn('LIVE_FRAME_STALE',{f['code'] for f in result['faults']})
        self.assertIn('LIVE_NODES_UNUSABLE',{f['code'] for f in result['faults']})
        monitor.reset()
        self.assertEqual(len(monitor.frames),0)
        self.assertFalse(monitor.observe(frame())['faults'])


if __name__=='__main__':
    unittest.main()
