#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
unset PYTHONPATH
PYTHON=python3
if [[ -x .venv/bin/python ]]; then
  PYTHON="$PWD/.venv/bin/python"
fi
uname -r
"$PYTHON" --version
/usr/sbin/wpa_supplicant -v
vendor/wpa_supplicant-2.10/wpa_supplicant/wpa_supplicant -v
git -C vendor/wifi-framework rev-parse HEAD
git -C vendor/wifi-framework submodule status
modinfo mac80211_hwsim | head -n 10
"$PYTHON" -m unittest discover -s tests -v
