#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
py=python3
if [[ -x .venv/bin/python ]]; then py=.venv/bin/python
elif ! python3 -c 'import openpyxl' 2>/dev/null; then py="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"; fi
exec "$py" -m unittest discover -s tests -v
