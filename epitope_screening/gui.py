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
        root.geometry('960x820')
        root.minsize(850, 730)
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.running = False
        self.last_result = None
        self.paths = {}
        self.params = {}
        box = ttk.Frame(root, padding=16)
        box.pack(fill='both', expand=True)
        ttk.Label(box, text='表位筛选与人群覆盖率分析', font=('', 16, 'bold')).pack(anchor='w')
        ttk.Label(box, text='导入单蛋白 FASTA 和已有 IEDB 预测表；分析在本机离线完成。').pack(anchor='w', pady=(4, 12))
        files = ttk.LabelFrame(box, text='输入与输出', padding=8)
        files.pack(fill='x')
        for index, (name, label) in enumerate([('fasta', '蛋白 FASTA'), ('mhci', 'MHC-I 预测表'), ('mhcii', 'MHC-II 预测表'), ('cutoff', 'cutoff CSV（留空用内置表）'), ('output', '结果保存文件夹')]):
            var = tk.StringVar(value=str(Path.home() / 'EpitopeScreeningResults') if name == 'output' else '')
            self.paths[name] = var
            ttk.Label(files, text=label).grid(row=index, column=0, sticky='w', padx=4, pady=3)
            ttk.Entry(files, textvariable=var).grid(row=index, column=1, sticky='ew', padx=4)
            ttk.Button(files, text='选择', command=lambda n=name: self.choose(n)).grid(row=index, column=2, padx=4)
        files.columnconfigure(1, weight=1)
        settings = ttk.LabelFrame(box, text='筛选参数（上下限默认均包含边界）', padding=8)
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
        if name == 'output':
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
            result[field.name] = type(getattr(defaults, field.name))(value)
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

    def start(self):
        try:
            settings = self.get_settings()
            paths = {key: value.get().strip() for key, value in self.paths.items()}
            if any(not paths[key] for key in ('fasta', 'mhci', 'mhcii', 'output')):
                raise ValueError('请选择 FASTA、两张预测表及结果保存文件夹。')
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
                result = run_analysis(paths['fasta'], paths['mhci'], paths['mhcii'], paths['output'], settings,
                                      paths['cutoff'] or None, lambda p, m: self.events.put(('progress', p, m)), self.stop)
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
                    messagebox.showinfo('完成', f'结果已保存：\n{event[1]}\n\n包含 results.xlsx 和运行记录。')
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


def main():
    root = tk.Tk()
    Application(root)
    root.mainloop()
