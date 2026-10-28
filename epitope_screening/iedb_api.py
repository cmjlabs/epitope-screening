"""Official online prediction, resumable job records, then the shared local core.

No requests are made until run_online() is called. Only HTTPS is supported.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pandas as pd

from . import __version__
from .core import Settings, Cancelled, normalize_table, read_sequence, run_analysis, sha256

BASE_URL = 'https://api-nextgen-tools.iedb.org/api/v1'
DEFAULT_I = ('HLA-A*01:01', 'HLA-A*02:01', 'HLA-A*03:01', 'HLA-A*11:01', 'HLA-A*24:02',
             'HLA-A*26:01', 'HLA-A*29:02', 'HLA-A*30:01', 'HLA-A*30:02', 'HLA-A*31:01',
             'HLA-A*32:01', 'HLA-A*33:01', 'HLA-A*68:01', 'HLA-A*68:02', 'HLA-B*07:02',
             'HLA-B*08:01', 'HLA-B*15:01', 'HLA-B*35:01', 'HLA-B*40:01', 'HLA-B*44:02',
             'HLA-B*44:03', 'HLA-B*51:01', 'HLA-B*53:01', 'HLA-B*57:01', 'HLA-B*58:01',
             'HLA-C*07:01', 'HLA-C*07:02')
DEFAULT_II = ('DRB1*01:01', 'DRB1*03:01', 'DRB1*04:01', 'DRB1*04:05', 'DRB1*07:01',
              'DRB1*08:02', 'DRB1*09:01', 'DRB1*11:01', 'DRB1*12:01', 'DRB1*13:02',
              'DRB1*15:01', 'DRB3*01:01', 'DRB3*02:02', 'DRB4*01:01', 'DRB5*01:01',
              'DQA1*05:01/DQB1*02:01', 'DQA1*05:01/DQB1*03:01', 'DQA1*03:01/DQB1*03:02',
              'DQA1*04:01/DQB1*04:02', 'DQA1*01:01/DQB1*05:01', 'DQA1*01:02/DQB1*06:02',
              'DPA1*02:01/DPB1*01:01', 'DPA1*01:03/DPB1*02:01', 'DPA1*01:03/DPB1*04:01',
              'DPA1*03:01/DPB1*04:02', 'DPA1*02:01/DPB1*05:01', 'DPA1*02:01/DPB1*14:01')


class APIError(ValueError):
    pass


def canonical_allele(value):
    return value.upper().replace('HLA-', '').replace(' ', '')


@dataclass
class PredictionSettings:
    alleles_i: list = field(default_factory=lambda: list(DEFAULT_I))
    alleles_ii: list = field(default_factory=lambda: list(DEFAULT_II))
    min_length_i: int = 8
    max_length_i: int = 14
    min_length_ii: int = 15
    max_length_ii: int = 15
    method_i: str = 'netmhcpan_ba'
    method_ii: str = 'netmhciipan_ba-4.3'
    allele_batch_size: int = 4
    poll_seconds: float = 10
    job_timeout_seconds: float = 1800
    proxy_url: str = ''

    def validate(self, sequence_length):
        for tag, lower, upper in [('i', 8, 15), ('ii', 11, 30)]:
            alleles = getattr(self, 'alleles_' + tag)
            if not isinstance(alleles, list) or not alleles or any(not isinstance(a, str) or not a.strip() or any(c in a for c in ',;\n\r') for a in alleles):
                raise ValueError(f'alleles_{tag} 必须为非空 HLA 字符串列表。')
            if len(set(map(canonical_allele, alleles))) != len(alleles):
                raise ValueError(f'alleles_{tag} 含重复 HLA。')
            low, high = getattr(self, 'min_length_' + tag), getattr(self, 'max_length_' + tag)
            if type(low) is not int or type(high) is not int or not lower <= low <= high <= min(upper, sequence_length):
                raise ValueError(f'MHC-{tag.upper()} 肽长需为 {lower}–{upper} 内的整数，且不能超过序列长度。')
        if self.method_i not in ('netmhcpan_ba', 'netmhcpan_ba-4.2') or self.method_ii not in ('netmhciipan_ba', 'netmhciipan_ba-4.2', 'netmhciipan_ba-4.3'):
            raise ValueError('在线筛选仅接受返回 IC50 的已支持 BA 模型。')
        if type(self.allele_batch_size) is not int or not 1 <= self.allele_batch_size <= 8:
            raise ValueError('allele_batch_size 需为 1–8，任务按批顺序运行。')
        for key, minimum in [('poll_seconds', 5), ('job_timeout_seconds', 30)]:
            value = getattr(self, key)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
                raise ValueError(f'{key} 不能小于 {minimum} 秒。')
        if self.proxy_url:
            proxy = urllib.parse.urlsplit(self.proxy_url)
            if proxy.scheme not in ('http', 'https') or not proxy.hostname or proxy.username or proxy.password:
                raise ValueError('代理需为不含账号或密码的 HTTP(S) URL。')


def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def check_cancel(stop):
    if stop is not None and stop.is_set():
        raise Cancelled('本地任务已取消。已提交的 IEDB 远程任务可能继续运行，可恢复查询。')


class APIClient:
    def __init__(self, proxy_url='', opener=None):
        proxy = urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url}) if proxy_url else urllib.request.ProxyHandler()
        self.opener = opener or urllib.request.build_opener(proxy)

    def request(self, path, payload=None):
        if not path.startswith(('/mhci', '/mhcii', '/pipeline', '/results/')) or '://' in path:
            raise APIError('拒绝非 IEDB 官方 API 请求路径。')
        request = urllib.request.Request(BASE_URL + path,
            data=None if payload is None else json.dumps(payload).encode('utf-8'),
            headers={'Accept': 'application/json', 'Content-Type': 'application/json',
                     'User-Agent': f'EpitopeScreening/{__version__}'})
        try:
            with self.opener.open(request, timeout=45) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read(2048).decode('utf-8', errors='replace')
            raise APIError(f'IEDB HTTP {error.code}: {detail}') from error
        if not isinstance(result, dict):
            raise APIError('IEDB 返回的响应不是 JSON 对象。')
        return result

    def get(self, path, stop=None):
        # GET can be retried; POST must never be blindly retried after a lost response.
        for attempt in range(3):
            check_cancel(stop)
            try:
                return self.request(path)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                if attempt == 2:
                    raise APIError(f'连接 IEDB 失败：{error}；可检查网络或设置代理，再恢复任务。') from error
                if stop is not None:
                    stop.wait(2 ** attempt)
                else:
                    time.sleep(2 ** attempt)


def build_payload(sequence, group, alleles, prediction):
    tag = 'i' if group == 'mhci' else 'ii'
    parameters = {'alleles': ','.join(alleles),
                  'peptide_length_range': [getattr(prediction, 'min_length_' + tag), getattr(prediction, 'max_length_' + tag)],
                  'predictors': [{'type': 'binding', 'method': getattr(prediction, 'method_' + tag)}]}
    parameters['predictors'].append({'type': 'immunogenicity', 'mask_choice': 'default'} if tag == 'i' else {'type': 'immunogenicity', 'method': 'cd4episcore'})
    if tag == 'ii':
        parameters['peptide_spacing'] = 1
    return {'pipeline_id': '', 'run_stage_range': [1, 1], 'stages': [{
        'stage_number': 1, 'tool_group': group,
        'input_sequence_text': '>protein\n' + sequence, 'input_parameters': parameters}]}


def parse_prediction(result, group, alleles, prediction, sequence):
    data = result.get('data', {})
    if result.get('status') != 'done' or data.get('errors') or result.get('errors'):
        raise APIError(f'IEDB {group} 未成功完成：{data.get("errors") or result.get("errors") or result.get("status")}')
    tables = [t for t in data.get('results', []) if t.get('type') == 'peptide_table']
    if len(tables) != 1 or not tables[0].get('table_data'):
        raise APIError(f'IEDB {group} 缺少非空 peptide_table。')
    table = tables[0]
    columns = table.get('table_columns', [])
    tag = 'i' if group == 'mhci' else 'ii'
    method = getattr(prediction, 'method_' + tag)
    fields = {'peptide': ('core', 'peptide'), 'allele': ('core', 'allele'),
              'start': ('core', 'start'), 'end': ('core', 'end'),
              'ic50': ('binding/elution', method + '_ic50'),
              'rank': ('binding/elution', method + '_percentile'),
              'score': ('immunogenicity' if tag == 'i' else 'immunogenicity.cd4episcore', 'score')}
    mapping = {}
    for key, pair in fields.items():
        indices = [i for i, c in enumerate(columns) if (c.get('source'), c.get('name')) == pair]
        if len(indices) != 1:
            raise APIError(f'IEDB {group} 缺少或重复 {key} 列：{pair}。不会替换为其他模型或评分。')
        mapping[key] = indices[0]
    if any(len(row) != len(columns) for row in table['table_data']):
        raise APIError('IEDB 返回不完整数据行。')
    frame = pd.DataFrame([{key: row[index] for key, index in mapping.items()} for row in table['table_data']])
    frame = normalize_table(frame, 'I' if tag == 'i' else 'II', sequence)
    observed = set()
    for row in frame.itertuples():
        key = (canonical_allele(row.allele), row.start, row.end)
        if key in observed:
            raise APIError('IEDB 返回重复的 HLA–坐标结果。')
        observed.add(key)
    expected = {(canonical_allele(a), start, start + length - 1)
                for a in alleles
                for length in range(getattr(prediction, 'min_length_' + tag), getattr(prediction, 'max_length_' + tag) + 1)
                for start in range(1, len(sequence) - length + 2)}
    if observed != expected:
        raise APIError(f'IEDB {group} 预测不完整：缺少 {len(expected - observed)} 条，额外 {len(observed - expected)} 条。')
    return frame.sort_values(['start', 'end', 'allele']).reset_index(drop=True)


def predict_batch(client, payload, folder, prediction, progress, stop):
    folder.mkdir(parents=True, exist_ok=True)
    request_file, submitted_file = folder / 'request.json', folder / 'submitted.json'
    marker = folder / 'submission_state.json'
    if request_file.exists() and json.loads(request_file.read_text(encoding='utf-8')) != payload:
        raise APIError('恢复任务的请求参数与原任务不同。')
    write_json(request_file, payload)
    check_cancel(stop)
    if submitted_file.exists():
        submitted = json.loads(submitted_file.read_text(encoding='utf-8'))
    else:
        if marker.exists():
            raise APIError(f'上一提交未取得确认，不能自动重复 POST。请核对远程任务。记录：{folder}')
        write_json(marker, {'status': 'submitting', 'started_at': datetime.now(timezone.utc).isoformat()})
        try:
            submitted = client.request('/pipeline', payload)
            write_json(submitted_file, submitted)
        except Exception as error:
            write_json(marker, {'status': 'submission_unknown', 'error': str(error)})
            raise APIError(f'IEDB 提交响应未确认：{error}。已保存记录，禁止自动重发以免重复提交。') from error
    if submitted.get('errors') or not submitted.get('result_id'):
        raise APIError(f'IEDB 拒绝任务：{submitted.get("errors") or submitted}')
    for warning in submitted.get('warnings', []):
        progress('IEDB 提示：' + str(warning))
    result_id = submitted['result_id']
    if not isinstance(result_id, str) or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._' for c in result_id):
        raise APIError('IEDB 返回无效 result_id。')
    result_file = folder / 'result.json'
    if result_file.exists():
        cached = json.loads(result_file.read_text(encoding='utf-8'))
        if cached.get('status') == 'done':
            return cached
    deadline = time.monotonic() + prediction.job_timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        check_cancel(stop)
        result = client.get('/results/' + result_id, stop)
        write_json(result_file, result)
        status = result.get('status')
        if status != last_status:
            progress(f'IEDB {result_id}: {status}')
            last_status = status
        if result.get('data', {}).get('errors') or result.get('errors'):
            raise APIError(f'IEDB 任务错误：{result.get("data", {}).get("errors") or result.get("errors")}')
        if status == 'done':
            return result
        if status not in ('pending', 'running', 'queued', 'processing', 'started'):
            raise APIError(f'IEDB 任务异常状态：{status}')
        if stop is not None:
            stop.wait(prediction.poll_seconds)
        else:
            time.sleep(prediction.poll_seconds)
    raise APIError(f'IEDB 任务等待超时；远程任务未删除，可从 {folder.parent.parent} 恢复。')


def run_online(fasta, output_root, settings=None, prediction=None, cutoff=None, progress=None, stop=None, resume=None, client=None):
    """Create one reproducible session; resume reuses confirmed remote result IDs."""
    settings, prediction = settings or Settings(), prediction or PredictionSettings()
    settings.validate()
    sequence = read_sequence(fasta)
    prediction.validate(len(sequence))
    if settings.max_window > len(sequence):
        raise ValueError('最大窗口不能超过序列长度。')
    from .core import DATA
    cutoff = Path(cutoff) if cutoff else DATA / 'class_I_allele_specific_cutoff.csv'
    plan = {'sequence': sequence, 'prediction': asdict(prediction), 'screening': asdict(settings),
            'cutoff_sha256': sha256(cutoff) if settings.use_allele_cutoff else None}
    # Network routing may be changed between retries without invalidating predictions.
    for option in ('proxy_url', 'poll_seconds', 'job_timeout_seconds'):
        plan['prediction'].pop(option)
    fingerprint = hashlib.sha256(json.dumps(plan, sort_keys=True).encode('utf-8')).hexdigest()
    if resume:
        output = Path(resume).resolve()
        manifest = json.loads((output / 'pipeline_manifest.json').read_text(encoding='utf-8'))
        if manifest.get('request_fingerprint') != fingerprint:
            raise ValueError('FASTA 或分析参数与原任务不同，请新建任务。')
    else:
        output = Path(output_root).resolve() / ('online_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:8])
        output.mkdir(parents=True, exist_ok=False)
        manifest = {'version': __version__, 'started_at': datetime.now(timezone.utc).isoformat(),
                    'request_fingerprint': fingerprint, 'prediction': plan['prediction'], 'screening': asdict(settings),
                    'base_url': BASE_URL, 'source_fasta_sha256': sha256(fasta), 'warnings': []}
        (output / 'input.fasta').write_text('>protein\n' + sequence + '\n', encoding='utf-8')
        if settings.use_allele_cutoff:
            (output / 'cutoff.csv').write_bytes(cutoff.read_bytes())
    if read_sequence(output / 'input.fasta') != sequence:
        raise ValueError('任务内的 FASTA 快照被修改。')
    if settings.use_allele_cutoff and sha256(output / 'cutoff.csv') != plan['cutoff_sha256']:
        raise ValueError('任务内的 cutoff 快照被修改。')
    manifest.update(status='running', last_started_at=datetime.now(timezone.utc).isoformat())
    manifest.pop('error', None)
    manifest_path = output / 'pipeline_manifest.json'
    write_json(manifest_path, manifest)
    progress = progress or (lambda percent, message: None)
    client = client or APIClient(prediction.proxy_url)
    progress(0, f'在线任务目录：{output}')
    try:
        groups = [('mhci', prediction.alleles_i), ('mhcii', prediction.alleles_ii)]
        total = sum(math.ceil(len(a) / prediction.allele_batch_size) for _, a in groups)
        completed = 0
        for group, alleles in groups:
            check_cancel(stop)
            snapshot = output / (group + '_parameters.json')
            if not snapshot.exists():
                write_json(snapshot, client.get('/' + group, stop))
            batches = []
            for index, start in enumerate(range(0, len(alleles), prediction.allele_batch_size)):
                batch = alleles[start:start + prediction.allele_batch_size]
                folder = output / 'predictions' / f'{group}_{index:03d}'
                payload = build_payload(sequence, group, batch, prediction)
                progress(int(70 * completed / total), f'{group} 批次 {index + 1}：{", ".join(batch)}')
                result = predict_batch(client, payload, folder, prediction,
                    lambda message: progress(int(70 * completed / total), message), stop)
                frame = parse_prediction(result, group, batch, prediction, sequence)
                frame.to_csv(folder / 'normalized.csv', index=False, encoding='utf-8-sig')
                batches.append(frame)
                submitted = json.loads((folder / 'submitted.json').read_text(encoding='utf-8'))
                warnings = submitted.get('warnings', []) + result.get('data', {}).get('warnings', [])
                for warning in warnings:
                    if warning not in manifest['warnings']:
                        manifest['warnings'].append(warning)
                        progress(int(70 * completed / total), 'IEDB 提示：' + str(warning))
                manifest.setdefault('jobs', {})[f'{group}_{index:03d}'] = {'result_id': submitted['result_id'],
                    'rows': len(frame), 'request_sha256': sha256(folder / 'request.json'), 'result_sha256': sha256(folder / 'result.json')}
                completed += 1
                write_json(manifest_path, manifest)
            pd.concat(batches, ignore_index=True).to_csv(output / 'predictions' / (group + '.csv'), index=False, encoding='utf-8-sig')
        check_cancel(stop)
        screening = run_analysis(output / 'input.fasta', output / 'predictions/mhci.csv', output / 'predictions/mhcii.csv',
            output / 'screening', settings, output / 'cutoff.csv' if settings.use_allele_cutoff else None,
            progress=lambda percent, message: progress(70 + int(percent * 0.29), message), stop=stop)
        manifest.update(status='complete', screening_directory=str(screening.relative_to(output)),
                        finished_at=datetime.now(timezone.utc).isoformat())
        manifest['output_hashes'] = {str(p.relative_to(output)): sha256(p) for p in output.rglob('*') if p.is_file() and p != manifest_path}
        write_json(manifest_path, manifest)
        progress(100, f'完整流程完成，结果：{screening / "results.xlsx"}')
        return output
    except Exception as error:
        manifest.update(status='cancelled' if isinstance(error, Cancelled) else 'failed', error=str(error))
        write_json(manifest_path, manifest)
        raise
