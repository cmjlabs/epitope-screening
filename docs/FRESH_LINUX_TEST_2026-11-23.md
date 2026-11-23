# v0.3.0b1 全新 Linux 下载与完整流程测试

测试日期：2026-11-23。结论：**通过**。使用 GitHub Releases 实际下载的原始源码包，测试期间未修改任何发行包文件，也未挂载此前的 Python、IEDB 工具或结果目录。

## 测试起点

- 新建空目录，记录初始条目数为 0，并创建 0 字节 `empty_start.txt`。
- 从 GitHub Release 下载源码 ZIP 与 SHA256SUMS，校验通过。源码提交：`33dc20c24a23ba388ff3ba24dd76ecde8371e10e`。
- 使用原始 Ubuntu 24.04 x86_64 环境，安装前没有项目虚拟环境、工具配置、uv、R 或 tcsh。
- 从 IEDB 官方入口重新下载 TC1 v0.2-beta、TC2 v0.2.3-beta，没有使用本机旧模型。
- 仓库目前为 Private；下载测试使用具有仓库读取权限的账号，其他用户也需要读取权限。

## 首次安装

完整执行发行包的 `setup_local_linux.sh`，自动安装系统依赖、uv 和两个隔离环境。系统 Python 3.12.3；运行环境 Python 3.11.14；桌面 pandas 2.2.3；预测器 pandas 1.5.2。安装脚本完成且不需要修改应用代码。

模型校验、数据提取、安装日志和环境依赖清单均保存。测试环境的虚拟显示仅用于自动操作真实 Tk 界面，用户在图形桌面运行不需要 Xvfb。Docker 仅为开发者隔离测试使用，用户脚本不调用 Docker。

## 断网后的真实计算

安装完成后断开测试环境网络，路由表无默认路由。实际调用本地 TC1/TC2 计算。界面测试只自动确认提示框，没有模拟或替换预测器。

| 用例 | MHC-I 行数 | MHC-II 行数 | 窗口数 | 任务内部耗时 |
|---|---:|---:|---:|---:|
| P09257 | 923 | 917 | 9977 | 28.2 秒 |
| P09259 | 615 | 609 | 6589 | 19.9 秒 |
| full-panel | 4158 | 486 | 88 | 81.8 秒 |
| 中文 结果 GUI | 24 | 18 | 88 | 3.8 秒 |

任务内部耗时不含应用启动、环境检查等开销；界面实际点击到完成约 16.0 秒。
完整面板用例为公开 LCMV 32 aa，采用默认 27+27 HLA、MHC-I 8–14mer、MHC-II 15mer。P09257（931 aa）与 P09259（623 aa）来自重新下载的 UniProt FASTA，采用每类一个 HLA、9/15mer。
所有用例均生成 CSV 和含四个工作表的 Excel。逐行核验肽段与坐标、预期行数、无缺失评分、无重复 HLA–坐标，以及覆盖率处于 0–1。中文和带空格的结果目录通过。默认双类覆盖率 0.8 下候选为 0；这些功能用例不能代替历史论文候选表复现。

## 恢复与回归

- 恢复完整面板任务，14 份原始预测 JSON 的 SHA256 和修改时间均未变化，未重新预测。
- 恢复前后的 `all_windows.csv` 和 `selected_windows.csv` 完全一致。
- 公开小样本与发行包的 IEDB API 参考结果逐项比较，肽段/坐标匹配，IC50、rank、score 最大绝对差均为 0。
- 发行包自带 31 项单元/回归测试全部通过。
- 测试后重新核验发行包内 53 个文件，内容全部与 GitHub 下载 ZIP 一致。

## 用户复现入口

1. 下载 [v0.3.0b1 发布包](https://github.com/cmjlabs/epitope-screening/releases/tag/v0.3.0b1) 并核对 SHA256。
2. 按官方许可从 [IEDB 官方下载页](https://nextgen-tools.iedb.org/download-all) 获取两个工具包，解压到不含空格及 shell 特殊字符的路径。
3. 在项目根目录执行：

```bash
bash setup_local_linux.sh /实际路径/ng_tc1-v0.2-beta /实际路径/ng_tc2-v0.2.3-beta
bash start_local_linux.sh
```

命令行完整面板示例：

```bash
.venv/bin/python -m epitope_screening local --fasta examples/public_lcmv.fasta --tools-config local-tools.json --output results
```

## 下载校验

| 文件 | SHA256 |
|---|---|
| GitHub 源码 ZIP | `d7f2fe7555506a50925219f22f7772381b0eedf3e1d479220293551e1b128362` |
| IEDB TC1 v0.2-beta | `28ef5ba773cadae7a6a5803a9f6047b833c2269bba7c43ac9997b631532179a6` |
| IEDB TC2 v0.2.3-beta | `7b3b55b25ffdece539bac92c4c66fbd00945fde72c846e6e7e6efe8e9f579a13` |

此报告验证安装与软件流程，不表示两个目标蛋白的完整历史 HLA 面板已复现。首次仍需联网获取第三方工具与依赖，并有权限安装系统软件。
