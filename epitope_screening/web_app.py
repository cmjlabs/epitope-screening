"""Loopback-only browser application; the scientific engines remain shared."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser

import pandas as pd

from . import __version__
from .core import Settings, Cancelled, read_sequence, run_analysis, populations
from .iedb_api import PredictionSettings, run_online, write_json
from .local_iedb import external_environment, run_local
from .web_setup import SetupWizard, ASSETS
from .report_bundle import build_result_bundle

ID = re.compile(r'^[a-f0-9]{32}$')
UPLOAD_SUFFIXES = {'fasta': {'.fasta','.fa','.faa','.txt'}, 'mhci': {'.csv','.tsv','.txt','.xlsx'},
                  'mhcii': {'.csv','.tsv','.txt','.xlsx'}, 'cutoff': {'.csv'},
                  'tc1': {'.gz'}, 'tc2': {'.gz'}}


def default_data_directory():
    base = Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))
    return base/'epitope-screening'


class Application:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for folder in ('uploads','jobs'):
            (self.root/folder).mkdir(exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.tasks = {}
        self.download_tickets = {}
        self.stops = {}
        self.threads = {}
        self.wizard = SetupWizard(self.root)
        for path in (self.root/'jobs').glob('*/task.json'):
            try:
                task = json.loads(path.read_text(encoding='utf-8'))
                if not ID.fullmatch(task['id']):
                    continue
                if task['status'] in ('running','cancelling'):
                    task.update(status='interrupted', message='程序已重新启动，可恢复此任务。')
                self.tasks[task['id']] = task
            except (OSError, ValueError, KeyError):
                continue

    def save(self, task):
        write_json(self.root/'jobs'/task['id']/'task.json', task)

    def state(self):
        with self.lock:
            jobs = [{k:v for k,v in task.items() if k not in ('request','logs')}
                    for task in sorted(self.tasks.values(),key=lambda t:t['created'],reverse=True)]
        return {'version': __version__, 'data_directory': str(self.root), 'setup': self.wizard.snapshot(),
            'tasks': jobs, 'defaults': {'settings': asdict(Settings()), 'prediction': asdict(PredictionSettings())},
            'populations': populations(),
            'public_fasta': (ASSETS/'examples/public_lcmv.fasta').read_text(encoding='utf-8'),
            'small_prediction': asdict(PredictionSettings(alleles_i=['HLA-A*02:01'],alleles_ii=['HLA-DRB1*01:01'],min_length_i=9,max_length_i=9))}

    def upload_path(self, upload_id, kind):
        if not isinstance(upload_id,str) or not ID.fullmatch(upload_id):
            raise ValueError('请选择需要的文件。')
        folder = self.root/'uploads'/upload_id
        record = json.loads((folder/'upload.json').read_text(encoding='utf-8'))
        if record['kind'] != kind:
            raise ValueError('文件类型与当前用途不匹配，请重新选择。')
        return folder/('file'+record['suffix'])

    def active(self):
        return any(t['status'] in ('running','cancelling') for t in self.tasks.values())

    def task(self, task_id):
        if not isinstance(task_id,str) or not ID.fullmatch(task_id) or task_id not in self.tasks:
            raise ValueError('未找到该任务。')
        with self.lock:
            result = json.loads(json.dumps(self.tasks[task_id]))
        result.pop('request',None)
        result['elapsed_seconds'] = round(time.time()-result['started'],1) if result['status'] in ('running','cancelling') else result.get('elapsed_seconds',0)
        return result

    def start_task(self, request, demo=False):
        with self.lock:
            if self.active() or self.wizard.snapshot()['busy']:
                raise ValueError('已有分析或配置正在运行，请等待或先取消。')
            mode = request.get('mode','local')
            if mode not in ('local','online','offline'):
                raise ValueError('请选择有效的运行方式。')
            if mode == 'local' and not self.wizard.snapshot()['ready']:
                raise ValueError('请先在“首次配置”中准备本地预测环境，或选择在线/导入表模式。')
            if mode == 'online' and request.get('allow_sequence_upload') is not True:
                raise ValueError('在线模式需要你确认将蛋白序列发送至 IEDB 官方服务器。')
            settings = Settings(**request.get('settings',{}))
            settings.validate()
            prediction = PredictionSettings(**request.get('prediction',{}))
            task_id = uuid.uuid4().hex
            folder = self.root/'jobs'/task_id
            folder.mkdir()
            if demo:
                for name in ('protein.fasta','mhci.csv','mhcii.csv'):
                    shutil.copyfile(ASSETS/'examples'/name,folder/name)
                settings = Settings(min_window=20,max_window=21,coverage_threshold=0)
                inputs = {'fasta':str(folder/'protein.fasta'),'mhci':str(folder/'mhci.csv'),'mhcii':str(folder/'mhcii.csv')}
            else:
                inputs = {}
                text = request.get('fasta_text','').strip()
                if text:
                    if len(text)>1024**2:
                        raise ValueError('序列文本太长，请检查是否为单条蛋白。')
                    if not text.startswith('>'):
                        text = '>protein\n'+text
                    (folder/'input.fasta').write_text(text+'\n',encoding='utf-8')
                    inputs['fasta'] = str(folder/'input.fasta')
                else:
                    source = self.upload_path(request.get('fasta'),'fasta')
                    shutil.copyfile(source,folder/'input.fasta')
                    inputs['fasta'] = str(folder/'input.fasta')
                for kind in (('mhci','mhcii') if mode=='offline' else ()):
                    source = self.upload_path(request.get(kind),kind)
                    target = folder/(kind+source.suffix)
                    shutil.copyfile(source,target)
                    inputs[kind] = str(target)
                if request.get('cutoff'):
                    shutil.copyfile(self.upload_path(request['cutoff'],'cutoff'),folder/'cutoff.csv')
                    inputs['cutoff'] = str(folder/'cutoff.csv')
            sequence = read_sequence(inputs['fasta'])
            if settings.max_window>len(sequence):
                raise ValueError(f'蛋白长 {len(sequence)} aa，最大窗口不能超过序列长度。')
            if mode!='offline':
                prediction.validate(len(sequence))
                for tag in ('i','ii'):
                    alleles = getattr(prediction,'alleles_'+tag)
                    if any(not re.fullmatch(r'[A-Za-z0-9*:/_\-]+',a) for a in alleles):
                        raise ValueError('HLA 名称含不支持的字符，请检查逗号分隔的名称。')
                    rows = len(alleles)*sum(len(sequence)-n+1 for n in range(getattr(prediction,'min_length_'+tag),getattr(prediction,'max_length_'+tag)+1))
                    if rows>1000000:
                        raise ValueError('本次预测规模超过网页版本的上限，请减少 HLA 或肽长范围。')
            task = {'id':task_id,'name':str(request.get('name') or ('离线演示（人工数据）' if demo else '蛋白分析'))[:80],
                'mode':mode,'demo':demo,'created':datetime.now(timezone.utc).isoformat(),
                'status':'running','progress':0,'message':'正在检查输入与环境','logs':[],
                'request':{'inputs':inputs,'settings':asdict(settings),'prediction':asdict(prediction)},'started':time.time()}
            self.tasks[task_id] = task
            self.save(task)
            self._spawn(task)
            return self.task(task_id)

    def _spawn(self, task):
        stop = threading.Event()
        self.stops[task['id']] = stop
        thread = threading.Thread(target=self._work,args=(task,stop),daemon=True)
        self.threads[task['id']] = thread
        thread.start()

    def _work(self, task, stop):
        folder = self.root/'jobs'/task['id']
        request = task['request']
        inputs = request['inputs']
        last_save = [0]
        def progress(percent,message):
            with self.lock:
                task.update(progress=percent,message=message)
                task['logs'] = (task['logs']+[message])[-1200:]
                if time.monotonic()-last_save[0]>2:
                    self.save(task);last_save[0]=time.monotonic()
        outcome = 'complete'
        final_message = '分析完成，完整结果包已保存在本机'
        try:
            settings = Settings(**request['settings'])
            prediction = PredictionSettings(**request['prediction'])
            args = (inputs['fasta'],folder/'output')
            resume = task.get('run_directory')
            if not resume and task['mode']!='offline':
                prefix = 'local_' if task['mode']=='local' else 'online_'
                found = sorted((folder/'output').glob(prefix+'*'))
                resume = str(found[-1]) if found else None
            if task['mode']=='local':
                config = json.loads((self.root/'local-tools.json').read_text(encoding='utf-8'))
                result = run_local(*args,config,settings,prediction,inputs.get('cutoff'),progress,stop,resume)
                manifest_name = 'local_manifest.json'
            elif task['mode']=='online':
                result = run_online(*args,settings,prediction,inputs.get('cutoff'),progress,stop,resume)
                manifest_name = 'pipeline_manifest.json'
            else:
                result = run_analysis(inputs['fasta'],inputs['mhci'],inputs['mhcii'],folder/'output',settings,inputs.get('cutoff'),progress,stop)
                manifest_name = None
            report_dir = result
            if manifest_name:
                manifest = json.loads((result/manifest_name).read_text(encoding='utf-8'))
                report_dir = result/manifest['screening_directory']
            report = json.loads((report_dir/'run_manifest.json').read_text(encoding='utf-8'))
            preview = pd.read_csv(report_dir/'selected_windows.csv',nrows=30)
            with self.lock:
                task.update(run_directory=str(result),report_directory=str(report_dir),progress=99,message='正在整理原始预测、中间筛选与最终结果包',
                summary={'windows':report['window_count'],'selected':report['selected_count'],
                    'preview':json.loads(preview.to_json(orient='records',force_ascii=False))},
                files=[str(p.relative_to(result)) for p in sorted(result.rglob('*')) if p.is_file() and p.suffix in ('.xlsx','.csv','.json','.log')])
        except Exception as error:
            with self.lock:
                outcome = 'cancelled' if isinstance(error,Cancelled) else 'failed'
                final_message = str(error)
                task.update(message='正在保存本次已生成的结果和日志')
                task['logs'] = (task['logs']+[str(error)])[-1200:]
                if task['mode']!='offline':
                    prefix = 'local_' if task['mode']=='local' else 'online_'
                    found = sorted((folder/'output').glob(prefix+'*'))
                    if found:
                        task['run_directory'] = str(found[-1])
        finally:
            with self.lock:
                task['elapsed_seconds'] = round(time.time()-task['started'],2)
                record = json.loads(json.dumps({**task, 'status':outcome, 'message':final_message}))
            try:
                build_result_bundle(folder, record)
                with self.lock:
                    task['bundle'] = True
            except Exception as error:
                outcome = 'failed'
                final_message += '；完整结果包生成失败：'+str(error)
            with self.lock:
                task.update(status=outcome,message=final_message,elapsed_seconds=round(time.time()-task['started'],2))
                if outcome=='complete':
                    task['progress'] = 100
                self.save(task)

    def bundle_path(self, task_id):
        self.task(task_id)
        path = self.root/'jobs'/task_id/'complete_results.zip'
        if not self.tasks[task_id].get('bundle') or not path.is_file():
            raise ValueError('完整结果包尚未生成，请等待任务停止后重试。')
        return path

    def issue_bundle_link(self, task_id):
        self.bundle_path(task_id)
        with self.lock:
            self.download_tickets = {key:value for key,value in self.download_tickets.items() if value[1]>time.monotonic()}
            ticket = secrets.token_urlsafe(32)
            self.download_tickets[ticket] = (task_id,time.monotonic()+60)
        return {'url':'/download/bundle?ticket='+ticket}

    def consume_bundle_link(self, ticket):
        with self.lock:
            task_id, expires = self.download_tickets.pop(ticket, (None,0))
        if not task_id or expires<time.monotonic():
            raise ValueError('下载链接已过期或已使用，请重新点击完整结果包按钮。')
        return self.bundle_path(task_id)

    def resume(self, task_id):
        with self.lock:
            self.task(task_id)
            if self.active() or self.wizard.snapshot()['busy']:
                raise ValueError('已有任务正在运行。')
            task = self.tasks[task_id]
            if task['mode']=='offline':
                raise ValueError('导入表模式无需恢复预测，请新建分析。')
            if task['mode']=='local' and not self.wizard.snapshot()['ready']:
                raise ValueError('请先完成本地环境配置。')
            task.update(status='running',started=time.time(),message='正在恢复任务',bundle=False)
            self.save(task)
            self._spawn(task)
            return self.task(task_id)

    def cancel(self, task_id):
        with self.lock:
            self.task(task_id)
            if task_id not in self.stops or self.tasks[task_id]['status'] not in ('running','cancelling'):
                raise ValueError('该任务当前没有运行。')
            self.stops[task_id].set()
            self.tasks[task_id].update(status='cancelling',message='正在停止，请稍候')
            return self.task(task_id)

    def download_path(self, task_id, name):
        self.task(task_id)
        base = Path(self.tasks[task_id].get('run_directory','')).resolve()
        if not base.is_relative_to(self.root/'jobs'/task_id):
            raise ValueError('该任务尚无可下载结果。')
        path = (base/name).resolve()
        if not path.is_relative_to(base) or not path.is_file() or path.suffix not in ('.xlsx','.csv','.json','.log'):
            raise ValueError('未找到可下载的结果文件。')
        return path

    def close(self):
        for stop in self.stops.values():
            stop.set()
        for thread in self.threads.values():
            thread.join(timeout=8)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def headers_out(self, content_type, length, status=200, extra=None):
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(length))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key,value in (extra or {}).items():
            self.send_header(key,value)
        self.end_headers()

    def json_out(self, data, status=200):
        body = json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.headers_out('application/json; charset=utf-8',len(body),status)
        self.wfile.write(body)

    def guard(self):
        port = self.server.server_port
        allowed = {f'127.0.0.1:{port}',f'localhost:{port}'}
        if self.headers.get('Host') not in allowed:
            raise PermissionError('不接受来自其他主机的请求。')
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://'+host for host in allowed}:
            raise PermissionError('不接受其他网页的请求。')
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.scheme or parsed.netloc:
            raise PermissionError('无效请求地址。')
        if parsed.path.startswith('/api/') and not secrets.compare_digest(self.headers.get('X-App-Token',''),self.server.app.token):
            raise PermissionError('会话已更新，请双击启动程序重新打开页面。')
        return parsed

    def do_OPTIONS(self):
        self.json_out({'error':'不接受跨站请求。'},403)

    def do_GET(self):
        try:
            parsed = self.guard()
            query = urllib.parse.parse_qs(parsed.query)
            app = self.server.app
            if parsed.path=='/api/state':
                self.json_out(app.state())
            elif parsed.path=='/api/task':
                self.json_out(app.task(query.get('id',[''])[0]))
            elif parsed.path in ('/api/file','/api/bundle','/download/bundle'):
                if parsed.path=='/download/bundle':
                    path = app.consume_bundle_link(query.get('ticket',[''])[0])
                elif parsed.path=='/api/bundle':
                    path = app.bundle_path(query.get('id',[''])[0])
                else:
                    path = app.download_path(query.get('id',[''])[0],query.get('name',[''])[0])
                self.headers_out(mimetypes.guess_type(path.name)[0] or 'application/octet-stream',path.stat().st_size,
                    extra={'Content-Disposition':"attachment; filename*=UTF-8''"+urllib.parse.quote(path.name)})
                with path.open('rb') as stream:
                    shutil.copyfileobj(stream,self.wfile)
            else:
                file = {'/':'index.html','/app.js':'app.js','/style.css':'style.css','/guide.html':'guide.html'}.get(parsed.path)
                if not file:
                    self.json_out({'error':'页面不存在'},404);return
                body = (ASSETS/file).read_bytes()
                content_type = {'html':'text/html','js':'text/javascript','css':'text/css'}[file.rsplit('.',1)[1]]
                self.headers_out(content_type+'; charset=utf-8',len(body));self.wfile.write(body)
        except PermissionError as error:
            self.json_out({'error':str(error)},403)
        except (BrokenPipeError,ConnectionResetError):
            pass
        except (ValueError,KeyError,OSError) as error:
            self.json_out({'error':str(error)},400)

    def do_POST(self):
        try:
            parsed = self.guard()
            length = int(self.headers.get('Content-Length','-1'))
            if length<0 or self.headers.get('Transfer-Encoding'):
                raise ValueError('需要明确的文件大小。')
            app = self.server.app
            if parsed.path=='/api/upload':
                query = urllib.parse.parse_qs(parsed.query)
                kind = query.get('kind',[''])[0]
                name = query.get('name',['file'])[0].replace('\\','/').rsplit('/',1)[-1][:180]
                suffix = Path(name).suffix.lower()
                if kind not in UPLOAD_SUFFIXES or suffix not in UPLOAD_SUFFIXES[kind]:
                    raise ValueError('文件格式不支持，请按页面说明选择文件。')
                limit = 3*1024**3 if kind in ('tc1','tc2') else 128*1024**2
                if not 0<length<=limit or length>shutil.disk_usage(app.root).free:
                    raise ValueError('文件过大或磁盘空间不足。')
                upload_id = uuid.uuid4().hex
                folder = app.root/'uploads'/upload_id
                folder.mkdir()
                remaining = length
                self.connection.settimeout(60)
                with (folder/('file'+suffix)).open('wb') as output:
                    while remaining:
                        chunk = self.rfile.read(min(1024**2,remaining))
                        if not chunk:
                            raise ValueError('文件传输中断，请重新选择文件。')
                        output.write(chunk);remaining-=len(chunk)
                write_json(folder/'upload.json',{'id':upload_id,'kind':kind,'name':name,'suffix':suffix,'bytes':length})
                self.json_out({'id':upload_id,'name':name,'bytes':length});return
            if length>2*1024**2:
                raise ValueError('请求内容过大。')
            body = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(body,dict):
                raise ValueError('请求格式不正确。')
            if parsed.path=='/api/start':
                self.json_out(app.start_task(body),202)
            elif parsed.path=='/api/demo':
                self.json_out(app.start_task({'mode':'offline'},demo=True),202)
            elif parsed.path=='/api/bundle-link':
                self.json_out(app.issue_bundle_link(body.get('id')))
            elif parsed.path=='/api/resume':
                self.json_out(app.resume(body.get('id')),202)
            elif parsed.path=='/api/cancel':
                self.json_out(app.cancel(body.get('id')))
            elif parsed.path=='/api/setup':
                if app.active():
                    raise ValueError('请先等待或停止当前分析。')
                action = body.get('action','download')
                if action not in ('download','import','check'):
                    raise ValueError('无效配置操作。')
                archives = {key:app.upload_path(body.get(key),key) for key in ('tc1','tc2')} if action=='import' else {}
                app.wizard.start(action,license_accepted=body.get('license_accepted') is True,
                    system_accepted=body.get('system_accepted') is True,archives=archives)
                self.json_out(app.wizard.snapshot(),202)
            elif parsed.path=='/api/quit':
                if app.active() or app.wizard.snapshot()['busy']:
                    raise ValueError('请先停止分析或等待首次配置结束。')
                self.json_out({'ok':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
            else:
                self.json_out({'error':'接口不存在'},404)
        except PermissionError as error:
            self.json_out({'error':str(error)},403)
        except (ValueError,TypeError,KeyError,OSError) as error:
            self.json_out({'error':str(error)},400)


def create_server(root, port=0):
    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads = True
    server.app = Application(root)
    return server


def smoke_test(root, local=False):
    server = create_server(root)
    thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    headers = {'X-App-Token':server.app.token,'Content-Type':'application/json'}
    def post(path,body):
        with urllib.request.urlopen(urllib.request.Request(url+path,data=json.dumps(body).encode(),headers=headers),timeout=30) as response:
            return json.load(response)
    try:
        if local:
            payload = {'name':'本地网页验证','mode':'local','fasta_text':(ASSETS/'examples/public_lcmv.fasta').read_text(encoding='utf-8'),
                'prediction':asdict(PredictionSettings(alleles_i=['HLA-A*02:01'],alleles_ii=['HLA-DRB1*01:01'],min_length_i=9,max_length_i=9))}
            task = post('/api/start',payload)
        else:
            task = post('/api/demo',{})
        deadline = time.monotonic()+300
        while time.monotonic()<deadline:
            value = server.app.task(task['id'])
            if value['status'] not in ('running','cancelling'):
                if value['status']!='complete':
                    raise ValueError(value['message'])
                file = next(f for f in value['files'] if f.endswith('results.xlsx'))
                request = urllib.request.Request(url+'/api/file?'+urllib.parse.urlencode({'id':task['id'],'name':file}),headers=headers)
                with urllib.request.urlopen(request) as response:
                    if not response.read(2)==b'PK':
                        raise ValueError('报告下载失败。')
                import zipfile
                import io
                with urllib.request.urlopen(urllib.request.Request(url+'/api/bundle?id='+task['id'],headers=headers)) as response:
                    with zipfile.ZipFile(io.BytesIO(response.read())) as bundle:
                        names = bundle.namelist()
                        if bundle.testzip() or not any(name.endswith('mhcii/step4_score_max.csv') for name in names):
                            raise ValueError('完整结果包缺少中间筛选结果。')
                print(json.dumps({'status':'passed','mode':value['mode'],'summary':value['summary'],'task_id':task['id']},ensure_ascii=False))
                return
            time.sleep(.2)
        raise TimeoutError('网页验证超时。')
    finally:
        server.shutdown();server.app.close();server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Epitope Screening 本地网页')
    parser.add_argument('--version',action='version',version=__version__)
    parser.add_argument('--data-dir',type=Path,default=default_data_directory())
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--smoke-test',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--smoke-test-local',action='store_true',help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.smoke_test or args.smoke_test_local:
        smoke_test(args.data_dir,args.smoke_test_local);return
    args.data_dir.mkdir(parents=True,exist_ok=True)
    lock_file = (args.data_dir/'process.lock').open('a')
    if os.name=='posix':
        import fcntl
        try:
            fcntl.flock(lock_file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            session = json.loads((args.data_dir/'session.json').read_text(encoding='utf-8'))
            if not args.no_browser:
                open_browser(session['url'])
            print('程序已运行。请在已打开的浏览器页面操作。',flush=True);return
    server = create_server(args.data_dir,args.port)
    url = f'http://127.0.0.1:{server.server_port}/#token={server.app.token}'
    write_json(args.data_dir/'session.json',{'url':url,'pid':os.getpid()})
    (args.data_dir/'session.json').chmod(0o600)
    print('表位筛选网页已启动。请保留此程序运行。\n'+url,flush=True)
    if not args.no_browser:
        open_browser(url)
    def stop(*_):
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    try:
        server.serve_forever()
    finally:
        server.app.close();server.server_close()
        (args.data_dir/'session.json').unlink(missing_ok=True)
        lock_file.close()


def open_browser(url):
    if sys.platform=='linux' and shutil.which('xdg-open'):
        subprocess.Popen(['xdg-open',url],env=external_environment(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


if __name__=='__main__':
    main()
