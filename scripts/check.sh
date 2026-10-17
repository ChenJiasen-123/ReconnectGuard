#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uname -r
python3 --version
/usr/sbin/wpa_supplicant -v
git -C vendor/wifi-framework rev-parse HEAD
git -C vendor/wifi-framework submodule status
modinfo mac80211_hwsim | head -n 10
python3 -m unittest discover -s tests -v
