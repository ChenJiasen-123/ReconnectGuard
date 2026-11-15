"""匹配握手身份；保留抓包顺序，允许由本轮双端日志解释的单一 M3/M4 倒序。"""
import itertools
import re

from .frames import eapol_message

STAGES = ('eapol_m1', 'eapol_m2', 'eapol_m3', 'eapol_m4')


def log_window(text, disconnect, connected):
    """只接受首个断开后的单次连接；第二次断开使窗口有歧义。"""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if disconnect in line), None)
    if start is None:
        return []
    for end in range(start + 1, len(lines)):
        if disconnect in lines[end]:
            return []
        if connected in lines[end]:
            return list(enumerate(lines[start + 1:end + 1], start + 2))
    return []


def _hex_refs(window, marker, value):
    found = []
    for number, line in window:
        if marker not in line:
            continue
        match = re.search(r'hexdump\(len=32\):\s*((?:[0-9a-fA-F]{2}\s*){32})\s*$', line)
        if match and ''.join(match.group(1).split()).lower() == value:
            found.append(number)
    return found


def corroborate(sta_text, ap_text, ap, sta, path):
    """按 peer、恢复世代、ANonce/SNonce 和协议进展交叉核对，拒绝多次握手拼接。"""
    sw = log_window(sta_text, 'CTRL-EVENT-DISCONNECTED bssid=' + ap,
                    'CTRL-EVENT-CONNECTED - Connection to ' + ap)
    aw = log_window(ap_text, 'AP-STA-DISCONNECTED ' + sta, 'AP-STA-CONNECTED ' + sta)
    sta_patterns = [
        'RX message 1 of 4-Way Handshake from ' + ap,
        'Sending EAPOL-Key 2/4',
        'RX message 3 of 4-Way Handshake from ' + ap,
        'Sending EAPOL-Key 4/4',
        'Key negotiation completed with ' + ap,
        'CTRL-EVENT-CONNECTED - Connection to ' + ap,
    ]
    ap_patterns = [
        'STA ' + sta + ' WPA: received EAPOL-Key frame (2/4 Pairwise)',
        'STA ' + sta + ' WPA: received EAPOL-Key frame (4/4 Pairwise)',
        sta + ' WPA_PTK entering state PTKINITDONE',
        'AP-STA-CONNECTED ' + sta,
    ]
    refs = []
    for role, window, patterns in [('sta', sw, sta_patterns), ('ap', aw, ap_patterns)]:
        numbers = []
        for pattern in patterns:
            matches = [number for number, line in window if pattern in line]
            if len(matches) != 1:
                return {'status': 'insufficient_evidence', 'reason': role + ': nonunique or missing handshake progress'}
            numbers.append(matches[0])
        if numbers != sorted(numbers):
            return {'status': 'insufficient_evidence', 'reason': role + ': contradictory protocol progress order'}
        refs.extend(f'{role}.log#line={n}' for n in numbers)
    for role, window, marker, value in [
        ('sta', sw, '  key_nonce', path[0]['nonce']),
        ('sta', sw, 'WPA: Renewed SNonce', path[1]['nonce']),
        ('ap', aw, 'WPA: Assign ANonce', path[0]['nonce']),
        ('ap', aw, 'WPA: Received Key Nonce', path[1]['nonce']),
    ]:
        matches = _hex_refs(window, marker, value)
        if not matches:
            return {'status': 'insufficient_evidence', 'reason': role + ': nonce binding missing'}
        refs.extend(f'{role}.log#line={n}' for n in matches)
    return {'status': 'corroborated', 'log_refs': refs}


def _valid_nonce(value):
    return bool(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) and value != '0' * 64)


def match_handshake(selected, ap, sta, sta_text='', ap_text='', no_kernel_drops=False):
    by_stage = {name: [] for name in STAGES}
    for row in selected:
        stage = eapol_message(row.get('eapol_key_info'))
        if (stage not in by_stage or row.get('bad_fcs') or row.get('bad_plcp') or
                row.get('radiotap_tx_flags', 0) & 1):
            continue
        source, dest = (ap, sta) if stage in ('eapol_m1', 'eapol_m3') else (sta, ap)
        if str(row.get('addr2')).lower() == source and str(row.get('addr1')).lower() == dest:
            by_stage[stage].append(row)
    diagnostic = {'status': 'insufficient_evidence', 'reason': 'missing or inconsistent handshake identity',
                  'observed_stages': {s: [r['frame'] for r in rs] for s, rs in by_stage.items()},
                  'capture_order_is_air_order': False}
    if not all(by_stage.values()):
        return None, diagnostic
    # 完全相同的 EAPOL 重传不构成另一组握手；缺失 payload 哈希时逐帧区分。
    groups = {}
    combinations = 0
    for path in itertools.product(*(by_stage[s] for s in STAGES)):
        combinations += 1
        if combinations > 100000:
            diagnostic['reason'] = 'candidate budget exceeded; no classification'
            return None, diagnostic
        m1, m2, m3, m4 = path
        c1, c2, c3, c4 = (r.get('replay_counter') for r in path)
        if (not all(isinstance(c, int) for c in (c1, c2, c3, c4)) or
                c1 != c2 or c3 <= c1 or c3 != c4 or not _valid_nonce(m1.get('nonce')) or
                not _valid_nonce(m2.get('nonce')) or m1['nonce'] != m3.get('nonce') or
                m4.get('nonce') != '0' * 64):
            continue
        signature = tuple(r.get('eapol_sha256', 'frame:' + str(r['frame'])) for r in path)
        strict = all(a['time'] < b['time'] and a['frame'] < b['frame'] for a, b in zip(path, path[1:]))
        swapped = sorted(path, key=lambda r: (r['time'], r['frame'])) == [m1, m2, m4, m3]
        if strict or swapped:
            previous = groups.get(signature)
            if previous is None or strict:
                groups[signature] = (list(path), strict)
    diagnostic['candidate_groups'] = len(groups)
    if len(groups) != 1:
        if groups:
            diagnostic['reason'] = 'multiple handshake identities in recovery window'
        return None, diagnostic
    path, strict = next(iter(groups.values()))
    observed = sorted(path, key=lambda r: (r['time'], r['frame']))
    diagnostic.update(
        protocol_frame_refs=['trace.pcap#frame=' + str(r['frame']) for r in path],
        capture_order=[eapol_message(r['eapol_key_info']) for r in observed],
        capture_frame_refs=['trace.pcap#frame=' + str(r['frame']) for r in observed],
        strict_capture_order=strict,
    )
    if strict:
        diagnostic.update(status='matched_in_capture_order', reason='unique identity and ordered capture')
        return path, diagnostic
    diagnostic['m3_after_m4_capture_ms'] = round((path[2]['time'] - path[3]['time']) * 1000, 3)
    origin = [r.get('capture_origin') for r in path]
    if not no_kernel_drops or origin != ['local_tx_status', 'radio_rx', 'local_tx_status', 'radio_rx']:
        diagnostic['reason'] = 'M3/M4 capture inversion lacks clean TX-status/RX provenance'
        return None, diagnostic
    corroboration = corroborate(sta_text, ap_text, ap, sta, path)
    diagnostic['endpoint_corroboration'] = corroboration
    if corroboration['status'] != 'corroborated':
        diagnostic['reason'] = 'M3/M4 capture inversion lacks nonce-bound endpoint corroboration'
        return None, diagnostic
    diagnostic.update(status='matched_with_capture_inversion',
                      reason='identity matched; endpoint logs show M3 before M4; capture inversion retained')
    return path, diagnostic
