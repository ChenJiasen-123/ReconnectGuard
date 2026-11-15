#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
[[ $EUID == 0 ]] || { echo 'Run in the isolated Linux lab with sudo'; exit 1; }
[[ -x work/wpa-before && -x work/wpa-after ]] || { echo 'Build paired clients first'; exit 1; }
output="results/research-suite-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$output"
for variant in before after; do
  RG_STOCK_WPA_SUPPLICANT="$PWD/work/wpa-$variant" python3 rejection.py --fault reject_deauth --repeat 2 > "$output/$variant.jsonl"
done
for phase in drop_assoc drop_eapol; do
  for duration in 0.2 2 6; do
    python3 rejection.py --fault "$phase" --duration "$duration"
  done
done > "$output/loss.jsonl"
for policy in fixed unguided guided; do
  for seed in 0 1 2; do
    RG_STOCK_WPA_SUPPLICANT="$PWD/work/wpa-before" python3 rejection.py --fault reject_deauth --policy "$policy" --seed "$seed"
  done
done > "$output/methods.jsonl"
echo "$output"
