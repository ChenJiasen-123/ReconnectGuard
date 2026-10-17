"""核实本轮 AP 到客户端的 Deauth 帧头；不判断缓存使用或安全漏洞。"""
import json
import sys
from pathlib import Path
from scapy.all import PcapReader, Dot11

folder = Path(sys.argv[1])
baseline = json.loads((folder / "connected_baseline.json").read_text())
ap = baseline["sta"]["bssid"].lower()
sta = baseline["sta"]["address"].lower()
frames = []
with PcapReader(str(folder / "trace.pcap")) as packets:
    for packet in packets:
        if not packet.haslayer(Dot11):
            continue
        frame = packet[Dot11]
        if (frame.type == 0 and frame.subtype == 12
                and (frame.addr2 or "").lower() == ap
                and (frame.addr1 or "").lower() == sta):
            frames.append({"capture_time": str(packet.time), "source": frame.addr2,
                           "destination": frame.addr1, "protected": bool(int(frame.FCfield) & 0x40)})
result = {"scope": "platform frame evidence", "matched_deauth_frames": frames,
          "count_note": "capture may contain retransmissions; count is not number of logical actions",
          "auth_path": "unknown", "security_verdict": "not_evaluated"}
(folder / "frame-check.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
if not frames:
    raise SystemExit("No matching AP-to-STA Deauth frame found")
