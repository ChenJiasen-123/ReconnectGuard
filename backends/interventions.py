"""明确记录的缓存干预；只在客户端主动断开后执行，不重启进程。"""
import re


def entries(raw):
    return [line for line in raw.splitlines() if re.search(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', line)]


def apply_cache_policy(lab, policy):
    if policy not in 'ABCD' or len(policy)!=1:
        raise ValueError('cache policy must be A/B/C/D')
    if lab.status().get('wpa_state')!='DISCONNECTED':
        raise RuntimeError('缓存干预要求客户端已主动断开')
    report={'policy':policy,'before':{},'after':{},'operations':[]}
    for target in ('ap','sta'):
        raw=lab.command(target,'PMKSA','cache_before',allow_rejected=True)
        if raw.startswith('FAIL') or 'UNKNOWN COMMAND' in raw:
            raise RuntimeError('PMKSA query unsupported: '+target)
        report['before'][target]=raw
    lab.save('cache-intervention.json',report)
    for target, clear in [('ap',policy in 'BD'),('sta',policy in 'CD')]:
        if not entries(report['before'][target]):
            raise RuntimeError('基线缓存不存在，不能标作保留/清除对照: '+target)
        if clear:
            response=lab.command(target,'PMKSA_FLUSH','cache_flush',allow_rejected=True)
            report['operations'].append({'target':target,'command':'PMKSA_FLUSH','response':response})
            lab.save('cache-intervention.json',report)
            if response!='OK':
                raise RuntimeError('缓存清除未被接受: '+target)
        after=lab.command(target,'PMKSA','cache_after',allow_rejected=True)
        report['after'][target]=after
        lab.save('cache-intervention.json',report)
        if clear and entries(after) or not clear and entries(after)!=entries(report['before'][target]):
            raise RuntimeError('缓存干预效果未证实: '+target)
    if lab.status().get('wpa_state')!='DISCONNECTED':
        raise RuntimeError('缓存干预期间发生意外重连，该轮条件无效')
    report['verified']=True
    lab.save('cache-intervention.json',report)
    return report
