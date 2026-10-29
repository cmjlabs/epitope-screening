"""Local Linux IEDB TC1/TC2 execution. No prediction API or Docker dependency."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

import pandas as pd

from . import __version__
from .core import DATA, Settings, Cancelled, read_sequence, normalize_table, run_analysis, sha256
from .iedb_api import PredictionSettings, canonical_allele, api_allele, write_json, check_cancel


def tool_files(root):
    return sorted(p for p in Path(root).rglob('*') if p.is_file() and '__pycache__' not in p.parts)


def tool_inventory(root):
    root=Path(root)
    records=[(str(p.relative_to(root)),p.stat().st_size,p.stat().st_mtime_ns) for p in tool_files(root)]
    return hashlib.sha256(json.dumps(records).encode()).hexdigest()


def tool_signature(root):
    root=Path(root)
    digest=hashlib.sha256()
    for path in tool_files(root):
        digest.update(str(path.relative_to(root)).encode()+b'\0'+sha256(path).encode()+b'\n')
    return {'sha256':digest.hexdigest(),'inventory':tool_inventory(root)}


def normalize_local_result(raw, group, alleles, prediction, sequence):
    """Expand peptide-only BA output to ALL source positions, preserving repeats."""
    if raw.get('errors'):
        raise ValueError(f'本地 IEDB 错误：{raw["errors"]}')
    tag = 'i' if group == 'mhci' else 'ii'
    binding_name = 'binding.' + getattr(prediction, 'method_' + tag)
    score_name = 'immunogenicity' if tag == 'i' else 'immunogenicity.cd4episcore'
    tables = {}
    for name in (binding_name, score_name):
        found = [t for t in raw.get('results', []) if t.get('method') == name and t.get('type') == 'peptide_table']
        if len(found) != 1 or not found[0].get('table_data'):
            raise ValueError(f'本地 IEDB 缺少唯一非空结果表：{name}')
        table = found[0]
        tables[name] = pd.DataFrame(table['table_data'], columns=table['table_columns'])
    binding, scores = tables[binding_name], tables[score_name]
    for frame in (binding, scores):
        if 'allele' in frame:
            frame['allele'] = frame.allele.map(canonical_allele)
        if 'start' in frame and 'end' in frame:
            for row in frame.itertuples():
                start, end = int(row.start), int(row.end)
                if start < 1 or end > len(sequence) or sequence[start-1:end] != row.peptide:
                    raise ValueError('本地 IEDB 返回的坐标与 FASTA 不匹配。')
    key = ['allele', 'peptide']
    binding = binding[key + ['ic50', 'percentile']].drop_duplicates()
    score_key = key if 'allele' in scores else ['peptide']
    scores = scores[score_key + ['score']].drop_duplicates()
    if binding.duplicated(key).any() or scores.duplicated(score_key).any():
        raise ValueError('本地 IEDB 对同一 HLA/肽段返回冲突的预测值。')
    expected = pd.DataFrame([{'allele': canonical_allele(a), 'peptide': sequence[s:s+length],
                             'start': s+1, 'end': s+length}
        for a in alleles
        for length in range(getattr(prediction, 'min_length_' + tag), getattr(prediction, 'max_length_' + tag)+1)
        for s in range(len(sequence)-length+1)])
    expected_pairs = set(zip(expected.allele, expected.peptide))
    if set(zip(binding.allele, binding.peptide)) != expected_pairs:
        raise ValueError('本地 IEDB 结合预测不完整或含额外 HLA/肽段。')
    frame = expected.merge(binding, on=key, how='left', validate='many_to_one')
    frame = frame.merge(scores, on=score_key, how='left', validate='many_to_one')
    frame['allele'] = 'HLA-' + frame.allele
    return normalize_table(frame, 'I' if tag == 'i' else 'II', sequence)


def local_payload(sequence, group, alleles, prediction):
    tag = 'i' if group == 'mhci' else 'ii'
    return {'input_sequence_text': '>protein\n' + sequence,
            'peptide_length_range': [getattr(prediction, 'min_length_' + tag), getattr(prediction, 'max_length_' + tag)],
            'alleles': ','.join(api_allele(a) for a in alleles),
            'predictors': [{'type': 'binding', 'method': getattr(prediction, 'method_' + tag)},
                {'type': 'immunogenicity', 'mask_choice': 'default'} if tag == 'i' else
                {'type': 'immunogenicity', 'method': 'cd4episcore', 'peptide_spacing': 1}]}


def validate_runtime(config):
    if sys.platform != 'linux' or platform.machine() != 'x86_64':
        raise ValueError('本地后端目前支持 Linux x86_64；Windows 请使用在线模式。')
    paths = {key: Path(config[key]).expanduser().resolve() for key in ('tc1', 'tc2')}
    # Preserve venv/bin/python: resolving its symlink would bypass the venv.
    paths['python'] = Path(config['python']).expanduser().absolute()
    for key, group in [('tc1', 'mhci'), ('tc2', 'mhcii')]:
        if any(c.isspace() or c in "'\"`$;&|<>(){}*?[]\\!" for c in str(paths[key])):
            raise ValueError('官方 TC1/TC2 工具目录不能包含空格或 shell 特殊字符，请换到简单路径。')
        if not (paths[key] / 'src' / ('tcell_' + group + '.py')).is_file():
            raise ValueError(f'{key} 需指向含 src/tcell_{group}.py 的官方工具目录。')
        signature=config.get('tool_signatures',{}).get(key)
        if signature and signature['inventory'] != tool_inventory(paths[key]):
            raise ValueError(f'{key} 安装内容已变化，请重新配置并创建新任务。')
    if not paths['python'].is_file():
        raise ValueError('本地工具 Python 不存在，请重新运行 setup_local_linux.sh。')
    for name in ('tcsh', 'gawk', 'perl', 'R'):
        if shutil.which(name) is None:
            raise ValueError(f'本地预测依赖 {name}，请先运行 setup_local_linux.sh。')
    workers = config.get('workers', 2)
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError('本地 workers 需为 1–8。')
    probe = subprocess.run([str(paths['python']), '-c',
        'import sys,pandas,numpy; assert sys.version_info[:2]==(3,11); assert pandas.__version__=="1.5.2"; assert numpy.__version__=="1.26.4"'],
        capture_output=True, text=True, timeout=30)
    if probe.returncode:
        raise ValueError('本地工具运行环境需为 Python 3.11、pandas 1.5.2、numpy 1.26.4；请运行 setup_local_linux.sh 配置独立环境。')
    return paths, workers


def execute_job(command, folder, stop):
    """Cancel the complete native predictor process group, without using a shell."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    with (folder / 'run.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        started = time.monotonic()
        try:
            while process.poll() is None:
                check_cancel(stop)
                if time.monotonic() - started > 7200:
                    raise TimeoutError('本地单批预测超过 2 小时，已停止。')
                time.sleep(.2)
            if process.returncode:
                raise ValueError(f'本地预测退出码 {process.returncode}，详情：{folder / "run.log"}')
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    return time.monotonic() - started


