# 跨平台构建

固定验证环境为 Python 3.11 x64、Ubuntu 22.04 / Windows Server 2022 runner。CI 使用公开 API 响应的固定快照做回归测试，默认不向官方服务提交网络任务。CI 验证计算、非法输入、阈值边界、中文路径、取消、重复运行、桌面启动和打包后的命令行示例。它不代替用户电脑上的交互验收或历史论文结果核对。

```bash
python -m pip install -r requirements-build.txt
python -m build
python -m PyInstaller --clean --noconfirm EpitopeScreening.spec
```

必须在 Windows 构建 Windows exe，在 Linux 构建 Linux 可执行文件。使用文件夹模式，分发整个 `dist/EpitopeScreening/`，以保证 Tcl/Tk、人群频率文件和 cutoff CSV 一起打包。

GitHub Actions 在 push 后自动执行双系统测试，通过后创建平台包和 Python wheel。到 Actions 对应成功运行页面下载 Artifacts。此流程不自动创建公开 Release，也不包含任何历史研究表格。

来源：[PyInstaller 工作方式](https://pyinstaller.org/en/stable/operating-mode.html)、[Python Tkinter](https://docs.python.org/3/library/tkinter.html)、[GitHub Python 构建测试](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)。

## Linux 本地网页包

```bash
python -m pip install -r requirements-build.txt
python scripts/build_web_guide.py
python -m PyInstaller --clean --noconfirm EpitopeScreeningWeb.spec
./dist/EpitopeScreening-Web/Start-Web --smoke-test --data-dir /tmp/web-smoke
```

CI 使用 Ubuntu 22.04 / Python 3.11 构建，校验 frozen 网页离线流程后，附带 `先看这里.html`、CSS、文档并生成 tar.gz。必须保留 `_internal` 与原始文件权限；GitHub Actions ZIP 仅作为外层传输，用户发行包使用 tar.gz。不要将本机 IEDB 工具包、运行目录或 `session.json` 打入发行包。

开发时可用 `python -m epitope_screening web`；`--data-dir` 用于独立测试数据位置，`--no-browser` 用于无桌面测试。服务仅监听回环地址，不能用作公网后端。独立网页说明由 `docs/WEB_BEGINNER.md` 经 `scripts/build_web_guide.py` 生成。
