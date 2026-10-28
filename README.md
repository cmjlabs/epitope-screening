# Epitope Screening · 表位筛选

Windows / Linux 离线 MHC 表位筛选、人群覆盖率与滑动窗口分析。

从 Lab Tools 网站与 IEDB pipeline 中提取筛选职责，提供共用计算核心的桌面界面和命令行。导入已有 IEDB 预测结果，输出分步筛选表、全部窗口、双类高覆盖率候选窗口及运行记录。

## Windows 使用

### 可执行版本（推荐）

在仓库 **Actions → 最新成功运行 → Artifacts** 下载 `EpitopeScreening-Windows-x64`，完整解压到可写文件夹，双击 `EpitopeScreening.exe`。必须保留同目录的 `_internal` 文件夹。无需自行安装 Python；exe 构建产物须等 Windows 自动测试和打包成功后才会出现。

1. 选择一个单蛋白 FASTA 文件。
2. 分别选择 MHC-I、MHC-II 预测表，支持 XLSX / CSV / TSV。
3. 选择输出文件夹，检查参数，点击「开始分析」。
4. 打开结果目录中的 `results.xlsx`；候选窗口在 `selected_windows` 工作表。

### 源码版本

安装 **Python 3.11 x64**，包含 Tcl/Tk 与 Python Launcher。下载并解压项目，双击 `start_windows.cmd`。首次启动联网安装依赖，之后分析可离线运行。依赖安装失败时窗口会保留错误信息，下一次启动会重试。

## Linux 使用

Ubuntu / Debian 先准备环境：

```bash
sudo apt install python3-venv python3-tk
bash start_linux.sh
```

源码推荐 Python 3.11；其他 Python 版本不在当前固定依赖测试范围内。Actions 提供在 Ubuntu 22.04 上构建的 `EpitopeScreening-Linux-x64` 文件夹包；较旧发行版优先用源码。解压后执行：

```bash
chmod +x EpitopeScreening
./EpitopeScreening
```

## 命令行

在项目根目录，用已安装依赖的 Python 执行：

```bash
python -m epitope_screening run --fasta examples/protein.fasta --mhci examples/mhci.csv --mhcii examples/mhcii.csv --config examples/config.json --output results
python -m epitope_screening populations
python -m epitope_screening default-config
```

也可以安装为 Python 包：`python -m pip install .`，然后使用 `epitope-screening run ...`。Windows 虚拟环境 Python 位于 `.venv\Scripts\python.exe`，Linux 位于 `.venv/bin/python`。

`examples/` 是人工构造的功能示例，不是已验证的生物学预测；默认 0.8 阈值下可能没有候选窗口，属于预期行为。

## 输入与参数

- [输入格式](docs/INPUTS.md)：IEDB 导出列、标准列及坐标要求。
- [计算方法](docs/METHODS.md)：边界、HLA 拆分、人群覆盖率、精度与候选规则。
- [项目整理说明](docs/MIGRATION.md)：旧代码来源、本版范围和后续迁移方向。
- [Windows / Linux 构建](docs/BUILD.md)：本地打包与自动测试。
- [第三方来源](THIRD_PARTY.md)：人群频率数据与计算代码来源。

默认参数继承网站当前界面：MHC-I allele-specific IC50 cutoff、rank ≤ 2、评分 0–100；MHC-II IC50 ≤ 1000 nM、rank ≤ 10、评分 −100–66；窗口 20–30 aa、人群 World、双类覆盖率各 ≥ 0.8。历史论文结果的参数尚未核定，默认值不代表历史参数。

## 输出

每次分析创建唯一结果文件夹，避免覆盖：

```text
results/<UTC时间>_<随机ID>/
├── results.xlsx                 # 全窗口 / 候选窗口 / 两类筛选表
├── all_windows.csv
├── selected_windows.csv
├── run_manifest.json            # 参数、版本、输入与频率数据哈希、状态、警告
├── mhci/step0_raw.csv ... final.csv
└── mhcii/step0_raw.csv ... final.csv
```

覆盖率均为 **0–1 比例**。保留未舍入数值供后续比较，候选筛选按覆盖率四舍五入到 4 位小数后判断。`combined_score` 为两类覆盖率的算术平均，候选条件要求两类各自达标。

## 开发与维护

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

`core.py` 负责计算，`gui.py` 只负责桌面交互，`__main__.py` 负责命令行。后续网站可直接调用 `run_analysis()`。当前项目不含服务器账号、网站数据库或未发表研究结果；从 FASTA 调用 IEDB 预测器和理化性质评估属于独立模块，暂保留于原项目中。

当前版本用于整理与跨平台构建。P09257 / P09259 历史结果核对另行进行，未声明已经复现。
