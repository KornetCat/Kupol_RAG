import json
from pathlib import Path
import tempfile
import unittest

from kupol_agent.history import compile_history, detect_historical_conditions, load_manifest, normalize_snapshot


class HistoryTests(unittest.TestCase):
    def _fixture(self, directory):
        source = Path(directory) / 'source'
        day = source / 'Logs/2026-10-14'
        day.mkdir(parents=True)
        return source, day

    def _write(self, path, records):
        path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in records) + '\n', encoding='utf-8')

    def test_historical_and_live_formats_and_bad_input(self):
        historical = normalize_snapshot({'ts': '2026-10-14T00:00:00', 'nodes': {'n': {'state': 'ON'}}})
        live = normalize_snapshot({'timestamp': historical['timestamp'], 'data': {'nodes': {'n': {'state': 'ON'}}}})
        self.assertEqual(historical['nodes'], live['nodes'])
        self.assertIsNone(normalize_snapshot({'nodes': None, 'ts': 'bad'}))
        self.assertIsNone(normalize_snapshot([]))

    def test_temporal_association_never_becomes_verified_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            source, day = self._fixture(directory)
            rows = []
            for minute, error in ((0, False), (1, True), (2, True), (3, False)):
                rows.append({'ts': f'2026-10-14T00:0{minute}:00', 'nodes': {
                    'solar_inverter_01': {'online': True, 'status': 'OK', 'error': error,
                                           'error_code': 'E09' if error else None}}})
            self._write(day/'telemetry.jsonl', rows)
            self._write(day/'events.jsonl', [{'ts':'2026-10-14T00:02:10', 'kind':'action',
                'node_id':'solar_inverter_01', 'action':'reset_error', 'success':True}])
            self._write(day/'logbook.jsonl', [{'ts':'2026-10-14T18:00:00', 'kind':'logbook',
                'text':'После reset_error генерация пошла. Это рассказ, а не проверенный вывод.'}])
            output = Path(directory)/'compiled'
            result = compile_history(source, output_dir=output, source_revision='test-sha')
            self.assertEqual(result['counts']['telemetry_rows'], 4)
            episode = next(ep for ep in result['episodes'] if ep['code'] == 'INVERTER_ERROR')
            self.assertEqual(episode['outcome'], 'CONDITION_CLEARED')
            self.assertEqual(episode['condition_clear_observations'], 1)
            self.assertFalse(episode['learning_eligible'])
            self.assertEqual(episode['repair_causality'], 'UNPROVEN')
            self.assertEqual(episode['historical_actions'][0]['action'], 'reset_error')
            self.assertEqual(episode['first']['source']['line'], 2)
            self.assertEqual(len(episode['first']['source']['file_sha256']), 64)
            self.assertEqual(episode['source_revision'], 'test-sha')
            self.assertFalse(result['narratives'][0]['instruction_authority'])
            self.assertIn('UNPROVEN', next((output/'history_cases').glob('case_*.md')).read_text(encoding='utf-8'))
            self.assertEqual(load_manifest(output/'history_manifest.json')['counts']['episodes'], 1)

    def test_malformed_null_nonfinite_and_out_of_order_rows_are_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            source, day = self._fixture(directory)
            (day/'telemetry.jsonl').write_text(
                '{bad}\n' + json.dumps({'ts':'2026-10-14T00:02:00','nodes':{'n':None}}) + '\n' +
                json.dumps({'ts':'2026-10-14T00:01:00','nodes':{'n':{}}}) + '\n' +
                json.dumps({'ts':'2026-10-14T00:03:00','nodes':None}) + '\n' +
                '{"ts":"2026-10-14T00:04:00","nodes":{"x":{"value":NaN}}}\n', encoding='utf-8')
            self._write(day/'events.jsonl', [])
            self._write(day/'logbook.jsonl', [])
            result = compile_history(source, output_dir=Path(directory)/'compiled')
            self.assertEqual(result['counts']['invalid_rows'], 2)
            self.assertEqual(result['counts']['out_of_order'], 1)
            self.assertEqual(result['counts']['unusable_snapshots'], 1)
            self.assertEqual(result['counts']['telemetry_rows'], 1)

    def test_rounding_tolerance_and_malformed_optional_values(self):
        nodes = {'x': {'online':True,'status':'OK','updated_at':1000.001},
                 'printer_3d_01': {'state':[], 'webhook_state':'COMPLETED'},
                 'supply_ventilation_01': {'filter_state':{}, 'motor_state':[], 'co2_ppm':None}}
        conditions = detect_historical_conditions(nodes, 1000.00075)
        self.assertNotIn('DATA_UNAVAILABLE:x', conditions)
        self.assertIsInstance(conditions, dict)

    def test_data_gap_is_not_a_cleared_condition(self):
        with tempfile.TemporaryDirectory() as directory:
            source, day = self._fixture(directory)
            self._write(day/'telemetry.jsonl', [
                {'ts':'2026-10-14T00:00:00','nodes':{'battery_01':{'soc_pct':20}}},
                {'ts':'2026-10-14T00:20:00','nodes':{'battery_01':{'soc_pct':80}}}])
            self._write(day/'events.jsonl', [])
            self._write(day/'logbook.jsonl', [])
            result = compile_history(source, output_dir=Path(directory)/'compiled', max_episodes=1)
            self.assertEqual(result['episodes'][0]['outcome'], 'DATA_GAP')
            self.assertEqual(result['episodes'][0]['condition_clear_observations'], 0)
            self.assertLessEqual(result['counts']['rag_cases'], 1)

    def test_secret_fields_and_unsupported_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            source, day = self._fixture(directory)
            self._write(day/'telemetry.jsonl', [{'ts':'2026-10-14T00:00:00', 'nodes': {
                'solar_inverter_01': {'error':True,'error_code':'E09'}}}])
            self._write(day/'events.jsonl', [{'ts':'2026-10-14T00:00:00','kind':'action',
                'node_id':'solar_inverter_01','action':'reset_error',
                'value':{'api_key':'DO_NOT_STORE_THIS','nested':'sk-abcdefghijk12345'}}])
            self._write(day/'logbook.jsonl', [])
            output = Path(directory)/'compiled'
            compile_history(source, output_dir=output)
            content = (output/'history_manifest.json').read_text(encoding='utf-8')
            self.assertNotIn('DO_NOT_STORE_THIS', content)
            self.assertNotIn('sk-abcdefghijk12345', content)
            unknown = Path(directory)/'unknown.json'
            unknown.write_text('{"schema_version":99}', encoding='utf-8')
            self.assertTrue(load_manifest(unknown)['warnings'])

    def test_short_event_alarm_between_snapshots_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            source, day = self._fixture(directory)
            self._write(day/'telemetry.jsonl', [
                {'ts':'2026-10-14T00:00:00','nodes':{'smoke_detector_02':{'smoke_detected':False}}},
                {'ts':'2026-10-14T00:01:00','nodes':{'smoke_detector_02':{'smoke_detected':False}}}])
            self._write(day/'events.jsonl', [
                {'ts':'2026-10-14T00:00:10','kind':'change','node_id':'smoke_detector_02','param':'smoke_detected','from':False,'to':True},
                {'ts':'2026-10-14T00:00:30','kind':'change','node_id':'smoke_detector_02','param':'smoke_detected','from':True,'to':False}])
            self._write(day/'logbook.jsonl', [])
            result = compile_history(source, output_dir=Path(directory)/'compiled')
            episode = result['episodes'][0]
            self.assertTrue(episode['event_only'])
            self.assertEqual(episode['duration_s'], 20)
            self.assertEqual(episode['outcome'], 'EVENT_SIGNAL_CLEARED')
            self.assertEqual(episode['first']['source']['file'], 'Logs/2026-10-14/events.jsonl')
            self.assertFalse(episode['learning_eligible'])

    def test_lost_target_does_not_count_as_condition_cleared(self):
        variants = [
            ({}, 'TARGET_MISSING'),
            ({'solar_inverter_01':None}, 'TARGET_MISSING'),
            ({'solar_inverter_01':{'online':False,'status':'OK','error':False}}, 'TARGET_OFFLINE'),
            ({'solar_inverter_01':{'online':True,'status':'UNKNOWN','error':False}}, 'TARGET_STATUS_UNKNOWN'),
        ]
        identities = []
        for after_nodes, reason in variants:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                source, day = self._fixture(directory)
                self._write(day/'telemetry.jsonl', [
                    {'ts':'2026-10-14T00:00:00','nodes':{'solar_inverter_01':{
                        'online':True,'status':'OK','error':True,'error_code':'E09'}}},
                    {'ts':'2026-10-14T00:01:00','nodes':after_nodes}])
                self._write(day/'events.jsonl', [])
                self._write(day/'logbook.jsonl', [])
                result = compile_history(source, output_dir=Path(directory)/'compiled', source_revision='fixture-revision')
                episode = next(ep for ep in result['episodes'] if ep['code']=='INVERTER_ERROR')
                identities.append(episode['id'])
                self.assertEqual(episode['outcome'],'TARGET_UNOBSERVABLE')
                self.assertEqual(episode['unobservable_reason'],reason)
                self.assertEqual(episode['condition_clear_observations'],0)
                self.assertFalse(episode['learning_eligible'])
                self.assertEqual(episode['first']['source']['line'],1)
                self.assertEqual(episode['after']['source']['line'],2)
        # Episode identity is anchored to its initial evidence, not a later
        # missing/offline value or the generated directory name.
        self.assertEqual(len(set(identities)),1)


if __name__ == '__main__':
    unittest.main()
