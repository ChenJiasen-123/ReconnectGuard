#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
unset PYTHONPATH

if [[ -x .venv/bin/python ]]; then
  PROJECT_PYTHON="$PWD/.venv/bin/python"
else
  PROJECT_PYTHON="$(command -v python3)"
fi
"$PROJECT_PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ is required"'

mkdir -p vendor work
if [[ ! -d vendor/wifi-framework/.git ]]; then
  git clone --recursive https://github.com/domienschepers/wifi-framework.git vendor/wifi-framework
fi
git -C vendor/wifi-framework checkout 7a173c1824caa95a848f351fe6eab2d5e5b389e1
git -C vendor/wifi-framework submodule update --init --recursive
sudo apt-get update
sudo apt-get install -y curl git make gcc python3-venv net-tools iw rfkill iproute2 tcpdump wpasupplicant libdbus-1-dev libnl-3-dev libnl-genl-3-dev libnl-route-3-dev libssl-dev
(cd vendor/wifi-framework/dependencies && ./build.sh) > work/build.log 2>&1
WPA_ARCHIVE=work/wpa_supplicant-2.10.tar.gz
WPA_SHA256=20df7ae5154b3830355f8ab4269123a87affdea59fe74fe9292a91d0d7e17b2f
if [[ ! -f "$WPA_ARCHIVE" ]]; then
  curl -fL --retry 3 -o "$WPA_ARCHIVE" https://w1.fi/releases/wpa_supplicant-2.10.tar.gz
fi
echo "$WPA_SHA256  $WPA_ARCHIVE" | sha256sum -c -
if [[ ! -d vendor/wpa_supplicant-2.10 ]]; then
  tar -xzf "$WPA_ARCHIVE" -C vendor
fi
(cd vendor/wpa_supplicant-2.10/wpa_supplicant && cp defconfig .config && make clean && make -j 2) > work/stock-wpa-build.log 2>&1
"$PROJECT_PYTHON" -m venv vendor/wifi-framework/setup/venv
vendor/wifi-framework/setup/venv/bin/pip install -r requirements-framework.txt > work/python-install.log 2>&1
"$PROJECT_PYTHON" scripts/patch_framework.py
git -C vendor/wifi-framework diff > work/framework.patch
env -u PYTHONPATH vendor/wifi-framework/setup/venv/bin/pip freeze > work/python-lock.txt
printf '依赖已准备；执行 sudo bash scripts/start.sh --client framework --probe connect\n'
