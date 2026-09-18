#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -x .venv/bin/python ]]; then
  py=.venv/bin/python
elif python3 -c 'import openpyxl' 2>/dev/null; then
  py=python3
elif [[ -x "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" ]]; then
  py="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
else
  echo '请先安装依赖：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'
  exit 1
fi
exec "$py" app.py "$@"
