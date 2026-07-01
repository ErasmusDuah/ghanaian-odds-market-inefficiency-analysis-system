#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP="$ROOT/setup_environment.py"
VENV_PYTHON="$ROOT/.venv/bin/python"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 was not found."
  echo "Install Python 3.11 or newer from https://www.python.org/downloads/ or your system package manager."
  exit 1
fi

python3 "$SETUP"

if [ ! -x "$VENV_PYTHON" ]; then
  echo "Virtual environment Python was not found after setup."
  exit 1
fi

"$VENV_PYTHON" "$ROOT/football/fb_run_intensive.py"
