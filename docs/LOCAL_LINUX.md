# 全新 Linux：本地 IEDB 预测

此功能从 0.3.0b1 起提供。v0.2.x 的 Linux 可执行版仍通过 API 预测。当前本地安装向导面向 **Ubuntu/Debian、x86_64、图形桌面**；不要求 Docker、Conda 或作者电脑在线。

## 第一次安装

1. 下载并解压本项目 GitHub 源码到可写目录。
2. 从 [IEDB 官方下载页](https://nextgen-tools.iedb.org/download-all) 按许可获取并解压 **TC1 与 TC2**。已验证包版本为 TC1 0.1.2-beta、TC2 0.2.2-beta。两个目录应分别包含 `src/tcell_mhci.py`、`src/tcell_mhcii.py`。模型数据较大，请预留磁盘空间。

官方工具内部有 shell 调用，TC1/TC2 的完整目录路径请使用不含空格或 shell 特殊字符的简单路径；软件会在运行前检查。
3. 在本项目目录运行（把两个路径换成实际解压路径）：

```bash
bash setup_local_linux.sh /下载目录/ng_tc1-0.1.2-beta /下载目录/ng_tc2-0.2.2-beta
bash start_local_linux.sh
```

首次配置需要联网。脚本检查并安装系统依赖，必要时通过 sudo 安装 `python3-venv`、Tk、tcsh、gawk、Perl、R；使用固定的 uv 0.9.5 获取 Python 3.11，并创建隔离的桌面环境及 IEDB 环境。IEDB 环境固定 pandas 1.5.2，桌面环境使用 pandas 2.2.3，避免旧版官方工具与新 pandas 的不兼容。无需手动设置 PATH 或修改官方源码。

脚本不下载或重新分发第三方预测器，不代替用户接受官方许可。用户需按适用条款获取工具包；模型不会打进本仓库。项目自带的 population coverage 代码与数据来源见 THIRD_PARTY.md。

所有 Python 环境和配置均在本项目目录内，`local-tools.json` 保存本机工具路径、内容哈希及并行数。首次散列完整工具数据可能耗时；后续检查文件清单、大小和修改时间，发生变化时要求重新配置。移动项目或工具目录后请重新运行配置脚本。预测依赖的原始模型、参考数据和程序应一并保留，不能仅保存一份 EXE。

## 日常使用

通过 `start_local_linux.sh` 启动时，界面默认选择 **Linux 本地预测**。选择单蛋白 FASTA、输出目录，确认 HLA、肽长和阈值，点击开始。无需填写 MHC-I/MHC-II 表，也无需代理。正常预测与筛选可在断网环境运行，不会切换到 API。

默认 NetMHCpan 4.1 BA、NetMHCIIpan 4.3 BA，并包含各自免疫原性评分；CD4Episcore 步长在本地 predictor 参数中明确设为 1，保证每个位置有评分。MHC-II 默认只用经过验证的 15mer。输入输出语义与现有在线模式一致，但不同工具或模型补丁、数据版本仍需数值核对。

默认并行 2 批，可修改 `local-tools.json` 的 `workers` 为 1–8。CPU、内存及任务大小决定实际速度。完成的批次可通过恢复原 `local_...` 目录复用；在线任务与本地任务不能混合恢复。取消会停止本地子进程及其预测进程组。

也可使用命令行：

```bash
.venv/bin/python -m epitope_screening local --fasta examples/public_lcmv.fasta --prediction-config examples/prediction-small.json --tools-config local-tools.json --output results
```

结果保存在 `local_.../screening/<ID>/results.xlsx`。`local_manifest.json` 记录模型参数、工具签名、批次耗时、结果哈希、状态与输出位置。MHC-I 肽段型输出按 FASTA 所有出现位置展开，保留重复序列的所有坐标；缺失或冲突评分仍会中止。

## 验证边界

使用无网络的干净 Linux 环境实际运行，未调用预测 API。公开 LCMV 32 aa 示例的一类一个 HLA 结果，与记录的 API 响应在肽段、坐标、IC50、percentile、score 上一致。完整 27+27 面板也已完成本地运行。此验证不代表历史论文 P09257/P09259 的结果已复现；历史参数与模型版本另行核对。

开发时用容器隔离验证安装过程与断网计算；容器仅用于测试，用户安装与运行脚本不调用 Docker。

## 从 GitHub 下载开始的独立实测

2026-11-23 在原始 Ubuntu 24.04 环境中，从 GitHub 下载 v0.3.0b1，并从 IEDB 重新下载 TC1 v0.2-beta、TC2 v0.2.3-beta。原版安装脚本、断网界面分析、完整 HLA 示例、两个真实蛋白的单 HLA 测试、任务恢复与 31 项回归测试均通过；发行包文件未修改。见[完整记录](FRESH_LINUX_TEST_2026-11-23.md)。
