"""Offline screening shared by the CLI and desktop application."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import uuid

import numpy as np
import openpyxl
import pandas as pd

from . import __version__
from .vendor.population_coverage.population_calculation import PopulationCoverage
from .vendor.population_coverage.population_coverage_pickle import population_coverage

DATA = Path(__file__).parent / 'data'
FREQUENCIES = Path(__file__).parent / 'vendor/population_coverage/population_coverage_pickle/population_genotype_map.p.gz'


class Cancelled(Exception):
    pass


@dataclass
class Settings:
    min_window: int = 20
    max_window: int = 30
    population: str = 'World'
    coverage_threshold: float = 0.8
    use_allele_cutoff: bool = True
    ic50_i: float = 500
    rank_i: float = 2
    score_i_min: float = 0
    score_i_max: float = 100
    score_i_strict_min: bool = False
    ic50_ii: float = 1000
    rank_ii: float = 10
    score_ii_min: float = -100
    score_ii_max: float = 66

    def validate(self):
        if type(self.min_window) is not int or type(self.max_window) is not int or not 1 <= self.min_window <= self.max_window:
            raise ValueError('窗口必须是正整数，且最小窗口不能大于最大窗口。')
        if type(self.use_allele_cutoff) is not bool or type(self.score_i_strict_min) is not bool:
            raise ValueError('布尔参数必须为 true / false。')
        for name, value in asdict(self).items():
            if name != 'population' and type(value) is not bool and not math.isfinite(value):
                raise ValueError(f'{name} 必须是有限数值。')
        if not 0 <= self.coverage_threshold <= 1:
            raise ValueError('覆盖率阈值应为 0–1 的比例。')
        for tag in ('i', 'ii'):
            if getattr(self, f'ic50_{tag}') < 0 or not 0 <= getattr(self, f'rank_{tag}') <= 100:
                raise ValueError('IC50 应非负，rank 应介于 0–100。')
            if getattr(self, f'score_{tag}_min') > getattr(self, f'score_{tag}_max'):
                raise ValueError('评分下限不能大于上限。')
        if self.population not in populations():
            raise ValueError(f'未知人群：{self.population}；可运行 populations 命令查询。')


def populations():
    return sorted(set(population_coverage['I']) & set(population_coverage['II']))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_sequence(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    records = sum(line.lstrip().startswith('>') for line in text.splitlines())
    if records != 1:
        raise ValueError('每次分析需要一个含单条蛋白序列的 FASTA 文件。')
    sequence = ''.join(line.strip() for line in text.splitlines() if not line.lstrip().startswith('>')).upper()
    if not sequence or re.search('[^ACDEFGHIKLMNPQRSTVWY]', sequence):
        raise ValueError('FASTA 只能包含 20 种标准氨基酸；请先处理 X、终止符或空白。')
    return sequence


def read_table(path):
    suffix = Path(path).suffix.lower()
    if suffix == '.xlsx':
        return pd.read_excel(path)
    if suffix in ('.tsv', '.txt'):
        return pd.read_csv(path, sep='\t')
    if suffix == '.csv':
        return pd.read_csv(path)
    raise ValueError('预测表支持 .xlsx、.csv、.tsv；旧版 .xls 请先另存为 .xlsx。')


def normalize_table(table, mhc_class, sequence):
    """Accept normalized columns or the IEDB BA export; reject ambiguous models."""
    normalized_columns = [str(c).strip().lower() for c in table.columns]
    if len(set(normalized_columns)) != len(normalized_columns):
        raise ValueError('预测表包含重复列名，请先明确需要使用的列。')
    columns = dict(zip(normalized_columns, table.columns))
    required = {'peptide': 'peptide', 'allele': 'allele', 'start': 'start', 'end': 'end'}
    for field, suffix in [('ic50', 'ic50'), ('rank', 'percentile'), ('score', 'immunogenicity score')]:
        if field in columns:
            required[field] = field
            continue
        if field == 'rank' and 'percentile' in columns:
            required[field] = 'percentile'
            continue
        prefix = 'netmhcpan_ba' if mhc_class == 'I' else 'netmhciipan_ba'
        found = [c for c in columns if c == suffix] if field == 'score' else [c for c in columns if c.startswith(prefix) and c.endswith(' ' + suffix)]
        if len(found) != 1:
            raise ValueError(f'MHC-{mhc_class} 缺少或包含多个 {field} 列，请按 docs/INPUTS.md 统一列名。')
        required[field] = found[0]
    missing = [name for name in required.values() if name not in columns]
    if missing:
        raise ValueError(f'MHC-{mhc_class} 缺少列：{missing}')
    frame = pd.DataFrame({target: table[columns[source]] for target, source in required.items()})
    if frame.empty:
        raise ValueError(f'MHC-{mhc_class} 输入表为空。')
    for name in ('ic50', 'rank', 'score', 'start', 'end'):
        frame[name] = pd.to_numeric(frame[name], errors='coerce')
        if frame[name].isna().any() or not frame[name].map(math.isfinite).all():
            raise ValueError(f'MHC-{mhc_class} 的 {name} 存在缺失或非数值；不会静默跳过筛选。')
    for name in ('start', 'end'):
        if (frame[name] % 1 != 0).any():
            raise ValueError('表位坐标必须为整数。')
        frame[name] = frame[name].astype(int)
    if (frame.ic50 < 0).any() or ((frame['rank'] < 0) | (frame['rank'] > 100)).any():
        raise ValueError('预测表 IC50 或 percentile 超出范围。')
    if frame.peptide.isna().any() or frame.allele.isna().any():
        raise ValueError('肽段或 HLA 列存在空值。')
    frame['peptide'] = frame.peptide.astype(str).str.strip().str.upper()
    frame['allele'] = frame.allele.astype(str).str.strip()
    for row in frame.itertuples():
        if row.start < 1 or row.end > len(sequence) or row.start > row.end or sequence[row.start - 1:row.end] != row.peptide:
            raise ValueError(f'MHC-{mhc_class} 第 {row.Index + 2} 行的肽段、坐标与 FASTA 不匹配。')
    return frame


def split_alleles(value):
    # Preserve both chains of MHC-II, matching the existing screening convention.
    result = []
    for item in re.split(r'[/,;]', str(value)):
        item = item.strip()
        if not item:
            continue
        if not item.upper().startswith('HLA-'):
            item = 'HLA-' + item
        result.append(item)
    if not result:
        raise ValueError('HLA 列不能为空。')
    return sorted(set(result))


def filter_table(frame, mhc_class, settings, cutoff_path, output):
    tag = 'i' if mhc_class == 'I' else 'ii'
    counts, warnings = {'raw': len(frame)}, []
    out = output / ('mhci' if tag == 'i' else 'mhcii')
    out.mkdir()
    def save(name, df):
        counts[name] = len(df)
        df.to_csv(out / (name + '.csv'), index=False, encoding='utf-8-sig')
        return df.copy()
    frame = save('step0_raw', frame)
    if tag == 'i' and settings.use_allele_cutoff:
        cutoffs = pd.read_csv(cutoff_path)
        if not {'Allele', 'IC50 cutoff'} <= set(cutoffs.columns) or cutoffs.Allele.duplicated().any():
            raise ValueError('cutoff 表需含唯一 Allele 和 IC50 cutoff 列。')
        values = pd.to_numeric(cutoffs['IC50 cutoff'], errors='coerce')
        if values.isna().any() or not values.map(math.isfinite).all() or (values < 0).any():
            raise ValueError('cutoff 表包含无效阈值。')
        limits = frame.allele.map(dict(zip(cutoffs.Allele, values)))
        missing = sorted(set(frame.loc[limits.isna(), 'allele']))
        if missing:
            warnings.append({'unmapped_cutoff_alleles_dropped': missing})
        frame = frame[frame.ic50 <= limits]
    else:
        frame = frame[frame.ic50 <= getattr(settings, 'ic50_' + tag)]
    frame = save('step1_ic50', frame)
    frame = save('step2_rank', frame[frame['rank'] <= getattr(settings, 'rank_' + tag)])
    minimum = getattr(settings, 'score_' + tag + '_min')
    mask = frame.score > minimum if tag == 'i' and settings.score_i_strict_min else frame.score >= minimum
    frame = save('step3_score_min', frame[mask])
    frame = save('step4_score_max', frame[frame.score <= getattr(settings, 'score_' + tag + '_max')])
    frame['allele'] = frame.allele.map(split_alleles)
    frame = frame.explode('allele').drop_duplicates(subset=['peptide', 'start', 'end', 'allele']).reset_index(drop=True)
    save('final', frame)
    return frame, counts, warnings


class _MemoryCoverage(PopulationCoverage):
    def set_file_content(self, filename):
        self.input_epitope_allele_list = self.input_rows


class CoverageCalculator:
    def __init__(self, population):
        self.population = population
        self.cache = {}
        self.unsupported = set()

    def calculate(self, frame, mhc_class):
        alleles = tuple(sorted(set(frame.allele)))
        if not alleles:
            return 0.0, ''
        key = (mhc_class, alleles)
        if key not in self.cache:
            available = {a for locus in population_coverage[mhc_class][self.population].values() for a, _ in locus}
            self.unsupported.update((mhc_class, a) for a in alleles if a not in available)
            if not available.intersection(alleles):
                raise ValueError(f'MHC-{mhc_class} 等位基因在人群 {self.population} 中均无频率数据。')
            pc = _MemoryCoverage()
            # Overall coverage P(at least one hit) depends on the allele union.
            # This does not preserve per-epitope hit distributions; we export coverage only.
            pc.input_rows = [('QUERY', alleles)]
            results, negative = pc.calculate_coverage(population=[self.population], mhc_class=[mhc_class])
            if not results or negative:
                raise ValueError(f'MHC-{mhc_class} 人群覆盖率计算失败。')
            value = float(results[0]['coverage'])
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('覆盖率计算返回无效值。')
            self.cache[key] = value
        return self.cache[key], ','.join(alleles)


def run_analysis(fasta, mhci, mhcii, output_root, settings=None, cutoff=None, progress=None, stop=None):
    settings = settings or Settings()
    settings.validate()
    progress = progress or (lambda percent, message: None)
    def check_stop():
        if stop is not None and stop.is_set():
            raise Cancelled('任务已取消；保留已有中间文件。')
    check_stop()
    sequence = read_sequence(fasta)
    if settings.max_window > len(sequence):
        raise ValueError('最大窗口不能超过蛋白序列长度。')
    cutoff = Path(cutoff) if cutoff else DATA / 'class_I_allele_specific_cutoff.csv'
    input_files = {'fasta': Path(fasta), 'mhci': Path(mhci), 'mhcii': Path(mhcii)}
    if settings.use_allele_cutoff:
        input_files['cutoff'] = cutoff
    hashes = {name: sha256(path) for name, path in input_files.items()}
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = Path(output_root).resolve() / f'{timestamp}_{uuid.uuid4().hex[:8]}'
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'status': 'running', 'version': __version__, 'python': platform.python_version(), 'platform': platform.platform(),
                'dependencies': {'pandas': pd.__version__, 'numpy': np.__version__, 'openpyxl': openpyxl.__version__}, 'settings': asdict(settings),
                'inputs': {name: {'path': str(path.resolve()), 'sha256': hashes[name]} for name, path in input_files.items()},
                'frequency_data_sha256': sha256(FREQUENCIES), 'coverage_unit': 'fraction_0_to_1',
                'coordinate_system': '1-based inclusive', 'warnings': []}
    def write_manifest():
        (output / 'run_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    write_manifest()
    try:
        frames = {}
        for index, (cls, source) in enumerate([('I', mhci), ('II', mhcii)]):
            check_stop()
            progress(index * 10, f'读取与筛选 MHC-{cls} 预测表…')
            frame = normalize_table(read_table(source), cls, sequence)
            frame, counts, warnings = filter_table(frame, cls, settings, cutoff, output)
            frames[cls] = frame
            manifest['step_counts_' + cls] = counts
            manifest['warnings'].extend(warnings)
        check_stop()
        calculator = CoverageCalculator(settings.population)
        rows = []
        total = sum(len(sequence) - size + 1 for size in range(settings.min_window, settings.max_window + 1))
        for size in range(settings.min_window, settings.max_window + 1):
            for start in range(1, len(sequence) - size + 2):
                check_stop()
                end = start + size - 1
                row = {'window_size': size, 'window_start': start, 'window_end': end, 'peptide': sequence[start - 1:end]}
                for cls, number in [('I', 1), ('II', 2)]:
                    frame = frames[cls]
                    window = frame[(frame.start >= start) & (frame.end <= end)]
                    cov, alleles = calculator.calculate(window, cls)
                    row[f'mhc{number}_count'] = len(window)
                    row[f'mhc{number}_coverage'] = cov
                    row[f'mhc{number}_alleles'] = alleles
                row['combined_score'] = (row['mhc1_coverage'] + row['mhc2_coverage']) / 2
                rows.append(row)
                if len(rows) % 25 == 0 or len(rows) == total:
                    progress(20 + int(70 * len(rows) / total), f'窗口 {size} aa：{len(rows)}/{total}')
        all_windows = pd.DataFrame(rows)
        # Retain full precision for numerical reuse; selection follows the historical 4-decimal display convention.
        selected = all_windows[(all_windows.mhc1_coverage.round(4) >= settings.coverage_threshold) &
                               (all_windows.mhc2_coverage.round(4) >= settings.coverage_threshold)].copy()
        all_windows.to_csv(output / 'all_windows.csv', index=False, encoding='utf-8-sig')
        selected.to_csv(output / 'selected_windows.csv', index=False, encoding='utf-8-sig')
        check_stop()
        progress(95, '导出 Excel 和运行记录…')
        with pd.ExcelWriter(output / 'results.xlsx', engine='openpyxl') as writer:
            for name, frame in [('all_windows', all_windows), ('selected_windows', selected), ('mhci_filtered', frames['I']), ('mhcii_filtered', frames['II'])]:
                frame.to_excel(writer, sheet_name=name, index=False)
                writer.sheets[name].freeze_panes = 'A2'
                writer.sheets[name].auto_filter.ref = writer.sheets[name].dimensions
        if hashes != {name: sha256(path) for name, path in input_files.items()}:
            raise ValueError('运行期间输入文件被修改，请使用固定的输入重新运行。')
        manifest.update(status='complete', window_count=len(all_windows), selected_count=len(selected),
                        selection_rule='both class coverages rounded to 4 decimals >= threshold',
                        unsupported_frequency_alleles=[{'class': c, 'allele': a} for c, a in sorted(calculator.unsupported)])
        manifest['outputs'] = {p.name: sha256(p) for p in output.iterdir() if p.is_file() and p.name != 'run_manifest.json'}
        write_manifest()
        progress(100, f'完成：{len(selected)} 个候选窗口。结果：{output}')
        return output
    except Exception as error:
        manifest.update(status='cancelled' if isinstance(error, Cancelled) else 'failed', error=str(error))
        write_manifest()
        raise
