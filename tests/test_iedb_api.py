from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error

from epitope_screening.core import Settings, Cancelled
from epitope_screening.iedb_api import (APIClient, APIError, PredictionSettings, build_payload,
                                      parse_prediction, predict_batch, run_online)

ROOT = Path(__file__).resolve().parents[1]
SEQUENCE = 'MGQIVTMFEALPHIIDEVINIVIIVLIVITGI'
SMALL = PredictionSettings(alleles_i=['HLA-A*02:01'], alleles_ii=['HLA-DRB1*01:01'], min_length_i=9, max_length_i=9)


def fixture(name):
    return json.loads((ROOT / 'tests/fixtures' / (name + '.json')).read_text(encoding='utf-8'))


class FakeClient:
    def __init__(self):
        self.posts = []
        self.gets = []

    def get(self, path, stop=None):
        self.gets.append(path)
        if path in ['/mhci', '/mhcii']:
            return {'predictors': []}
        return fixture(path.removeprefix('/results/test-'))

    def request(self, path, payload=None):
        self.posts.append(payload)
        group = payload['stages'][0]['tool_group']
        return {'result_id': 'test-' + group, 'warnings': []}


class APITests(unittest.TestCase):
    def test_default_panel_aliases_are_formatted_for_api(self):
        prediction = PredictionSettings()
        request = build_payload(SEQUENCE, 'mhcii', prediction.alleles_ii, prediction)
        names = request['stages'][0]['input_parameters']['alleles'].split(',')
        self.assertEqual(len(names), 27)
        self.assertEqual(names[0], 'HLA-DRB1*01:01')
        self.assertEqual(names[15], 'HLA-DQA1*05:01/DQB1*02:01')
        self.assertEqual(names[-1], 'HLA-DPA1*02:01/DPB1*14:01')
        # Both chain-prefix conventions denote the same human molecule.
        paired = build_payload(SEQUENCE, 'mhcii', ['HLA-DQA1*05:01/HLA-DQB1*02:01'], prediction)
        self.assertEqual(paired['stages'][0]['input_parameters']['alleles'], names[15])
        self.assertEqual(build_payload(SEQUENCE, 'mhci', ['H2-Kb'], prediction)
                         ['stages'][0]['input_parameters']['alleles'], 'H2-Kb')

    def test_exact_model_and_immunogenicity_requests(self):
        request = build_payload(SEQUENCE, 'mhcii', SMALL.alleles_ii, SMALL)
        p = request['stages'][0]['input_parameters']
        self.assertEqual(p['peptide_spacing'], 1)
        self.assertEqual(p['predictors'], [{'type': 'binding', 'method': 'netmhciipan_ba-4.3'}, {'type': 'immunogenicity', 'method': 'cd4episcore'}])
        self.assertEqual(p['peptide_length_range'], [15, 15])

    def test_real_responses_preserve_raw_score_and_coordinates(self):
        for group, alleles, count in [('mhci', SMALL.alleles_i, 24), ('mhcii', SMALL.alleles_ii, 18)]:
            frame = parse_prediction(fixture(group), group, alleles, SMALL, SEQUENCE)
            self.assertEqual(len(frame), count)
            self.assertTrue(all(SEQUENCE[row.start - 1:row.end] == row.peptide for row in frame.itertuples()))
            if group == 'mhcii':
                row = frame[frame.peptide == 'ALPHIIDEVINIVII'].iloc[0]
                self.assertEqual(row.score, 92.1408)  # raw CD4Episcore, NOT the 74.05632 combined score
                self.assertEqual(row.ic50, 701.26)

    def test_empty_partial_duplicate_and_wrong_model_responses_fail(self):
        good = fixture('mhci')
        bad = deepcopy(good);bad['data']['results'][0]['table_data'].pop()
        with self.assertRaisesRegex(APIError, '不完整'):
            parse_prediction(bad, 'mhci', SMALL.alleles_i, SMALL, SEQUENCE)
        bad = deepcopy(good);bad['data']['results'][0]['table_data'].append(bad['data']['results'][0]['table_data'][0])
        with self.assertRaisesRegex(APIError, '重复'):
            parse_prediction(bad, 'mhci', SMALL.alleles_i, SMALL, SEQUENCE)
        with self.assertRaisesRegex(APIError, 'ic50'):
            parse_prediction(good, 'mhci', SMALL.alleles_i, replace(SMALL, method_i='netmhcpan_ba-4.2'), SEQUENCE)
        bad = deepcopy(good);bad['data']['errors'] = ['predictor failed']
        with self.assertRaises(APIError):
            parse_prediction(bad, 'mhci', SMALL.alleles_i, SMALL, SEQUENCE)

    def test_settings_reject_bad_ranges_duplicates_and_credential_proxy(self):
        for settings in [replace(SMALL, min_length_ii=10), replace(SMALL, poll_seconds=0),
                         replace(SMALL, alleles_i=['HLA-A*02:01', 'A*02:01']),
                         replace(SMALL, proxy_url='http://user:secret@localhost:7897'),
                         replace(SMALL, method_ii='netmhciipan_el')]:
            with self.assertRaises(ValueError):
                settings.validate(len(SEQUENCE))

    def test_ambiguous_submission_is_not_automatically_reposted(self):
        class Broken(FakeClient):
            def request(self, path, payload=None):
                self.posts.append(payload)
                raise urllib.error.URLError('response lost after submission')
        client = Broken()
        with tempfile.TemporaryDirectory() as folder:
            payload = build_payload(SEQUENCE, 'mhci', SMALL.alleles_i, SMALL)
            for _ in range(2):
                with self.assertRaises(APIError):
                    predict_batch(client, payload, Path(folder), SMALL, lambda m: None, None)
            self.assertEqual(len(client.posts), 1)
            self.assertEqual(json.loads((Path(folder) / 'submission_state.json').read_text())['status'], 'submission_unknown')

    def test_cached_job_resume_does_not_post_or_poll(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            payload = build_payload(SEQUENCE, 'mhci', SMALL.alleles_i, SMALL)
            a = predict_batch(client, payload, folder, SMALL, lambda m: None, None)
            self.assertEqual(a['status'], 'done')
            client.gets.clear()
            predict_batch(client, payload, folder, SMALL, lambda m: None, None)
            self.assertEqual(len(client.posts), 1)
            self.assertEqual(client.gets, [])

    def test_full_pipeline_replays_real_responses_and_resumes(self):
        with tempfile.TemporaryDirectory(prefix='在线 表位 ') as folder:
            client = FakeClient()
            params = Settings(min_window=20, max_window=20)
            output = run_online(ROOT / 'examples/public_lcmv.fasta', folder, params, SMALL, client=client)
            manifest = json.loads((output / 'pipeline_manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['status'], 'complete')
            self.assertEqual(len(client.posts), 2)
            screening = output / manifest['screening_directory']
            self.assertTrue((screening / 'results.xlsx').is_file())
            self.assertEqual(json.loads((screening / 'run_manifest.json').read_text(encoding='utf-8'))['window_count'], 13)
            run_online(ROOT / 'examples/public_lcmv.fasta', folder, params, replace(SMALL, poll_seconds=15), resume=output, client=client)
            self.assertEqual(len(client.posts), 2)
            with self.assertRaisesRegex(ValueError, '参数'):
                run_online(ROOT / 'examples/public_lcmv.fasta', folder, replace(params, rank_i=1), SMALL, resume=output, client=client)

    def test_cancellation_leaves_job_id_for_resume(self):
        stop = threading.Event()
        class Pending(FakeClient):
            def get(self, path, stop=None):
                stop.set()
                return {'status': 'pending', 'data': {'errors': []}}
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(Cancelled):
                predict_batch(Pending(), build_payload(SEQUENCE, 'mhci', SMALL.alleles_i, SMALL), Path(folder), SMALL, lambda m: None, stop)
            self.assertTrue((Path(folder) / 'submitted.json').exists())

    def test_v020_invalid_allele_resume_reuses_mhci_and_preserves_rejection(self):
        prediction = replace(SMALL, alleles_ii=['DRB1*01:01'])
        class LegacyClient(FakeClient):
            def request(self, path, payload=None):
                if payload['stages'][0]['input_parameters']['alleles'] == 'DRB1*01:01':
                    self.posts.append(payload)
                    return {'errors': ['The following are not valid alleles: DRB1*01:01']}
                return super().request(path, payload)
        def legacy_payload(sequence, group, alleles, config):
            request = build_payload(sequence, group, alleles, config)
            request['stages'][0]['input_parameters']['alleles'] = ','.join(alleles)
            return request
        with tempfile.TemporaryDirectory() as folder:
            client = LegacyClient()
            params = Settings(min_window=20, max_window=20)
            with patch('epitope_screening.iedb_api.build_payload', side_effect=legacy_payload):
                with self.assertRaisesRegex(APIError, '拒绝任务'):
                    run_online(ROOT / 'examples/public_lcmv.fasta', folder, params, prediction, client=client)
            output = next(Path(folder).glob('online_*'))
            original_mhci = (output / 'predictions/mhci_000/result.json').read_bytes()
            run_online(ROOT / 'examples/public_lcmv.fasta', folder, params, prediction, resume=output, client=client)
            self.assertEqual(len(client.posts), 3)  # I succeeded, II rejected, corrected II only
            self.assertEqual(client.posts[-1]['stages'][0]['input_parameters']['alleles'], 'HLA-DRB1*01:01')
            self.assertEqual((output / 'predictions/mhci_000/result.json').read_bytes(), original_mhci)
            archived = list((output / 'predictions/mhcii_000').glob('rejected_*/submitted.json'))
            self.assertEqual(len(archived), 1)
            self.assertIn('not valid alleles', archived[0].read_text())
            self.assertEqual(json.loads((output / 'pipeline_manifest.json').read_text())['status'], 'complete')
            run_online(ROOT / 'examples/public_lcmv.fasta', folder, params, prediction, resume=output, client=client)
            self.assertEqual(len(client.posts), 3)

    def test_alias_repair_refuses_unknown_or_confirmed_jobs_and_model_changes(self):
        for state in ('unknown', 'confirmed', 'different-model'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                prediction = replace(SMALL, alleles_ii=['DRB1*01:01'])
                payload = build_payload(SEQUENCE, 'mhcii', prediction.alleles_ii, prediction)
                old = deepcopy(payload)
                old['stages'][0]['input_parameters']['alleles'] = 'DRB1*01:01'
                (folder / 'request.json').write_text(json.dumps(old))
                rejected = {'errors': ['The following are not valid alleles: DRB1*01:01']}
                if state == 'confirmed':
                    rejected['result_id'] = 'already-submitted'
                if state != 'unknown':
                    (folder / 'submitted.json').write_text(json.dumps(rejected))
                if state == 'different-model':
                    payload['stages'][0]['input_parameters']['predictors'][0]['method'] = 'netmhciipan_ba-4.2'
                client = FakeClient()
                with self.assertRaises(APIError):
                    predict_batch(client, payload, folder, prediction, lambda m: None, None)
                self.assertEqual(client.posts, [])

    def test_only_idempotent_gets_are_retried(self):
        client = APIClient()
        with patch.object(client, 'request', side_effect=[urllib.error.URLError('temporary'), {'predictors': []}]) as request, patch('time.sleep'):
            self.assertEqual(client.get('/mhci'), {'predictors': []})
            self.assertEqual(request.call_count, 2)


if __name__ == '__main__':
    unittest.main()
