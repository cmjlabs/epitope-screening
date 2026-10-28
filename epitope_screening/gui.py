"""A local desktop UI. All Tk operations stay on the main thread."""
from dataclasses import asdict, fields
import json
from pathlib import Path
import queue
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import __version__
from .core import Settings, run_analysis, Cancelled, populations
from .iedb_api import PredictionSettings, run_online

LABELS = {
 'min_window': '最小窗口 (aa)', 'max_window': '最大窗口 (aa)', 'population': '人群',
 'coverage_threshold': '双类覆盖率下限 (0–1)', 'use_allele_cutoff': 'MHC-I 使用等位基因 cutoff',
 'ic50_i': 'MHC-I IC50 上限 (nM)', 'rank_i': 'MHC-I rank 上限 (%)',
 'score_i_min': 'MHC-I 评分下限', 'score_i_max': 'MHC-I 评分上限',
 'score_i_strict_min': 'MHC-I 评分严格大于下限', 'ic50_ii': 'MHC-II IC50 上限 (nM)',
 'rank_ii': 'MHC-II rank 上限 (%)', 'score_ii_min': 'MHC-II 评分下限', 'score_ii_max': 'MHC-II 评分上限',
}


class Application:
    def __init__(self, root):
        self.root = root
        root.title(f'Epitope Screening {__version__} · 表位筛选')
        root.geometry('1060x820')
        root.minsize(900, 720)
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.running = False
        self.last_result = None
        self.paths = {}
        self.params = {}
        box = ttk.Frame(root, padding=16)
        box.pack(fill='both', expand=True)
        ttk.Label(box, text='表位筛选与人群覆盖率分析', font=('', 16, 'bold')).pack(anchor='w')
        ttk.Label(box, text='在线模式：FASTA 发送至 IEDB 官方服务器预测，筛选在本机完成；离线模式：导入已有预测表。').pack(anchor='w', pady=(4, 8))
        modes = ttk.Frame(box)
        modes.pack(fill='x')
        self.mode = tk.StringVar(value='online')
        ttk.Radiobutton(modes, text='从 FASTA 在线预测并筛选', variable=self.mode, value='online').pack(side='left')
        ttk.Radiobutton(modes, text='导入预测表离线筛选', variable=self.mode, value='offline').pack(side='left', padx=15)
        tabs = ttk.Notebook(box)
        tabs.pack(fill='x', pady=8)
        input_page, filter_page = ttk.Frame(tabs), ttk.Frame(tabs)
        tabs.add(input_page, text='输入与在线预测')
        tabs.add(filter_page, text='本地筛选参数')
        files = ttk.LabelFrame(input_page, text='输入与输出', padding=8)
        files.pack(fill='x')
        for index, (name, label) in enumerate([('fasta', '蛋白 FASTA'), ('mhci', 'MHC-I 预测表'), ('mhcii', 'MHC-II 预测表'), ('cutoff', 'cutoff CSV（留空用内置表）'), ('output', '结果保存文件夹'), ('resume', '恢复在线任务目录（可选）')]):
            var = tk.StringVar(value=str(Path.home() / 'EpitopeScreeningResults') if name == 'output' else '')
            self.paths[name] = var
            ttk.Label(files, text=label).grid(row=index, column=0, sticky='w', padx=4, pady=3)
            ttk.Entry(files, textvariable=var).grid(row=index, column=1, sticky='ew', padx=4)
            ttk.Button(files, text='选择', command=lambda n=name: self.choose(n)).grid(row=index, column=2, padx=4)
        files.columnconfigure(1, weight=1)
        online = ttk.LabelFrame(input_page, text='在线预测参数（离线模式不使用）', padding=8)
        online.pack(fill='x', pady=(8, 0))
        self.prediction = PredictionSettings()
        self.api_fields = {}
        for row, (name, label) in enumerate([('alleles_i', 'MHC-I HLA（逗号分隔）'), ('alleles_ii', 'MHC-II HLA（逗号分隔）'), ('proxy_url', 'HTTP 代理（可选，无需修改系统代理）')]):
            value = getattr(self.prediction, name)
            var = tk.StringVar(value=', '.join(value) if isinstance(value, list) else value)
            self.api_fields[name] = var
            ttk.Label(online, text=label).grid(row=row, column=0, sticky='w')
            ttk.Entry(online, textvariable=var).grid(row=row, column=1, columnspan=3, sticky='ew', padx=5, pady=2)
        for index, (name, label) in enumerate([('min_length_i', 'I 最小肽长'), ('max_length_i', 'I 最大肽长'), ('min_length_ii', 'II 最小肽长'), ('max_length_ii', 'II 最大肽长')]):
            var = tk.StringVar(value=str(getattr(self.prediction, name)))
            self.api_fields[name] = var
            ttk.Label(online, text=label).grid(row=3 + index // 2, column=(index % 2) * 2, sticky='w')
            ttk.Entry(online, textvariable=var, width=10).grid(row=3 + index // 2, column=(index % 2) * 2 + 1, sticky='ew', padx=5, pady=2)
        ttk.Label(online, text='默认模型：NetMHCpan BA 4.1 / NetMHCIIpan BA 4.3；CD4Episcore 仅在 15 aa 验证。').grid(row=5, column=0, columnspan=4, sticky='w')
        ttk.Button(online, text='导入预测参数 JSON', command=self.load_prediction).grid(row=6, column=0, sticky='w', pady=3)
        ttk.Button(online, text='保存预测参数 JSON', command=self.save_prediction).grid(row=6, column=1, sticky='w')
        online.columnconfigure(1, weight=1)
        online.columnconfigure(3, weight=1)
        settings = ttk.LabelFrame(filter_page, text='筛选参数（上下限默认均包含边界）', padding=8)
        settings.pack(fill='x', pady=10)
        default = Settings()
        for index, field in enumerate(fields(default)):
            value = getattr(default, field.name)
            row, column = divmod(index, 2)
            if type(value) is bool:
                var = tk.BooleanVar(value=value)
                widget = ttk.Checkbutton(settings, text=LABELS[field.name], variable=var)
                widget.grid(row=row, column=column * 2, columnspan=2, sticky='w', padx=4, pady=4)
            else:
                var = tk.StringVar(value=str(value))
                ttk.Label(settings, text=LABELS[field.name]).grid(row=row, column=column * 2, sticky='w', padx=4)
                if field.name == 'population':
                    widget = ttk.Combobox(settings, textvariable=var, values=populations(), state='readonly', width=19)
                else:
                    widget = ttk.Entry(settings, textvariable=var, width=20)
                widget.grid(row=row, column=column * 2 + 1, sticky='ew', padx=4, pady=4)
            self.params[field.name] = var
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)
        bar = ttk.Frame(box)
        bar.pack(fill='x')
        self.start_button = ttk.Button(bar, text='开始分析', command=self.start)
        self.start_button.pack(side='left')
        self.cancel_button = ttk.Button(bar, text='取消', command=self.stop.set, state='disabled')
        self.cancel_button.pack(side='left', padx=6)
        ttk.Button(bar, text='导入参数 JSON', command=self.load_config).pack(side='left', padx=6)
        ttk.Button(bar, text='保存参数 JSON', command=self.save_config).pack(side='left')
        self.progress = ttk.Progressbar(box, maximum=100)
        self.progress.pack(fill='x', pady=10)
        self.log = tk.Text(box, height=8, wrap='word', state='disabled')
        self.log.pack(fill='both', expand=True)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def choose(self, name):
        if self.running:
            return
        if name in ('output', 'resume'):
            path = filedialog.askdirectory()
        else:
            types = [('FASTA', '*.fasta *.fa *.faa')] if name == 'fasta' else [('预测数据', '*.xlsx *.csv *.tsv *.txt')]
            if name == 'cutoff':
                types = [('CSV', '*.csv')]
            path = filedialog.askopenfilename(filetypes=types + [('所有文件', '*')])
        if path:
            self.paths[name].set(path)

    def get_settings(self):
        result = {}
        defaults = Settings()
        for field in fields(defaults):
            value = self.params[field.name].get()
            result[field.name] = field.type(value)
        settings = Settings(**result)
        settings.validate()
        return settings

    def load_config(self):
        if self.running:
            return
        path = filedialog.askopenfilename(filetypes=[('JSON', '*.json')])
        if path:
            try:
                settings = Settings(**json.loads(Path(path).read_text(encoding='utf-8-sig')))
                settings.validate()
                for key, value in asdict(settings).items():
                    self.params[key].set(value)
            except Exception as error:
                messagebox.showerror('参数错误', str(error))

    def save_config(self):
        try:
            settings = self.get_settings()
            path = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('JSON', '*.json')])
            if path:
                Path(path).write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as error:
            messagebox.showerror('参数错误', str(error))

    def get_prediction(self):
        data = asdict(self.prediction)
        for name, var in self.api_fields.items():
            value = var.get().strip()
            data[name] = [a.strip() for a in value.split(',') if a.strip()] if name.startswith('alleles_') else (int(value) if name.startswith(('min_length', 'max_length')) else value)
        return PredictionSettings(**data)

    def load_prediction(self):
        if self.running:
            return
        path = filedialog.askopenfilename(filetypes=[('JSON', '*.json')])
        if path:
            try:
                prediction = PredictionSettings(**json.loads(Path(path).read_text(encoding='utf-8-sig')))
                prediction.validate(max(30, prediction.max_length_i, prediction.max_length_ii))
                self.prediction = prediction
                for name, var in self.api_fields.items():
                    value = getattr(prediction, name)
                    var.set(', '.join(value) if isinstance(value, list) else value)
            except Exception as error:
                messagebox.showerror('预测参数错误', str(error))

    def save_prediction(self):
        try:
            prediction = self.get_prediction()
            prediction.validate(max(30, prediction.max_length_i, prediction.max_length_ii))
            path = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('JSON', '*.json')])
            if path:
                Path(path).write_text(json.dumps(asdict(prediction), ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as error:
            messagebox.showerror('预测参数错误', str(error))

    def start(self):
        try:
            settings = self.get_settings()
            paths = {key: value.get().strip() for key, value in self.paths.items()}
            online_mode = self.mode.get() == 'online'
            required = ('fasta', 'output') if online_mode else ('fasta', 'mhci', 'mhcii', 'output')
            if any(not paths[key] for key in required):
                raise ValueError('请选择 FASTA、结果文件夹；离线模式还需要两张预测表。')
            prediction = self.get_prediction() if online_mode else None
            if prediction:
                from .core import read_sequence
                prediction.validate(len(read_sequence(paths['fasta'])))
        except Exception as error:
            messagebox.showerror('请检查输入', str(error))
            return
        self.running = True
        self.stop.clear()
        self.progress['value'] = 0
        self.start_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        def work():
            try:
                callback = lambda p, m: self.events.put(('progress', p, m))
                if online_mode:
                    result = run_online(paths['fasta'], paths['output'], settings, prediction, paths['cutoff'] or None,
                                        progress=callback, stop=self.stop, resume=paths['resume'] or None)
                else:
                    result = run_analysis(paths['fasta'], paths['mhci'], paths['mhcii'], paths['output'], settings,
                                          paths['cutoff'] or None, callback, self.stop)
                self.events.put(('done', result))
            except Cancelled as error:
                self.events.put(('cancelled', str(error)))
            except Exception:
                self.events.put(('error', traceback.format_exc()))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        while not self.events.empty():
            event = self.events.get()
            if event[0] == 'progress':
                self.progress['value'] = event[1]
                text = event[2]
            else:
                self.running = False
                self.start_button.configure(state='normal')
                self.cancel_button.configure(state='disabled')
                text = str(event[1])
                if event[0] == 'done':
                    self.last_result = event[1]
                    messagebox.showinfo('完成', f'结果已保存：\n{event[1]}\n\n在线任务的 Excel 位于 screening 子目录；离线任务包含 results.xlsx。')
                elif event[0] == 'error':
                    messagebox.showerror('分析失败', text)
            self.log.configure(state='normal')
            self.log.insert('end', text + '\n')
            self.log.see('end')
            self.log.configure(state='disabled')
        self.root.after(100, self.poll)

    def close(self):
        if self.running:
            self.stop.set()
            messagebox.showinfo('正在取消', '请等待当前读取或计算结束，再关闭窗口。')
        else:
            self.root.destroy()


def main(smoke_test=False):
    root = tk.Tk()
    if smoke_test:
        root.withdraw()
    Application(root)
    if smoke_test:
        root.update_idletasks()
        root.update()
        root.destroy()
    else:
        root.mainloop()
