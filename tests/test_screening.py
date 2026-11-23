from dataclasses import replace
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import unittest

import pandas as pd

from epitope_screening.core import (
    Settings, read_sequence, normalize_table, read_table, filter_table,
    CoverageCalculator, run_analysis, Cancelled, _MemoryCoverage, DATA,
)

ROOT = Path(__file__).resolve().parents[1]
SEQUENCE = 'ACDEFGHIKLMNPQRSTVWYACDEFGHIKL'


class ScreeningTests(unittest.TestCase):
    def test_multirecord_fasta_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'bad.fa'
            p.write_text('>a\nACDE\n>b\nFGHI\n')
            with self.assertRaisesRegex(ValueError, '单条'):
                read_sequence(p)

    def test_iedb_columns_and_coordinate_validation(self):
        frame = read_table(ROOT / 'examples/mhci.csv').rename(columns={
            'ic50': 'netmhcpan_ba IC50', 'percentile': 'netmhcpan_ba percentile',
            'score': 'immunogenicity score'})
        normalized = normalize_table(frame, 'I', SEQUENCE)
        self.assertEqual(len(normalized), 2)
        frame.loc[0, 'start'] = 2
        with self.assertRaisesRegex(ValueError, '不匹配'):
            normalize_table(frame, 'I', SEQUENCE)

    def test_duplicate_column_names_are_rejected(self):
        frame = read_table(ROOT / 'examples/mhci.csv')
        frame[' IC50 '] = 25
        with self.assertRaisesRegex(ValueError, '重复列名'):
            normalize_table(frame, 'I', SEQUENCE)

    def test_missing_scores_are_not_silently_accepted(self):
        frame = read_table(ROOT / 'examples/mhci.csv')
        frame.loc[0, 'score'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'score'):
            normalize_table(frame, 'I', SEQUENCE)

    def test_ambiguous_prediction_models_are_rejected(self):
        frame = read_table(ROOT / 'examples/mhcii.csv').rename(columns={
            'ic50': 'netmhciipan_ba-4.3 IC50', 'percentile': 'netmhciipan_ba-4.3 percentile'})
        frame['netmhciipan_ba-4.2 IC50'] = 25
        with self.assertRaisesRegex(ValueError, '多个'):
            normalize_table(frame, 'II', SEQUENCE)

    def test_filter_boundaries_and_strict_score_option(self):
        frame = pd.DataFrame({'allele': ['HLA-A*02:01'] * 3, 'peptide': ['ACDEFGHIK'] * 3,
            'start': [1] * 3, 'end': [9] * 3, 'ic50': [500, 501, 500],
            'rank': [2, 2, 2], 'score': [0, 1, 1]})
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'one').mkdir(); (base / 'two').mkdir()
            params = Settings(use_allele_cutoff=False)
            a, counts, _ = filter_table(frame, 'I', params, None, base / 'one')
            b, _, _ = filter_table(frame, 'I', replace(params, score_i_strict_min=True), None, base / 'two')
            self.assertEqual(counts['step3_score_min'], 2)
            self.assertEqual(len(a), 1)
            self.assertEqual(a.iloc[0].score, 0)
            self.assertEqual(b.iloc[0].score, 1)
            self.assertEqual(len(b), 1)
            self.assertEqual(counts['step1_ic50'], 2)

    def test_unknown_population_and_invalid_ranges(self):
        for settings in [Settings(population='not-a-population'), Settings(min_window=30, max_window=20),
                         Settings(score_ii_min=80), Settings(coverage_threshold=80), Settings(rank_i=float('nan'))]:
            with self.assertRaises(ValueError):
                settings.validate()

    def test_coverage_matches_known_frequency_result(self):
        alleles = ['HLA-A*30:02','HLA-A*01:01','HLA-A*68:01','HLA-A*11:01','HLA-A*03:01','HLA-A*26:01',
                   'HLA-B*44:02','HLA-B*44:03','HLA-B*15:01','HLA-A*23:01','HLA-B*35:01','HLA-A*24:02',
                   'HLA-B*40:01','HLA-A*31:01','HLA-A*30:01','HLA-A*33:01']
        calc = CoverageCalculator('World')
        value, _ = calc.calculate(pd.DataFrame({'allele': alleles}), 'I')
        self.assertAlmostEqual(value, 0.8559470559337659, places=10)
        # Check that caching by union preserves total coverage for per-epitope grouping.
        pc = _MemoryCoverage()
        pc.input_rows = [('PEPTIDE1', alleles[:8]), ('PEPTIDE2', alleles[8:])]
        result, negative = pc.calculate_coverage(population=['World'], mhc_class=['I'])
        self.assertFalse(negative)
        self.assertAlmostEqual(value, result[0]['coverage'], places=10)

    def test_unsupported_alleles_fail_instead_of_returning_zero(self):
        with self.assertRaisesRegex(ValueError, '无频率'):
            CoverageCalculator('World').calculate(pd.DataFrame({'allele': ['HLA-INVALID*99:99']}), 'I')

    def test_end_to_end_unicode_paths_and_repeatability(self):
        with tempfile.TemporaryDirectory(prefix='表位 测试 ') as folder:
            root = Path(folder)
            settings = Settings(min_window=20, max_window=21, coverage_threshold=0)
            args = (ROOT / 'examples/protein.fasta', ROOT / 'examples/mhci.csv', ROOT / 'examples/mhcii.csv', root)
            a = run_analysis(*args, settings=settings)
            b = run_analysis(*args, settings=settings)
            self.assertNotEqual(a, b)
            self.assertEqual((a / 'all_windows.csv').read_bytes(), (b / 'all_windows.csv').read_bytes())
            manifest = json.loads((a / 'run_manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['status'], 'complete')
            self.assertEqual(manifest['window_count'], 21)
            self.assertEqual(manifest['selected_count'], 21)
            self.assertEqual(len(manifest['inputs']['fasta']['sha256']), 64)
            report = pd.read_excel(a / 'results.xlsx', sheet_name=None)
            self.assertEqual(set(report), {'all_windows', 'selected_windows', 'mhci_filtered', 'mhcii_filtered', 'filter_summary', 'settings'})
            empty = report['all_windows'].iloc[-1]
            self.assertEqual(empty.mhc1_coverage, 0)
            self.assertTrue((a / 'mhci/step1_ic50.csv').exists())

    def test_cli_with_legacy_redirected_encoding(self):
        with tempfile.TemporaryDirectory() as folder:
            environment = dict(os.environ, PYTHONIOENCODING='ascii')
            result = subprocess.run([sys.executable, '-m', 'epitope_screening', 'run',
                '--fasta', str(ROOT / 'examples/protein.fasta'), '--mhci', str(ROOT / 'examples/mhci.csv'),
                '--mhcii', str(ROOT / 'examples/mhcii.csv'), '--output', folder],
                cwd=ROOT, env=environment, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
            self.assertIn('完成', result.stdout.decode('utf-8'))

    def test_cutoff_is_required_when_enabled(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                run_analysis(ROOT / 'examples/protein.fasta', ROOT / 'examples/mhci.csv',
                             ROOT / 'examples/mhcii.csv', folder, cutoff=Path(folder) / 'missing.csv')
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_cancelled_job_does_not_report_success(self):
        with tempfile.TemporaryDirectory() as folder:
            stop = threading.Event()
            def progress(percent, message):
                stop.set()
            with self.assertRaises(Cancelled):
                run_analysis(ROOT / 'examples/protein.fasta', ROOT / 'examples/mhci.csv',
                             ROOT / 'examples/mhcii.csv', folder, progress=progress, stop=stop)
            manifest = json.loads(next(Path(folder).glob('*/run_manifest.json')).read_text(encoding='utf-8'))
            self.assertEqual(manifest['status'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
