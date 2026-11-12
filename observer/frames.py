"""从真实 hwsim 轮次目录提取帧级与日志级证据（只读）。

这个模块是 ``observer.oracle:Oracle`` 的**事后证据补充**，不是检查器本身，
也不改 Runner / Observer 的任何行为。它回答三个问题：

1. 本轮重连尝试在空口上到底出现了哪些帧（真实 frame 编号 + 单调时间）；
2. 这次重连走的是完整 SAE 还是 PMKSA 缓存复用（SAE 认证帧 / Association
   Request 的 PMKID / supplicant 日志三方互证）；
3. ``authorization_timing`` 为什么仍然只能判 insufficient_evidence：
   ``key_install`` 与 ``port_authorized`` 是控制面事件，不是空口帧，
   帧级契约无法在无帧号的情况下表示它们。

用法（需要 scapy，用 Framework venv 的 Python）::

    vendor/wifi-framework/setup/venv/bin/python -m observer.frames \
        results/<run_id> --output evidence-real.json

无 scapy 时仍可使用本模块的纯函数（RSN IE 解析、帧分类、报告组装），
只有 :func:`read_pcap_frames` 会抛 :class:`MissingScapy`。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

EAPOL_STAGES = ("eapol_m1", "eapol_m2", "eapol_m3", "eapol_m4")
KEY_STAGE = "key_install"
AUTH_STAGES = ("port_authorized", "data_frame")
SAE_STAGES = ("sae_commit", "sae_confirm")
DISCONNECT_MARKER = "CTRL-EVENT-DISCONNECTED"

# EAPOL-Key Key Information 位（802.11 RSN）
KI_KEY_TYPE = 0x0008
KI_INSTALL = 0x0040
KI_ACK = 0x0080
KI_MIC = 0x0100
KI_SECURE = 0x0200

NOT_FRAME_STAGES = {
    KEY_STAGE: "密钥安装是 wpa_supplicant / hostapd 的控制面状态变化，不是空口帧；"
               "事件见 events.jsonl 的 WPA: Key negotiation completed",
    "port_authorized": "端口授权是 AP 的控制面状态变化，不是空口帧；"
                       "AP-STA-CONNECTED 只表明关联，不等于端口授权",
}

LIMITATIONS = (
    "frame 编号是 trace.pcap 中从 1 开始的顺序号，只在本轮抓包内有效。",
    "pmksa_presence=present 只证明缓存表有条目；本次是否复用需 SAE 帧 + PMKID + 日志三方互证。",
    "控制面事件时间来自 events.jsonl 的 monotonic_s / received_at，帧时间来自 pcap；"
    "两者通过固定偏移换算，跨轮次不可比较。",
    "key_install / port_authorized 没有对应帧号，帧级契约无法表示，"
    "authorization_timing 必须保持 insufficient_evidence。",
    "本模块不下发动作、不修改产物，也不把恢复成功当作安全通过。",
)


class MissingScapy(RuntimeError):
    """读取 pcap 需要 scapy；纯函数不依赖它。"""


def parse_rsn_pmkids(body: bytes) -> dict:
    """解析 RSN IE 的 PMKID 列表。纯函数，可单测。

    RSN IE 结构：version(2) group(4) pairwise_count(2) pairwise_list(4n)
    akm_count(2) akm_list(4n) capabilities(2) [pmkid_count(2) pmkids(16n)]
    """
    try:
        if len(body) < 8:
            return {"error": "RSN IE 太短"}
        off = 2 + 4
        pairwise_count = int.from_bytes(body[off:off + 2], "little")
        off += 2 + 4 * pairwise_count
        if off + 2 > len(body):
            return {"error": "RSN IE 缺少 AKM 计数字段"}
        akm_count = int.from_bytes(body[off:off + 2], "little")
        off += 2 + 4 * akm_count + 2  # akm list + capabilities
        if off + 2 > len(body):
            return {"pmkid_count": 0, "pmkids": [], "pmkid_field_absent": True}
        count = int.from_bytes(body[off:off + 2], "little")
        off += 2
        if count * 16 > len(body) - off:
            return {"error": "PMKID 数量超过 IE 长度"}
        return {"pmkid_count": count,
                "pmkids": [body[off + 16 * i: off + 16 * (i + 1)].hex()
                           for i in range(count)],
                "pmkid_field_absent": False}
    except (IndexError, TypeError, ValueError) as exc:
        return {"error": "RSN IE 解析失败: %s" % exc}


def eapol_message(key_info: int | None) -> str | None:
    """按 Key Information 位判定四次握手消息序号。纯函数。"""
    if key_info is None:
        return None
    pairwise = bool(key_info & KI_KEY_TYPE)
    install = bool(key_info & KI_INSTALL)
    ack = bool(key_info & KI_ACK)
    mic = bool(key_info & KI_MIC)
    secure = bool(key_info & KI_SECURE)
    if not pairwise:
        return None
    if ack and not mic and not install and not secure:
        return "eapol_m1"
    if mic and not ack and not install and not secure:
        return "eapol_m2"
    if mic and ack and install and secure:
        return "eapol_m3"
    if mic and not ack and not install and secure:
        return "eapol_m4"
    return "eapol_unclassified"


def classify_frame(row: dict) -> list[str]:
    """把标准化帧记录映射为阶段标签。纯函数，可单测。"""
    labels = []
    frame_type, subtype = row.get("type"), row.get("subtype")
    if frame_type == 0 and subtype == 11:
        algo, seq = row.get("auth_algo"), row.get("auth_seq")
        if algo == 3 and seq == 1:
            labels.append("sae_commit")
        elif algo == 3 and seq == 2:
            labels.append("sae_confirm")
        elif algo == 0:
            labels.append("open_system_auth")
    if frame_type == 0 and subtype == 0 and row.get("rsn_pmkids"):
        labels.append("assoc_req_with_pmkid")
    if frame_type == 0 and subtype == 12:
        labels.append("deauth")
    if frame_type == 0 and subtype == 10:
        labels.append("disassoc")
    message = eapol_message(row.get("eapol_key_info"))
    if message and message.startswith("eapol_"):
        labels.append(message)
    if frame_type == 2 and row.get("protected") and "eapol_key_info" not in row:
        labels.append("data_frame")
    return labels


def read_pcap_frames(path: Path) -> list[dict]:
    """读取 pcap，返回标准化帧记录。需要 scapy（延迟导入）。"""
    try:
        from scapy.all import PcapReader, Dot11, Dot11Auth, Dot11Elt, EAPOL
    except ImportError as exc:
        raise MissingScapy("读取 trace.pcap 需要 scapy：" + str(exc)) from exc

    rows = []
    with PcapReader(str(path)) as reader:
        for number, packet in enumerate(reader, 1):
            if not packet.haslayer(Dot11):
                continue
            dot11 = packet[Dot11]
            row = {
                "frame": number,
                "time": float(packet.time),
                "type": int(dot11.type),
                "subtype": int(dot11.subtype),
                "protected": bool(int(dot11.FCfield) & 0x40),
                "addr1": dot11.addr1, "addr2": dot11.addr2, "addr3": dot11.addr3,
            }
            if dot11.type == 0 and dot11.subtype == 11 and packet.haslayer(Dot11Auth):
                auth = packet[Dot11Auth]
                row["auth_algo"] = int(auth.algo)
                row["auth_seq"] = int(auth.seqnum)
            if dot11.type == 0 and dot11.subtype == 0:
                layer = packet
                while layer is not None:
                    if isinstance(layer, Dot11Elt) and layer.ID == 48:
                        parsed = parse_rsn_pmkids(bytes(layer.info))
                        if parsed.get("pmkids"):
                            row["rsn_pmkids"] = parsed["pmkids"]
                        break
                    layer = layer.payload if hasattr(layer, "payload") else None
            if packet.haslayer(EAPOL):
                payload = bytes(packet[EAPOL].payload)
                if len(payload) >= 3 and payload[0] == 2:  # RSN Key descriptor
                    row["eapol_key_info"] = int.from_bytes(payload[1:3], "big")
            rows.append(row)
    return rows


def load_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def clock_offset(records: list[dict]) -> float | None:
    """由 events.jsonl 的 (monotonic_s, received_at) 求固定时钟偏移。纯函数。"""
    offsets = []
    for record in records:
        clock, stamp = record.get("monotonic_s"), record.get("received_at")
        if not isinstance(clock, (int, float)) or not stamp:
            continue
        try:
            wall = datetime.fromisoformat(str(stamp)).timestamp()
        except ValueError:
            continue
        offsets.append(float(clock) - wall)
    return median(offsets) if offsets else None


def to_monotonic(wall_time: float, offset: float | None) -> float | None:
    # 保留完整精度：真实帧时间与控制事件只差毫秒量级，四舍五入会抹掉先后关系。
    return wall_time + offset if offset is not None else None


def _utc(wall_time: float) -> str:
    return datetime.fromtimestamp(wall_time, tz=timezone.utc).isoformat(
        timespec="microseconds").replace("+00:00", "Z")


def _disconnect_wall_time(journal: list[dict]) -> float | None:
    for record in journal:
        if DISCONNECT_MARKER in str(record.get("raw_event", "")):
            try:
                return datetime.fromisoformat(str(record["received_at"])).timestamp()
            except (KeyError, ValueError):
                continue
    return None


def stage_index(frames: list[dict]) -> dict[str, list[dict]]:
    """按阶段汇总帧，保留每个阶段的全部真实帧号。纯函数。"""
    index: dict[str, list[dict]] = {}
    for row in frames:
        for label in classify_frame(row):
            index.setdefault(label, []).append(row)
    return index


def _log_hits(path: Path, markers: tuple[str, ...],
              exclude: tuple[str, ...] = ()) -> list[dict]:
    if not path.exists():
        return []
    hits = []
    for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if any(bad in line for bad in exclude):
            continue
        if any(marker in line for marker in markers):
            hits.append({"ref": "%s#line=%d" % (path.name, number),
                         "line": line.strip()[:240]})
    return hits


def build_real_evidence(run_dir) -> dict:
    """组装一轮真实证据报告。缺 pcap 时抛 MissingScapy / FileNotFoundError。"""
    path = Path(run_dir)
    context = json.loads((path / "context.json").read_text(encoding="utf-8"))
    run_id = context.get("run_id") or path.name
    attempt_id = context.get("attempt_id")
    journal = load_journal(path / "events.jsonl")
    offset = clock_offset(journal)
    frames = read_pcap_frames(path / "trace.pcap")
    boundary = _disconnect_wall_time(journal)
    epoch = [row for row in frames if boundary is None or row["time"] > boundary]
    index = stage_index(epoch)

    def one(stage):
        return index.get(stage, [])[:1]

    timeline = []
    for stage in EAPOL_STAGES + ("data_frame",):
        rows = one(stage)
        if not rows:
            continue
        row = rows[0]
        timeline.append({
            "frame": row["frame"],
            "stage": stage,
            "monotonic_s": to_monotonic(row["time"], offset),
            "utc": _utc(row["time"]),
            "source": "trace.pcap",
        })
    timeline = [row for row in timeline if row["monotonic_s"] is not None]
    timeline.sort(key=lambda row: row["monotonic_s"])

    sae_frames = one("sae_commit") + one("sae_confirm")
    pmkid_frames = one("assoc_req_with_pmkid")
    pmkids = sorted({pmkid for row in index.get("assoc_req_with_pmkid", [])
                     for pmkid in row.get("rsn_pmkids", [])})
    reuse_log = _log_hits(path / "sta.log", (
        "PMKSA cache entry found", "try to use PMKSA caching instead of new SAE",
        "PMKID from assoc IE found from PMKSA cache", "PMKSA caching was used",
        "matched PMKID"), exclude=("No PMKSA cache entry found",))
    sae_log = _log_hits(path / "sta.log", (
        "No PMKSA cache entry found", "SAE: PMKID"))
    cache_log = _log_hits(path / "events.jsonl", ("PMKSA-CACHE-ADDED", "PMKSA-CACHE-REMOVED"))

    reuse_claimed = bool(pmkid_frames) and bool(reuse_log) and not sae_frames
    if reuse_claimed:
        auth_path = "pmksa_reuse"
        auth_reason = ("本轮重连窗口内无 SAE 认证帧，Association Request 携带 PMKID，"
                       "且 supplicant 日志记录缓存命中与 PMKID 匹配")
    elif sae_frames and all(one(stage) for stage in EAPOL_STAGES):
        auth_path = "full_sae"
        auth_reason = "本轮重连窗口内有 SAE commit/confirm 与完整四次握手帧"
    else:
        auth_path = "unknown"
        auth_reason = "缺少本轮重连窗口的 SAE 帧或 PMKID 匹配证据，保留 unknown"

    control = []
    for record in journal:
        raw = str(record.get("raw_event", ""))
        if any(key in raw for key in ("PMKSA-CACHE-ADDED", "PMKSA-CACHE-REMOVED",
                                      "AP-STA-CONNECTED", "WPA: Key negotiation completed",
                                      "CTRL-EVENT-CONNECTED", DISCONNECT_MARKER)):
            control.append({
                "ref": "events.jsonl#seq=%s" % record.get("seq"),
                "seq": record.get("seq"),
                "source": record.get("source"),
                "received_at": record.get("received_at"),
                "monotonic_s": record.get("monotonic_s"),
                "raw_event": raw[:200],
            })

    frame_evidence = {
        "source": "trace.pcap",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "sae_observed": bool(sae_frames),
        "pmkid_match": bool(pmkid_frames) if reuse_claimed else None,
        "timeline": timeline,
    }
    present_stages = [row["stage"] for row in timeline]
    missing = [stage for stage in EAPOL_STAGES + (KEY_STAGE,)
               if stage not in present_stages]

    return {
        "schema_version": 1,
        "scope": "real hwsim evidence extraction; not a security verdict",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "pcap_frames_total": len(frames),
        "disconnect_boundary_utc": _utc(boundary) if boundary else None,
        "reconnect_window_frames": len(epoch),
        "clock_offset": offset,
        "frame_inventory": {
            stage: [{"frame": row["frame"], "utc": _utc(row["time"]),
                     "protected": row.get("protected")} for row in rows]
            for stage, rows in sorted(index.items())
        },
        "auth_path": {
            "value": auth_path,
            "reason": auth_reason,
            "sae_frames": [row["frame"] for row in sae_frames],
            "assoc_req_with_pmkid_frames": [row["frame"] for row in index.get("assoc_req_with_pmkid", [])],
            "pmkids": pmkids,
            "pmksa_reuse_log": reuse_log[:12],
            "sae_or_no_cache_log": sae_log[:8],
        },
        "cache": {
            "pmksa_cache_added_or_removed": cache_log,
            "note": "缓存条目存在不等于复用；复用结论由 auth_path 的帧 + PMKID + 日志共同支撑",
        },
        "control_timeline": control,
        "frame_evidence": frame_evidence,
        "authorization_timing": {
            "status": "insufficient_evidence",
            "reason": "帧级证据缺少 %s 阶段" % ("、".join(missing) if missing else "关键阶段"),
            "not_frame_stages": NOT_FRAME_STAGES,
            "note": "不把 AP-STA-CONNECTED 当作端口授权；不把恢复成功当作安全通过",
        },
        "security_verdict": "not_evaluated",
        "limitations": list(LIMITATIONS),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", help="一轮结果目录，含 context.json / events.jsonl / trace.pcap")
    parser.add_argument("--output", type=Path, help="另存报告路径；默认打印到标准输出")
    args = parser.parse_args(argv)
    report = build_real_evidence(args.run_dir)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print("真实证据报告已写入 %s" % args.output)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
