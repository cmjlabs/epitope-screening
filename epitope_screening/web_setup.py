"""First-run local IEDB setup, controlled by explicit browser actions."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import threading
import urllib.request

from .core import sha256
from .iedb_api import write_json
from .local_iedb import external_environment, tool_signature, validate_runtime

ASSETS = Path(__file__).parent / 'web'
PACKAGES = {
    'tc1': {'version': 'v0.2-beta', 'size': 743438595,
        'url': 'https://downloads.iedb.org/nextgen-tools/tcell_mhci/v0.2-beta/IEDB_NG_TC1-v0.2-beta.tar.gz',
        'sha256': '28ef5ba773cadae7a6a5803a9f6047b833c2269bba7c43ac9997b631532179a6'},
    'tc2': {'version': 'v0.2.3-beta', 'size': 1662207664,
        'url': 'https://downloads.iedb.org/nextgen-tools/tcell_mhcii/v0.2.3-beta/IEDB_NG_TC2-v0.2.3-beta.tar.gz',
        'sha256': '7b3b55b25ffdece539bac92c4c66fbd00945fde72c846e6e7e6efe8e9f579a13'},
}
UV_URL = 'https://github.com/astral-sh/uv/releases/download/0.9.5/uv-x86_64-unknown-linux-gnu.tar.gz'
UV_SHA = '2cf10babba653310606f8b49876cfb679928669e7ddaa1fb41fb00ce73e64f66'


def safe_extract(archive, destination, max_bytes=16 * 1024**3):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:gz') as stream:
        members = stream.getmembers()
        if len(members) > 200000 or sum(max(0, m.size) for m in members) > max_bytes:
            raise ValueError('工具包解压体积超出限制。')
        for member in members:
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError('工具包包含不安全的文件路径。')
            if member.isdev() or member.isfifo():
                raise ValueError('工具包包含不支持的设备文件。')
        stream.extractall(destination, members=members, filter='data')


def missing_system():
    missing = [name for name in ('tcsh', 'gawk', 'perl', 'R') if not shutil.which(name)]
    if not Path('/etc/ssl/certs/ca-certificates.crt').is_file():
        missing.append('ca-certificates')
    return missing


class SetupWizard:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.state = {'busy': False, 'ready': False, 'progress': 0, 'stage': '尚未配置', 'logs': [], 'error': None}
        config = self.root / 'local-tools.json'
        if config.is_file():
            try:
                value = json.loads(config.read_text())
                self.state['ready'] = Path(value['python']).is_file() and all(Path(value[k]).is_dir() for k in ('tc1','tc2'))
                if self.state['ready']:
                    self.state.update(stage='本地环境已配置', progress=100)
            except (ValueError, KeyError, OSError):
                pass

    def snapshot(self):
        with self.lock:
            return {**self.state, 'logs': list(self.state['logs']),
                'supported': platform.system() == 'Linux' and platform.machine() == 'x86_64',
                'missing_system': missing_system(), 'packages': PACKAGES,
                'data_directory': str(self.root), 'free_bytes': shutil.disk_usage(self.root).free}

    def update(self, progress, message):
        with self.lock:
            self.state.update(progress=progress, stage=message)
            self.state['logs'] = (self.state['logs'] + [message])[-300:]

    def start(self, action, *, license_accepted=False, system_accepted=False, archives=None):
        with self.lock:
            if self.state['busy']:
                raise ValueError('首次配置正在进行，请等待完成。')
            if platform.system() != 'Linux' or platform.machine() != 'x86_64':
                raise ValueError('本地配置目前支持 Linux x86_64；其他平台可使用在线或导入表模式。')
            if action != 'check' and any(c.isspace() or c in "'\"`$;&|<>(){}*?[]\\!" for c in str(self.root)):
                raise ValueError('本地模型的数据目录不能含空格或 shell 特殊字符。请使用默认用户数据位置。')
            if action != 'check' and not license_accepted:
                raise ValueError('请先阅读官方许可，并确认你有权使用这些预测工具。')
            if action != 'check' and missing_system() and not system_accepted:
                raise ValueError('需要安装系统组件。请勾选允许系统授权；密码只在系统授权窗口输入。')
            self.state.update(busy=True, error=None, progress=0, stage='准备配置')
            if action != 'check':
                self.state['ready'] = False
        thread = threading.Thread(target=self._work, args=(action, archives or {}, license_accepted), daemon=True)
        thread.start()

    def command(self, args, progress, message, env=None):
        self.update(progress, message)
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', env=env or external_environment()) as process:
            for line in process.stdout:
                if line.strip():
                    with self.lock:
                        self.state['logs'] = (self.state['logs'] + [line.strip()])[-300:]
            if process.wait():
                raise ValueError('配置命令未完成。请查看下方日志；若取消了系统授权，可重新点击配置。')

    def download(self, url, path, expected_hash, expected_size=None, progress=10, label='下载'):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and sha256(path) == expected_hash:
            self.update(progress, label + '：使用已校验的下载文件')
            return path
        part = path.with_suffix(path.suffix + '.part')
        offset = part.stat().st_size if part.exists() else 0
        headers = {'User-Agent': 'EpitopeScreening-LocalSetup'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=90) as response:
            append = offset > 0 and response.status == 206
            received = offset if append else 0
            total = expected_size or (int(response.headers.get('Content-Length', 0)) + received)
            last = -1
            with part.open('ab' if append else 'wb') as output:
                while chunk := response.read(2 * 1024**2):
                    output.write(chunk)
                    received += len(chunk)
                    percent = int(100 * received / total) if total else 0
                    if percent != last:
                        self.update(progress, f'{label}：{received / 1024**2:.0f} / {total / 1024**2:.0f} MB ({percent}%)')
                        last = percent
        if expected_size and part.stat().st_size != expected_size:
            raise ValueError(label + ' 下载不完整，可再次点击配置继续下载。')
        if sha256(part) != expected_hash:
            part.unlink(missing_ok=True)
            raise ValueError(label + ' 校验失败，已丢弃文件，请重试。')
        part.replace(path)
        return path

    def _work(self, action, archives, accepted):
        try:
            if action == 'check':
                validate_runtime(json.loads((self.root / 'local-tools.json').read_text()))
            else:
                allocated = sum(p.stat().st_size for name in ('uploads','downloads','tools','runtime')
                    for p in (self.root/name).rglob('*') if p.is_file() and not p.is_symlink())
                needed = max(512*1024**2, 12*1024**3-allocated)
                if shutil.disk_usage(self.root).free < needed:
                    raise ValueError('首次配置建议至少留出 12 GB 可用空间。请清理磁盘后重试。')
                if missing_system():
                    if not shutil.which('apt-get'):
                        raise ValueError('自动安装系统组件目前支持 Ubuntu/Debian。')
                    command = ['/bin/sh', '-c', 'export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get install -y --no-install-recommends tcsh gawk perl r-base-core ca-certificates']
                    if os.geteuid() != 0:
                        if not shutil.which('pkexec'):
                            raise ValueError('缺少系统图形授权组件 pkexec，请管理员先安装 tcsh、gawk、Perl、R 和 ca-certificates。')
                        command = ['pkexec'] + command
                    self.command(command, 5, '安装系统组件；请在系统授权窗口确认')
                folders = {}
                for key in ('tc1','tc2'):
                    package = PACKAGES[key]
                    progress = 15 if key == 'tc1' else 40
                    if key in archives:
                        archive = Path(archives[key])
                        self.update(progress, f'校验导入的 {key.upper()} 工具包')
                        if sha256(archive) != package['sha256']:
                            raise ValueError(f"{key.upper()} 工具包不是当前支持的 {package['version']}。请使用页面所列的官方对应版本。")
                    else:
                        archive = self.download(package['url'], self.root/'downloads'/(key+'.tar.gz'),
                            package['sha256'], package['size'], progress, key.upper())
                    target = self.root/'tools'/key
                    self.update(progress + 15, f'解压 {key.upper()}，请稍候')
                    if target.exists():
                        shutil.rmtree(target)
                    safe_extract(archive, target)
                    entry = 'tcell_mhci.py' if key == 'tc1' else 'tcell_mhcii.py'
                    found = list(target.rglob('src/' + entry))
                    if len(found) != 1:
                        raise ValueError(key.upper() + ' 工具包内未找到唯一的程序入口。')
                    folders[key] = str(found[0].parent.parent)
                self.update(65, '准备独立的 Python 运行环境')
                archive = self.download(UV_URL, self.root/'downloads/uv.tar.gz', UV_SHA, progress=65, label='运行环境管理器')
                uv_root = self.root/'runtime/uv'
                safe_extract(archive, uv_root, 128 * 1024**2)
                uv = next(uv_root.rglob('uv'))
                uv.chmod(0o755)
                env = external_environment()
                env.update(UV_PYTHON_INSTALL_DIR=str(self.root/'runtime/python'), UV_CACHE_DIR=str(self.root/'runtime/cache'))
                venv = self.root/'runtime/predictor'
                python = venv/'bin/python'
                if not python.is_file():
                    self.command([str(uv), 'venv', '--managed-python', '--python', '3.11.14', str(venv)], 70, '自动安装 Python 3.11', env)
                self.command([str(uv), 'pip', 'install', '--python', str(python), '-r', str(ASSETS/'requirements-local.txt')], 80, '安装预测所需依赖', env)
                config = {**folders, 'python': str(python), 'workers': 2}
                validate_runtime(config)
                self.update(90, '记录模型签名；首次配置可能需要几分钟')
                config['tool_signatures'] = {key: tool_signature(folders[key]) for key in folders}
                write_json(self.root/'local-tools.json', config)
                write_json(self.root/'setup-record.json', {'license_confirmed': accepted,
                    'time': datetime.now(timezone.utc).isoformat(), 'packages': PACKAGES})
            with self.lock:
                self.state['ready'] = True
            self.update(100, '配置完成，可以开始本地分析')
        except Exception as error:
            with self.lock:
                self.state.update(error=str(error), stage='配置尚未完成', ready=False)
                self.state['logs'] = (self.state['logs'] + [str(error)])[-300:]
        finally:
            with self.lock:
                self.state['busy'] = False
