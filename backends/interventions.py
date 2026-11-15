"""明确记录的缓存干预；只在客户端主动断开后执行，不重启进程。"""
import re
import math
import time


def entries(raw):
    return [line for line in raw.splitlines() if re.search(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', line)]


def retention_check(before, after, elapsed_s):
    """比较缓存身份及有效性；允许查询间的整数秒倒计时，不允许延期或换条目。"""
    def parse(raw):
        values={}
        for line in entries(raw):
            match=re.fullmatch(r'\s*\d+\s+((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})\s+([0-9a-fA-F]{32})\s+(-?\d+)\s+([01])\s*',line)
            if not match:raise ValueError('unsupported PMKSA row')
            peer,pmkid,expiry,opportunistic=match.groups();key=(peer.lower(),pmkid.lower(),opportunistic)
            if key in values:raise ValueError('duplicate PMKSA identity')
            values[key]=int(expiry)
        return values
    try:
        a,b=parse(before),parse(after)
        allowance=math.ceil(elapsed_s)
        valid=bool(a) and a.keys()==b.keys() and all(b[key]>0 and 0<=a[key]-b[key]<=allowance for key in a)
        return {'verified':valid,'comparison':'peer + PMKID + opportunistic flag; index excluded',
                'elapsed_s':elapsed_s,'max_countdown_s':allowance,
                'expiry_before':list(a.values()),'expiry_after':list(b.values())}
    except ValueError as exc:return {'verified':False,'reason':str(exc)}


def apply_cache_policy(lab, policy):
    if policy not in 'ABCD' or len(policy)!=1:
        raise ValueError('cache policy must be A/B/C/D')
    if lab.status().get('wpa_state')!='DISCONNECTED':
        raise RuntimeError('缓存干预要求客户端已主动断开')
    report={'policy':policy,'before':{},'after':{},'operations':[],'retention_checks':{}}
    read_started={}
    for target in ('ap','sta'):
        read_started[target]=time.monotonic()
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
        retained=True
        if not clear:
            check=retention_check(report['before'][target],after,time.monotonic()-read_started[target])
            report['retention_checks'][target]=check
            lab.save('cache-intervention.json',report)
            retained=check['verified']
        if clear and entries(after) or not clear and not retained:
            raise RuntimeError('缓存干预效果未证实: '+target)
    if lab.status().get('wpa_state')!='DISCONNECTED':
        raise RuntimeError('缓存干预期间发生意外重连，该轮条件无效')
    report['verified']=True
    lab.save('cache-intervention.json',report)
    return report
