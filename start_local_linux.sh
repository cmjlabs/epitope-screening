#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ! -f local-tools.json || ! -x .venv/bin/python ]]; then
  echo '请先运行 setup_local_linux.sh，按官方许可配置 TC1/TC2。' >&2
  exit 2
fi
export EPITOPE_DEFAULT_MODE=local
exec .venv/bin/python -m epitope_screening gui
