#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
project_root="$PWD"
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo '本地预测目前支持 Linux x86_64（Ubuntu/Debian）。' >&2
  exit 2
fi
if [[ $# != 2 || ! -f "$1/src/tcell_mhci.py" || ! -f "$2/src/tcell_mhcii.py" ]]; then
  echo '先从 IEDB 官方下载并解压 TC1/TC2，阅读并按条款获取使用许可。'
  echo 'https://nextgen-tools.iedb.org/download-all'
  echo '用法：bash setup_local_linux.sh /路径/ng_tc1-版本 /路径/ng_tc2-版本'
  exit 2
fi
# Only system dependencies require sudo. All Python environments remain in this project.
missing=0
for executable in python3 tcsh gawk perl R; do
  command -v "$executable" >/dev/null || missing=1
done
if ! python3 -c 'import venv, tkinter' >/dev/null 2>&1; then missing=1; fi
if [[ $missing == 1 ]]; then
  command -v apt-get >/dev/null || { echo '此安装向导暂支持 Ubuntu/Debian。'; exit 2; }
  privilege=()
  if [[ $(id -u) != 0 ]]; then privilege=(sudo); fi
  "${privilege[@]}" apt-get update
  "${privilege[@]}" apt-get install -y --no-install-recommends python3 python3-venv python3-tk tcsh gawk perl r-base-core
fi
if [[ ! -x .local-bootstrap/bin/python ]]; then python3 -m venv .local-bootstrap; fi
.local-bootstrap/bin/python -m pip install uv==0.9.5
export UV_PYTHON_INSTALL_DIR="$project_root/.local-runtime/python"
export UV_CACHE_DIR="$project_root/.local-runtime/cache"
uv_bin="$project_root/.local-bootstrap/bin/uv"
if [[ ! -x .venv-local-tools/bin/python ]]; then "$uv_bin" venv --managed-python --python 3.11 .venv-local-tools; fi
"$uv_bin" pip install --python .venv-local-tools/bin/python -r requirements-local.txt
if [[ ! -x .venv/bin/python ]]; then "$uv_bin" venv --managed-python --python 3.11 .venv; fi
"$uv_bin" pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python - "$1" "$2" <<'PY'
from pathlib import Path
import json,sys
from epitope_screening.local_iedb import validate_runtime,tool_signature
config={'tc1':str(Path(sys.argv[1]).resolve()),'tc2':str(Path(sys.argv[2]).resolve()),
        'python':str(Path('.venv-local-tools/bin/python').absolute()),'workers':2}
validate_runtime(config)
print('首次记录官方工具和模型数据的哈希，可能需要几分钟……',flush=True)
config['tool_signatures']={key:tool_signature(config[key]) for key in ('tc1','tc2')}
Path('local-tools.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
Path('.venv/dependencies-ready').touch()
print('本地环境配置完成。下一步：bash start_local_linux.sh')
PY
