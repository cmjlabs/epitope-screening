"""Exercise real HTTP uploads, scientific exports, persistence and local boundaries."""
from contextlib import ExitStack
import io
import json
from pathlib import Path
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

from epitope_screening.web_app import Application, ASSETS, create_server
from epitope_screening.web_setup import safe_extract, SetupWizard
from epitope_screening.local_iedb import external_environment


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = create_server(self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.app.close()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None, token=True):
        merged = {'X-App-Token': self.server.app.token} if token else {}
        merged.update(headers or {})
        raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url+path, data=raw, headers=merged)
        try:
            response = urllib.request.urlopen(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data = response.read()
            return response.status, json.loads(data) if response.headers.get('Content-Type','').startswith('application/json') else data

    def finish(self, task_id):
        deadline = time.monotonic()+30
        while time.monotonic()<deadline:
            code, value = self.request('/api/task?id='+task_id)
            if value['status'] not in ('running', 'cancelling'):
                self.assertEqual(value['status'], 'complete', value)
                return value
            time.sleep(.05)
        self.fail('Offline task timed out')

    def test_upload_analyze_download_and_restart_history(self):
        payload = {'mode':'offline','settings':{'min_window':20,'max_window':21,'coverage_threshold':0}}
        for kind, name in [('fasta','protein.fasta'),('mhci','mhci.csv'),('mhcii','mhcii.csv')]:
            code, record = self.request('/api/upload?'+urllib.parse.urlencode({'kind':kind,'name':'中文 空格 '+name}), (ASSETS/'examples'/name).read_bytes())
            self.assertEqual(code,200,record)
            payload[kind] = record['id']
        code, task = self.request('/api/start',payload)
        self.assertEqual(code,202,task)
        result = self.finish(task['id'])
        self.assertEqual(result['summary']['windows'],21)
        self.assertEqual(result['summary']['selected'],21)
        workbook = next(f for f in result['files'] if f.endswith('results.xlsx'))
        code, raw = self.request('/api/file?'+urllib.parse.urlencode({'id':task['id'],'name':workbook}))
        self.assertEqual(code,200)
        import pandas as pd
        with pd.ExcelFile(io.BytesIO(raw)) as excel:
            rows = pd.read_excel(excel,'selected_windows')
            self.assertEqual(len(rows),21)
            self.assertEqual(rows.iloc[0]['peptide'],'ACDEFGHIKLMNPQRSTVWY')
            self.assertAlmostEqual(rows.iloc[0]['mhc1_coverage'],.5243072563,places=9)
        restarted = Application(self.root)
        self.assertNotEqual(restarted.token,self.server.app.token)
        self.assertEqual(restarted.task(task['id'])['status'],'complete')
        # Reports outside the current task are never downloadable.
        for name in ('../../../../session.json','/etc/passwd','../task.json'):
            code, _ = self.request('/api/file?'+urllib.parse.urlencode({'id':task['id'],'name':name}))
            self.assertEqual(code,400)

    def test_loopback_session_and_origin_boundaries(self):
        self.assertEqual(self.request('/')[0],200)
        self.assertEqual(self.request('/api/state',token=False)[0],403)
        self.assertEqual(self.request('/api/state',headers={'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.request('/api/state',headers={'Host':'other.example'})[0],403)
        self.assertEqual(self.request('/../web_app.py')[0],404)
        code, body = self.request('/api/start',{'mode':'online','fasta_text':'ACDEFGHIKLMNPQRSTVWY'*2})
        self.assertEqual(code,400)
        self.assertIn('确认',body['error'])
        self.assertFalse(self.server.app.tasks)
        self.assertEqual(self.request('/api/upload?kind=tc1&name=payload.sh',b'echo bad')[0],400)
        self.assertEqual(self.request('/api/start',[])[0],400)

    def test_interrupted_job_survives_restart(self):
        folder=self.root/'jobs'/('a'*32)
        folder.mkdir()
        (folder/'task.json').write_text(json.dumps({'id':'a'*32,'status':'running','created':'2026-01-01','request':{}}))
        restarted=Application(self.root)
        self.assertEqual(restarted.task('a'*32)['status'],'interrupted')


class InstallerTests(unittest.TestCase):
    def test_archive_rejects_escape_and_special_files(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            for member_name, kind in [('../escaped',tarfile.REGTYPE),('device',tarfile.CHRTYPE),('escape-link',tarfile.SYMTYPE)]:
                archive=root/'input.tar.gz'
                with tarfile.open(archive,'w:gz') as stream:
                    info=tarfile.TarInfo(member_name); info.type=kind
                    if kind==tarfile.SYMTYPE: info.linkname='../../outside'
                    stream.addfile(info)
                with self.assertRaises((ValueError,tarfile.FilterError)):
                    safe_extract(archive,root/'output')
            self.assertFalse((root/'escaped').exists())

    def test_consent_and_hash_checks_precede_execution(self):
        with tempfile.TemporaryDirectory() as name, ExitStack() as stack:
            stack.enter_context(patch('epitope_screening.web_setup.platform.system',return_value='Linux'))
            stack.enter_context(patch('epitope_screening.web_setup.platform.machine',return_value='x86_64'))
            stack.enter_context(patch('epitope_screening.web_setup.missing_system',return_value=['R']))
            wizard=SetupWizard(name)
            with self.assertRaisesRegex(ValueError,'许可'):
                wizard.start('download')
            with self.assertRaisesRegex(ValueError,'系统授权'):
                wizard.start('download',license_accepted=True)
            network=stack.enter_context(patch('epitope_screening.web_setup.urllib.request.urlopen'))
            network.return_value.__enter__.return_value.read.side_effect=[b'bad',b'']
            network.return_value.__enter__.return_value.headers={'Content-Length':'3'}
            network.return_value.__enter__.return_value.status=200
            with self.assertRaisesRegex(ValueError,'校验失败'):
                wizard.download('https://example.com/archive',Path(name)/'archive.gz','0'*64)
            self.assertFalse((Path(name)/'archive.gz').exists())
            self.assertFalse((Path(name)/'archive.gz.part').exists())

    def test_frozen_subprocesses_do_not_inherit_bundle_libraries(self):
        with patch('sys.frozen',True,create=True), patch.dict('os.environ',{'LD_LIBRARY_PATH':'/bundle','LD_LIBRARY_PATH_ORIG':'/system','PYTHONHOME':'/bundle','PYTHONPATH':'/bundle'}):
            env=external_environment()
            self.assertEqual(env['LD_LIBRARY_PATH'],'/system')
            self.assertNotIn('PYTHONHOME',env)
            self.assertNotIn('PYTHONPATH',env)
