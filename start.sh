#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -z "${PYTHON_BIN:-}" ]; then
  if command -v python3.12 >/dev/null 2>&1; then PYTHON_BIN=python3.12; else PYTHON_BIN=python3; fi
fi
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "Python 3.12+ is required. Set PYTHON_BIN to your Python 3.12 executable.")'
if [ ! -x .venv/bin/python ]; then
  "$PYTHON_BIN" -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8765}" --workers 1 --no-access-log