def run_local(fasta, output_root, tools_config, settings=None, prediction=None, cutoff=None,
              progress=None, stop=None, resume=None):
    settings, prediction = settings or Settings(), prediction or PredictionSettings()
    stop = stop or threading.Event()
    settings.validate()
    sequence = read_sequence(fasta)
    prediction.validate(len(sequence))
    if settings.max_window > len(sequence):
        raise ValueError('最大窗口不能超过序列长度。')
    paths, workers = validate_runtime(tools_config)
    cutoff = Path(cutoff) if cutoff else DATA / 'class_I_allele_specific_cutoff.csv'
    plan = {'sequence': sequence, 'prediction': asdict(prediction), 'screening': asdict(settings),
            'cutoff_sha256': sha256(cutoff) if settings.use_allele_cutoff else None,
            'tools': {key: {'path': str(paths[key]), 'entry_sha256': sha256(paths[key]/'src'/('tcell_'+group+'.py'))}
                      for key, group in [('tc1','mhci'), ('tc2','mhcii')]}, 'python': str(paths['python']),
            'tool_signatures':tools_config.get('tool_signatures',{})}
    for option in ('proxy_url', 'poll_seconds', 'job_timeout_seconds'):
        plan['prediction'].pop(option)
    fingerprint = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    output = Path(resume).resolve() if resume else Path(output_root).resolve() / ('local_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:8])
    if resume:
        manifest = json.loads((output/'local_manifest.json').read_text(encoding='utf-8'))
        if manifest['fingerprint'] != fingerprint:
            raise ValueError('本地恢复任务的输入、参数或工具环境已改变。')
    else:
        output.mkdir(parents=True, exist_ok=False)
        manifest = {'version': __version__, 'backend': 'local-linux', 'fingerprint': fingerprint,
                    'plan': plan, 'workers': workers, 'jobs': {}}
        (output/'input.fasta').write_text('>protein\n'+sequence+'\n',encoding='utf-8')
        if settings.use_allele_cutoff:
            shutil.copyfile(cutoff, output/'cutoff.csv')
    progress = progress or (lambda p,m: None)
    if read_sequence(output/'input.fasta') != sequence:
        raise ValueError('本地任务 FASTA 快照已被修改。')
    if settings.use_allele_cutoff and sha256(output/'cutoff.csv') != plan['cutoff_sha256']:
        raise ValueError('本地任务 cutoff 快照已被修改。')
    manifest['status'] = 'running'
    write_json(output/'local_manifest.json',manifest)
    progress(0, f'本地任务目录：{output}；并行 {workers}，不调用预测 API')
    if (prediction.min_length_ii, prediction.max_length_ii) != (15,15):
        message = 'CD4Episcore 仅在 15mer 验证，其他长度的有效性未知。'
        manifest['warnings'] = [message]
        progress(0,message)
    jobs=[]
    for group in ('mhci','mhcii'):
        alleles = getattr(prediction,'alleles_'+('i' if group=='mhci' else 'ii'))
        for i,start in enumerate(range(0,len(alleles),prediction.allele_batch_size)):
            jobs.append((group,i,alleles[start:start+prediction.allele_batch_size]))
    def work(job):
        group,index,alleles=job
        folder=output/'predictions'/f'{group}_{index:03d}'
        folder.mkdir(parents=True,exist_ok=True)
        payload=local_payload(sequence,group,alleles,prediction)
        write_json(folder/'request.json',payload)
        result=folder/'result.json'
        cache=manifest['jobs'].get(folder.name,{})
        check_cancel(stop)
        if result.exists() and cache.get('result_sha256') == sha256(result):
            raw=json.loads(result.read_text())
            elapsed=cache['elapsed_seconds']
        else:
            tool=paths['tc1' if group=='mhci' else 'tc2']/'src'/('tcell_'+group+'.py')
            elapsed=execute_job([str(paths['python']),str(tool),'-j',str(folder/'request.json'),'-o',str(folder/'result'),'-f','json'],folder,stop)
            if not result.exists():
                raise ValueError(f'本地 IEDB 未生成结果，详情：{folder/"run.log"}')
            raw=json.loads(result.read_text())
        frame=normalize_local_result(raw,group,alleles,prediction,sequence)
        frame.to_csv(folder/'normalized.csv',index=False,encoding='utf-8-sig')
        return group,folder,frame,{'elapsed_seconds':elapsed,'rows':len(frame),'result_sha256':sha256(result),'warnings':raw.get('warnings',[])}
    started=time.monotonic()
    try:
        frames={'mhci':[],'mhcii':[]}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures=[pool.submit(work,job) for job in jobs]
            for done,future in enumerate(as_completed(futures),1):
                try:
                    group,folder,frame,record=future.result()
                except Exception:
                    stop.set()
                    for pending in futures:
                        pending.cancel()
                    raise
                frames[group].append(frame)
                manifest['jobs'][folder.name]=record
                write_json(output/'local_manifest.json',manifest)
                progress(int(70*done/len(jobs)),f'{folder.name}：{len(frame)} 行，{record["elapsed_seconds"]:.1f} 秒')
        manifest['prediction_seconds']=time.monotonic()-started
        for group in frames:
            pd.concat(frames[group],ignore_index=True).sort_values(['allele','start','end']).to_csv(output/'predictions'/(group+'.csv'),index=False,encoding='utf-8-sig')
        result=run_analysis(output/'input.fasta',output/'predictions/mhci.csv',output/'predictions/mhcii.csv',output/'screening',settings,
            output/'cutoff.csv' if settings.use_allele_cutoff else None,progress=lambda p,m:progress(70+int(p*.29),m),stop=stop)
        manifest.update(status='complete',elapsed_seconds=time.monotonic()-started,screening_directory=str(result.relative_to(output)))
        progress(100,f'本地流程完成：{result/"results.xlsx"}')
        return output
    except Exception as error:
        manifest.update(status='cancelled' if isinstance(error,Cancelled) else 'failed',error=str(error))
        raise
    finally:
        write_json(output/'local_manifest.json',manifest)
