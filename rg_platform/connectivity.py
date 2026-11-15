"""固定预算的补充诊断。不能覆盖原单包检查，也不重试直到成功。"""
import os
import re
import subprocess
import time


def followup(lab):
    started = time.monotonic_ns()
    challenge = os.urandom(16).hex()
    before = lab.status()
    address = before.get('address', '')
    if not re.fullmatch(r'[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}', address):
        return {'status':'collection_error','reason':'missing target STA address'}
    ap_before = lab.command('ap','STA '+address,'diagnostic_ap_before',allow_rejected=True)
    neighbor = ['ip','-j','-n',lab.namespaces[1],'neigh','show','dev',lab.sta_if]
    argv = ['ip','netns','exec',lab.namespaces[1],'ping','-n','-c','5','-i','0.2',
            '-W','1','-I',lab.sta_if,'-p',challenge,'10.203.0.1']
    report = {'method':'fixed_five_packet_followup','budget':{'packets':5,'interval_s':.2,'reply_timeout_s':1,'process_timeout_s':5},
              'started_ns':started,'challenge':challenge,'argv':argv,'sta_before':before,'ap_before':ap_before}
    try:
        env = dict(os.environ, LC_ALL='C')
        report['neighbor_before'] = subprocess.run(neighbor,capture_output=True,text=True,timeout=2,check=True).stdout
        reply = subprocess.run(argv,capture_output=True,text=True,timeout=5,env=env)
        report.update(returncode=reply.returncode,stdout=reply.stdout,stderr=reply.stderr)
        report['neighbor_after'] = subprocess.run(neighbor,capture_output=True,text=True,timeout=2,check=True).stdout
        after = lab.status()
        ap_after = lab.command('ap','STA '+address,'diagnostic_ap_after',allow_rejected=True)
        report.update(sta_after=after,ap_after=ap_after)
        match = re.search(r'(\d+) packets transmitted, (\d+) received',reply.stdout)
        if reply.returncode not in (0,1) or match is None:
            report.update(status='collection_error',reason='ping execution or summary parsing failed')
        else:
            sent, received = map(int,match.groups())
            stable = all(s.get('wpa_state')=='COMPLETED' for s in (before,after)) and all('[AUTHORIZED]' in s for s in (ap_before,ap_after))
            report.update(status='invalid_budget' if sent!=5 or received>sent else 'pass' if sent==received==5 and stable else 'fail',
                          packets_sent=sent,packets_received=received,
                          reachability='pass' if received>0 and stable else 'fail',
                          loss_fraction=1-received/sent if sent else None,
                          reply_sequences=[int(n) for n in re.findall(r'icmp_seq=(\d+).*?time[=<]',reply.stdout)])
    except (OSError,subprocess.SubprocessError) as exc:
        report.update(status='collection_error',reason=str(exc))
    report['finished_ns'] = time.monotonic_ns()
    return report


class DiagnosticMixin:
    def probe_data_plane(self):
        primary = super().probe_data_plane()
        # 所有基线和重连都执行同一五包预算；primary.status 始终保持原判定。
        primary['followup'] = followup(self)
        return primary
