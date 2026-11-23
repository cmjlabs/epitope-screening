"""Self-contained audit download: inputs, native predictions, every filter stage and final exports."""
import hashlib
from html import escape
import json
from pathlib import Path
import urllib.parse
import zipfile

from .core import sha256


def build_result_bundle(job_folder, record):
    root = Path(job_folder).resolve()
    target = root / 'complete_results.zip'
    temporary = root / 'complete_results.zip.tmp'
    excluded = {'task.json',target.name,temporary.name}
    files = [p for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink()
        and p.resolve().is_relative_to(root) and p.name not in excluded and not p.name.endswith('.tmp')]
    sections = {'inputs': [], 'predictions': [], 'filtering': [], 'results': [], 'records': []}
    for path in files:
        relative = path.relative_to(root)
        parts = relative.parts
        if 'predictions' in parts:
            group = 'predictions'
        elif 'mhci' in parts or 'mhcii' in parts:
            group = 'filtering'
        elif path.name in ('all_windows.csv','selected_windows.csv','results.xlsx','coverage_step1_mhci.csv','coverage_step2_mhcii.csv','filter_summary.csv','screening_settings.csv'):
            group = 'results'
        elif 'inputs' in parts or len(parts)==1 or path.name in ('input.fasta','cutoff.csv'):
            group = 'inputs'
        else:
            group = 'records'
        sections[group].append('files/'+relative.as_posix())
    counts = ''
    report_path = record.get('report_directory')
    if report_path:
        manifest = Path(report_path)/'run_manifest.json'
        if manifest.is_file():
            value = json.loads(manifest.read_text(encoding='utf-8'))
            rows = []
            for cls in ('I','II'):
                for stage, amount in value.get('step_counts_'+cls,{}).items():
                    rows.append(f'<tr><td>MHC-{cls}</td><td>{escape(stage)}</td><td>{amount}</td></tr>')
            counts = '<h2>筛选行数</h2><table><tr><th>类别</th><th>阶段</th><th>保留行数</th></tr>'+''.join(rows)+'</table>'
    descriptions = [
        ('inputs','01 输入与参数','原始 FASTA、导入的预测表、实际使用的 cutoff 快照。预测参数与模型信息见运行记录。'),
        ('predictions','02 原始 IEDB 预测','本地/在线预测的每批 request、未经筛选的原始 result、运行日志及合并表；原生返回的额外列也保留。导入表模式没有新生成的 IEDB 预测。'),
        ('filtering','03 每一个阈值的中间结果','按顺序：step0_raw 标准化未筛选 → step1_ic50 → step2_rank → step3_score_min → step4_score_max → final HLA 拆分去重。每个 CSV 都保留表头，零行也会导出。'),
        ('results','04 窗口、覆盖率与最终结果','all_windows 为全部窗口；coverage_step1_mhci 为通过 I 类覆盖率的窗口；coverage_step2_mhcii 在前一步基础上应用 II 类阈值，与 selected_windows 相同。filter_summary 记录各阶段规则和行数，results.xlsx 包含最终数据、阶段摘要和筛选参数。'),
        ('records','05 运行记录','软件版本、模型签名、参数、输入与输出校验值、原始任务记录等。task_record.json 记录网页任务状态；失败/取消时只包含截至该时点的实际文件。')]
    content = []
    for key, title, description in descriptions:
        links = ''.join('<li><a href="'+urllib.parse.quote(name,safe='/')+'">'+escape(name)+'</a></li>' for name in sections[key])
        content.append('<h2>'+title+'</h2><p>'+description+'</p><ul>'+links+'</ul>')
    html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>完整结果包说明</title><style>body{max-width:1050px;margin:40px auto;padding:0 24px;font:15px/1.8 system-ui,sans-serif;color:#243c33;background:#f8faf8}h1,h2{color:#1c634e}h2{margin-top:32px}a{color:#236e57;overflow-wrap:anywhere}table{border-collapse:collapse}th,td{padding:6px 18px;border:1px solid #ccdcd4}li{margin:5px 0}code{background:#eaf1ec;padding:3px}</style><h1>从原始预测到最终候选：完整结果包</h1>'''
    html += '<p>任务：'+escape(record.get('name',''))+'；状态：'+escape(record['status'])+'</p>'
    html += '<p>请先完整解压 ZIP，再打开本说明。覆盖率使用 0–1；阈值按顺序累计应用。MHC-I 专属 cutoff 启用时优先于统一 IC50。拆分 HLA 链可能增加行数。原始预测不会被筛选表覆盖。</p>'
    html += '<p>原始 JSON、请求和日志在 files/output/ 下保留原目录。CSV 可用 Excel/LibreOffice 导入，序列与 HLA 建议按文本读取。SHA256SUMS.txt 列出包内文件校验值；不包含 IEDB 模型安装包和可执行环境。</p>'
    html += counts+''.join(content)+'</html>'
    extras = {'先看这里.html':html.encode('utf-8'),
        'task_record.json':json.dumps(record,ensure_ascii=False,indent=2).encode('utf-8')}
    checks = []
    with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as archive:
        for path in files:
            name = 'files/'+path.relative_to(root).as_posix()
            archive.write(path,name)
            checks.append(sha256(path)+'  '+name)
        for name, data in extras.items():
            archive.writestr(name,data)
            checks.append(hashlib.sha256(data).hexdigest()+'  '+name)
        archive.writestr('SHA256SUMS.txt','\n'.join(checks)+'\n')
    temporary.replace(target)
    return target
