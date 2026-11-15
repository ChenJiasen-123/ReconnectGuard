#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
fw=vendor/wifi-framework
expected=7a173c1824caa95a848f351fe6eab2d5e5b389e1
[[ $(git -C "$fw" rev-parse HEAD) == "$expected" ]] || { echo 'Framework source revision mismatch'; exit 1; }
[[ ! -e work/hostap-pair ]] || { echo 'Existing experimental build preserved; choose a fresh checkout'; exit 1; }
mkdir -p work
cp -a "$fw/dependencies/hostap_2_10" work/hostap-pair
cd work/hostap-pair/wpa_supplicant
[[ -f .config ]] || cp defconfig .config
sed -i '/CONFIG_FRAMEWORK_EXTENSIONS=/d' .config
# Baseline enables SAE and testing controls equally for both binaries.
printf '\nCONFIG_SAE=y\nCONFIG_TESTING_OPTIONS=y\n' >> .config
make clean
make -j2
cp wpa_supplicant ../../wpa-before
python3 ../../../scripts/research/backport_sae_reject.py ..
make -j2
cp wpa_supplicant ../../wpa-after
sha256sum ../../wpa-before ../../wpa-after
