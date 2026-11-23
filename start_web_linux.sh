#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -x ./Start-Web ]]; then
  exec ./Start-Web "$@"
elif [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python -m epitope_screening web "$@"
else
  echo '这是源码目录，尚未配置 Python 环境。普通用户请下载 Releases 中的 Linux 网页版压缩包，解压后双击 Start-Web。' >&2
  exit 2
fi
