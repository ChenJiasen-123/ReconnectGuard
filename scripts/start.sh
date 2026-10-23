#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
unset PYTHONPATH
PYTHON=python3
if [[ -x .venv/bin/python ]]; then
  PYTHON="$PWD/.venv/bin/python"
fi
exec "$PYTHON" -m rg_platform.lab "$@"
