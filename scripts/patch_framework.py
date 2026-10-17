"""只使用 hwsim，禁止 Framework 改变全局 rfkill 状态。"""
from pathlib import Path
root = Path(__file__).resolve().parents[1]
p = root / "vendor/wifi-framework/library/daemon.py"
s = p.read_text()
old = 'subprocess.check_output(["rfkill", "unblock", "wifi"])'
new = 'pass # ReconnectGuard: hwsim only; do not change global rfkill'
if old in s:
    p.write_text(s.replace(old, new))
elif new not in s:
    raise SystemExit("上游代码与预期不符，拒绝盲目修改")

s = p.read_text()
s = s.replace('["-dd", "-K"]', '["-dd"]').replace('["-d", "-K"]', '["-d"]').replace('["-K"]', '[]')
p.write_text(s)
