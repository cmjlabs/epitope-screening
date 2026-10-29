from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from epitope_screening.core import Settings
from epitope_screening.iedb_api import PredictionSettings, parse_prediction
from epitope_screening.local_iedb import normalize_local_result, run_local, local_payload, tool_signature

ROOT=Path(__file__).resolve().parents[1]
SEQ='MGQIVTMFEALPHIIDEVINIVIIVLIVITGI'
SMALL=PredictionSettings(alleles_i=['HLA-A*02:01'],alleles_ii=['DRB1*01:01'],min_length_i=9,max_length_i=9)

def load(name):
    return json.loads((ROOT/'tests/fixtures'/name).read_text())

class LocalTests(unittest.TestCase):
    def test_tool_signature_tracks_data_but_ignores_bytecode_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'model.dat').write_bytes(b'original')
            first=tool_signature(root)
            (root/'__pycache__').mkdir()
            (root/'__pycache__/example.pyc').write_bytes(b'cache')
            self.assertEqual(first,tool_signature(root))
            (root/'model.dat').write_bytes(b'changed!')
            self.assertNotEqual(first['sha256'],tool_signature(root)['sha256'])

    def test_local_real_outputs_match_recorded_api_values(self):
        for group,alleles in [('mhci',SMALL.alleles_i),('mhcii',SMALL.alleles_ii)]:
            a=normalize_local_result(load('local-'+group+'.json'),group,alleles,SMALL,SEQ)
            b=parse_prediction(load(group+'.json'),group,alleles,SMALL,SEQ)
            a=a.sort_values(['start','allele']).reset_index(drop=True)
            b=b.sort_values(['start','allele']).reset_index(drop=True)
            self.assertEqual(a.peptide.tolist(),b.peptide.tolist())
            for field in ['ic50','rank','score']:
                self.assertEqual(a[field].tolist(),b[field].tolist())

    def test_repeated_peptides_expand_to_every_source_position(self):
        sequence='ACDEFGHIKACDEFGHIK'
        peptides=sorted({sequence[s:s+9] for s in range(len(sequence)-8)})
        raw={'results':[
            {'method':'binding.netmhcpan_ba','type':'peptide_table','table_columns':['allele','peptide','ic50','percentile'],
             'table_data':[['HLA-A*02:01',p,25,1] for p in peptides]},
            {'method':'immunogenicity','type':'peptide_table','table_columns':['allele','peptide','score'],
             'table_data':[['HLA-A*02:01',p,.2] for p in peptides]}]}
        result=normalize_local_result(raw,'mhci',SMALL.alleles_i,SMALL,sequence)
        self.assertEqual(result[result.peptide=='ACDEFGHIK'].start.tolist(),[1,10])
        self.assertEqual(len(result),10)

    def test_missing_or_conflicting_local_scores_fail(self):
        raw=load('local-mhcii.json')
        score=next(t for t in raw['results'] if t.get('method')=='immunogenicity.cd4episcore')
        score['table_data'].pop()
        with self.assertRaisesRegex(ValueError,'score'):
            normalize_local_result(raw,'mhcii',SMALL.alleles_ii,SMALL,SEQ)
        raw=load('local-mhci.json')
        score=next(t for t in raw['results'] if t.get('method')=='immunogenicity')
        other=deepcopy(score['table_data'][0]);other[-1]=999
        score['table_data'].append(other)
        with self.assertRaisesRegex(ValueError,'冲突'):
            normalize_local_result(raw,'mhci',SMALL.alleles_i,SMALL,SEQ)

    def test_cd4_local_spacing_is_predictor_parameter(self):
        payload=local_payload(SEQ,'mhcii',SMALL.alleles_ii,SMALL)
        self.assertEqual(payload['predictors'][1]['peptide_spacing'],1)
        self.assertEqual(payload['predictors'][0]['method'],'netmhciipan_ba-4.3')

    def test_local_pipeline_resume_uses_validated_cached_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            paths={'python':Path(sys.executable)}
            for tag,group in [('tc1','mhci'),('tc2','mhcii')]:
                paths[tag]=root/tag
                (paths[tag]/'src').mkdir(parents=True)
                (paths[tag]/'src'/('tcell_'+group+'.py')).write_text('# fixture test')
            calls=[]
            def fake_execute(command,folder,stop):
                group='mhci' if 'tcell_mhci.py' in command[1] else 'mhcii'
                (folder/'result.json').write_text(json.dumps(load('local-'+group+'.json')))
                calls.append(group)
                return .1
            params=Settings(min_window=20,max_window=20)
            with patch('epitope_screening.local_iedb.validate_runtime',return_value=(paths,2)),patch('epitope_screening.local_iedb.execute_job',side_effect=fake_execute):
                output=run_local(ROOT/'examples/public_lcmv.fasta',root,{},params,SMALL)
                run_local(ROOT/'examples/public_lcmv.fasta',root,{},params,SMALL,resume=output)
                self.assertEqual(len(calls),2)
                manifest=json.loads((output/'local_manifest.json').read_text())
                self.assertEqual(manifest['status'],'complete')
                self.assertTrue((output/manifest['screening_directory']/'results.xlsx').is_file())
                with self.assertRaisesRegex(ValueError,'改变'):
                    run_local(ROOT/'examples/public_lcmv.fasta',root,{},replace(params,rank_i=1),SMALL,resume=output)

if __name__=='__main__':unittest.main()
