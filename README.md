# Epitope Screening · 表位筛选

Windows / Linux 桌面软件：**FASTA → IEDB 官方 API 预测 → 本地阈值筛选 → 窗口与人群覆盖率 → Excel / CSV 报告**。

0.2 版新增在线预测，同时保留导入预测表的离线筛选。联网预测无需 Docker、WSL、IEDB 工具包或自己的预测服务器；序列由用户电脑直接发送至 IEDB 官方服务器，本地负责筛选与覆盖率计算。软件不会在后台自动提交，只有点击开始或执行 `online` 命令才会发送序列。

**0.3.0b1 新增 Linux 本地预测（测试版）**：首次配置后，可用本机 IEDB TC1/TC2 从 FASTA 开始完成预测、免疫原性评分和筛选，不调用 API。全新 Ubuntu/Debian x86_64 用户请下载源码并按[本地安装说明](docs/LOCAL_LINUX.md)操作；首次仍需按官方许可获取两个工具包。无需 Docker、Conda 或作者电脑在线。Windows 本地预测尚未接入，Windows 可继续使用在线或导入表模式。

## Windows 使用

### 可执行版本（推荐）

在仓库 [Releases](https://github.com/cmjlabs/epitope-screening/releases) 下载最新版本的 Windows x64 ZIP，完整解压到可写文件夹，双击 `EpitopeScreening.exe`。必须保留同目录的 `_internal` 文件夹。无需自行安装 Python。Actions 中也保留对应提交的构建产物。

**v0.2.1 修复 MHC-II 默认 HLA 名称被接口拒绝的问题。** 在旧版遇到该错误时，请保留原任务目录，用新版选择同一 FASTA、原参数及「恢复在线任务目录」后继续，已完成的 MHC-I 不会重新提交。见[恢复说明](docs/ONLINE.md#恢复-v020-的-mhc-ii-hla-名称错误)。

1. 默认选择「从 FASTA 在线预测并筛选」。
2. 选择一个单蛋白 FASTA 和输出文件夹；**无需准备 MHC-I / II 预测表**。
3. 检查 HLA 与预测肽长，在「本地筛选参数」页设置阈值。
4. 点击「开始分析」，等待官方预测与本地筛选完成。
5. 到任务目录下 `screening/<时间_ID>/results.xlsx` 查看结果，候选在 `selected_windows` 工作表。

如已有预测表，切换「导入预测表离线筛选」，再选择两张表。断网或取消后，选择原在线任务文件夹作为「恢复在线任务目录」，保持同一序列和分析参数再运行，即可使用已保存任务编号或结果。

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
# 小规模在线试跑（公开 LCMV 示例，每类一个 HLA）
python -m epitope_screening online --fasta examples/public_lcmv.fasta --prediction-config examples/prediction-small.json --config examples/config.json --output results

# 导入已有预测结果，离线分析
python -m epitope_screening run --fasta examples/protein.fasta --mhci examples/mhci.csv --mhcii examples/mhcii.csv --config examples/config.json --output results
python -m epitope_screening populations
python -m epitope_screening default-config
```

也可以安装为 Python 包：`python -m pip install .`，然后使用 `epitope-screening run ...`。Windows 虚拟环境 Python 位于 `.venv\Scripts\python.exe`，Linux 位于 `.venv/bin/python`。

`examples/protein.fasta` 与 CSV 是人工构造的离线功能示例；`public_lcmv.fasta` 是官方文档中的公开在线示例。单个 HLA 的试跑在 0.8 阈值下可能没有候选窗口，属于预期行为。

## 输入与参数

- [在线预测与恢复](docs/ONLINE.md)：模型、批次、隐私提示、网络设置与结果留存。
- [输入格式](docs/INPUTS.md)：IEDB 导出列、标准列及坐标要求。
- [计算方法](docs/METHODS.md)：边界、HLA 拆分、人群覆盖率、精度与候选规则。
- [项目整理说明](docs/MIGRATION.md)：旧代码来源、本版范围和后续迁移方向。
- [Windows / Linux 构建](docs/BUILD.md)：本地打包与自动测试。
- [第三方来源](THIRD_PARTY.md)：人群频率数据与计算代码来源。

本地筛选默认参数继承网站当前界面：MHC-I allele-specific IC50 cutoff、rank ≤ 2、评分 0–100；MHC-II IC50 ≤ 1000 nM、rank ≤ 10、评分 −100–66；窗口 20–30 aa、人群 World、双类覆盖率各 ≥ 0.8。历史论文结果的参数尚未核定，默认值不代表历史参数。

在线预测默认使用原流水线的 27 个 MHC-I HLA 与 27 个 MHC-II HLA，MHC-I 长度 8–14 aa、MHC-II **15 aa**；MHC-II 长度默认值与旧流水线的 11–30 不同，因为官方说明 CD4Episcore 只对 15mer 验证过。可自行修改，软件保留 IEDB 警告。

## 输出

在线任务包含 `input.fasta` 快照、`pipeline_manifest.json`、`predictions/` 中的每批请求、任务 ID、原始 JSON 和标准 CSV，以及 `screening/` 内的本地筛选结果。每次新分析创建唯一结果文件夹，避免覆盖。

本地筛选结果结构：

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

`core.py` 负责计算，`gui.py` 只负责桌面交互，`__main__.py` 负责命令行。后续网站可直接调用 `run_analysis()`。当前项目不含服务器账号、网站数据库或未发表研究结果。`iedb_api.py` 负责从 FASTA 调用官方 API，不依赖本地预测器；完整本地 TC1/TC2 工具包和理化性质评估仍保留于原项目中。

当前版本已进行官方 API 小样本实测、录制响应的离线回归测试及本地核心测试；新版本在 Windows 的验证情况以对应 CI 记录为准。P09257 / P09259 历史结果核对另行进行，未声明已经复现。
