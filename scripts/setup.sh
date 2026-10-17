#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p vendor work
if [[ ! -d vendor/wifi-framework/.git ]]; then
  git clone --recursive https://github.com/domienschepers/wifi-framework.git vendor/wifi-framework
fi
git -C vendor/wifi-framework checkout 7a173c1824caa95a848f351fe6eab2d5e5b389e1
git -C vendor/wifi-framework submodule update --init --recursive
sudo apt-get update
sudo apt-get install -y git make gcc python3-venv net-tools iw rfkill iproute2 tcpdump wpasupplicant libdbus-1-dev libnl-3-dev libnl-genl-3-dev libnl-route-3-dev libssl-dev
(cd vendor/wifi-framework/dependencies && ./build.sh) > work/build.log 2>&1
python3 -m venv vendor/wifi-framework/setup/venv
vendor/wifi-framework/setup/venv/bin/pip install -r requirements-framework.txt > work/python-install.log 2>&1
python3 scripts/patch_framework.py
git -C vendor/wifi-framework diff > work/framework.patch
vendor/wifi-framework/setup/venv/bin/pip freeze > work/python-lock.txt
printf '依赖已准备；执行 sudo bash scripts/start.sh --client framework --probe connect\n'
