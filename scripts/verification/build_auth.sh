#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
[[ $(git -C vendor/wifi-framework rev-parse HEAD) == 7a173c1824caa95a848f351fe6eab2d5e5b389e1 ]] || { echo 'Framework source revision mismatch'; exit 1; }
[[ ! -e work/hostap-auth ]] || { echo 'Existing instrumented build preserved'; exit 1; }
mkdir -p work
cp -a vendor/wifi-framework/dependencies/hostap_2_10 work/hostap-auth
python3 scripts/verification/instrument.py work/hostap-auth
(cd work/hostap-auth/hostapd && make clean && make -j2)
(cd work/hostap-auth/wpa_supplicant && sed -i '/CONFIG_FRAMEWORK_EXTENSIONS=/d' .config && make clean && make -j2)
sha256sum work/hostap-auth/hostapd/hostapd work/hostap-auth/wpa_supplicant/wpa_supplicant
