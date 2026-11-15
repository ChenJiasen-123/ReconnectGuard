"""倒序只能由本轮、同 peer、同 nonce 的双端证据解释。"""
import copy
import unittest

from observer.handshake import match_handshake
from test_postprocess import AP, STA, frames


def inverted():
    rows = frames()[1:]
    rows[2]['time'], rows[3]['time'] = rows[3]['time'], rows[2]['time']
    rows[2]['frame'], rows[3]['frame'] = rows[3]['frame'], rows[2]['frame']
    for i, row in enumerate(rows):
        row['capture_origin'] = 'local_tx_status' if i in (0, 2) else 'radio_rx'
        row['eapol_sha256'] = str(i)
    return rows


def logs():
    anonce, snonce = 'aa ' * 32, 'bb ' * 32
    sta = '\n'.join([
        'CTRL-EVENT-DISCONNECTED bssid=' + AP,
        '  key_nonce - hexdump(len=32): ' + anonce,
        'WPA: RX message 1 of 4-Way Handshake from ' + AP,
        'WPA: Renewed SNonce - hexdump(len=32): ' + snonce,
        'WPA: Sending EAPOL-Key 2/4',
        'WPA: RX message 3 of 4-Way Handshake from ' + AP,
        'WPA: Sending EAPOL-Key 4/4',
        'WPA: Key negotiation completed with ' + AP,
        'CTRL-EVENT-CONNECTED - Connection to ' + AP,
    ])
    ap = '\n'.join([
        'AP-STA-DISCONNECTED ' + STA,
        'WPA: Assign ANonce - hexdump(len=32): ' + anonce,
        'WPA: Received Key Nonce - hexdump(len=32): ' + snonce,
        'STA ' + STA + ' WPA: received EAPOL-Key frame (2/4 Pairwise)',
        'STA ' + STA + ' WPA: received EAPOL-Key frame (4/4 Pairwise)',
        STA + ' WPA_PTK entering state PTKINITDONE',
        'AP-STA-CONNECTED ' + STA,
    ])
    return sta, ap


def match(rows, sta=None, ap=None, clean=True):
    s, a = logs()
    return match_handshake(rows, AP, STA, s if sta is None else sta, a if ap is None else ap, clean)


class HandshakeTests(unittest.TestCase):
    def test_corroborated_inversion_preserves_original_frames(self):
        rows = inverted();before = copy.deepcopy(rows)
        path, report = match(rows)
        self.assertIsNotNone(path)
        self.assertEqual(report['status'], 'matched_with_capture_inversion')
        self.assertEqual(report['capture_order'], ['eapol_m1','eapol_m2','eapol_m4','eapol_m3'])
        self.assertFalse(report['strict_capture_order'])
        self.assertEqual(rows, before)

    def test_missing_wrong_peer_old_nonce_or_contradictory_logs_do_not_pass(self):
        s, a = logs()
        for changes in [{'sta':''}, {'ap':''}, {'clean':False},
                        {'sta':s.replace(AP, 'ff:ff:ff:ff:ff:ff')},
                        {'ap':a.replace('aa ', 'cc ')},
                        {'sta':s.replace('Sending EAPOL-Key 4/4','RX message 3 of 4-Way Handshake from '+AP)},
                        {'sta':s.replace('WPA: Sending EAPOL-Key 2/4','CTRL-EVENT-DISCONNECTED bssid='+AP)}]:
            with self.subTest(changes=changes):self.assertIsNone(match(inverted(), **changes)[0])
        lines=s.splitlines();lines[5],lines[6]=lines[6],lines[5]
        self.assertIsNone(match(inverted(),sta='\n'.join(lines))[0])

    def test_missing_origin_frame_nonce_replay_or_bad_fcs_do_not_pass(self):
        for idx, key, value in [(2,'capture_origin','unknown'),(2,'nonce','cc'*32),
                                (3,'replay_counter',3),(3,'nonce','cc'*32),
                                (0,'bad_fcs',True),(2,'radiotap_tx_flags',1)]:
            rows=inverted();rows[idx][key]=value
            self.assertIsNone(match(rows)[0])
        self.assertIsNone(match(inverted()[:-1])[0])

    def test_ambiguous_identity_rejected_exact_retransmission_allowed(self):
        rows=inverted();duplicate=copy.deepcopy(rows[1]);duplicate.update(frame=99,eapol_sha256='different')
        self.assertIsNone(match(rows+[duplicate])[0])
        duplicate['eapol_sha256']=rows[1]['eapol_sha256']
        self.assertIsNotNone(match(rows+[duplicate])[0])

    def test_arbitrary_reordering_is_not_repaired(self):
        rows=inverted();rows[0]['time']=rows[-1]['time']+.1
        self.assertIsNone(match(rows)[0])

    def test_authentication_path_needs_separate_cache_or_sae_evidence(self):
        from observer.postprocess import infer_path
        from test_postprocess import LOG
        s,a=logs();s=s.replace('  key_nonce', '\n'.join(LOG.splitlines()[1:3])+'\n  key_nonce',1)
        rows=frames()[:1]+inverted()
        options=dict(ap=AP,sta=STA,start=1,end=2,log_text=s,ap_log_text=a,no_kernel_drops=True)
        self.assertEqual(infer_path(rows,**options)['value'],'pmksa_reuse')
        missing=dict(options,ap_log_text='')
        self.assertEqual(infer_path(rows,**missing)['value'],'unknown')
        for i,(source,seq) in enumerate([(AP,1),(STA,1),(AP,2),(STA,2)]):
            rows.append(dict(frame=100+i,time=1.01+i*.01,type=0,subtype=11,addr2=source,
                             addr1=STA if source==AP else AP,auth_algo=3,auth_seq=seq,auth_status=0))
        for number,row in enumerate(sorted(rows,key=lambda r:r['time']),1):row['frame']=number
        self.assertEqual(infer_path(rows,**options)['value'],'full_sae')
        rows[-1]['auth_status']=77
        self.assertEqual(infer_path(rows,**options)['value'],'unknown')
