# 跨平台构建

固定验证环境为 Python 3.11 x64、Ubuntu 22.04 / Windows Server 2022 runner。CI 验证计算、非法输入、阈值边界、中文路径、取消、重复运行、桌面启动和打包后的命令行示例。它不代替用户电脑上的交互验收或历史论文结果核对。

```bash
python -m pip install -r requirements-build.txt
python -m build
python -m PyInstaller --clean --noconfirm EpitopeScreening.spec
```

必须在 Windows 构建 Windows exe，在 Linux 构建 Linux 可执行文件。使用文件夹模式，分发整个 `dist/EpitopeScreening/`，以保证 Tcl/Tk、人群频率文件和 cutoff CSV 一起打包。

GitHub Actions 在 push 后自动执行双系统测试，通过后创建平台包和 Python wheel。到 Actions 对应成功运行页面下载 Artifacts。此流程不自动创建公开 Release，也不包含任何历史研究表格。

来源：[PyInstaller 工作方式](https://pyinstaller.org/en/stable/operating-mode.html)、[Python Tkinter](https://docs.python.org/3/library/tkinter.html)、[GitHub Python 构建测试](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)。
