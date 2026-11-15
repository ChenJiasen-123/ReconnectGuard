"""枚举事件抽象的可达状态和输入，核对时序监视器；不是SUT全局安全证明。"""
import itertools,json
from observer.authorization import monitor

ALPHABET=('install','delete','failed_install','authorize','revoke')


def row(symbol,index):
    v={'ns':index,'rc':0,'ref':str(index)}
    if symbol in ('install','delete','failed_install'):
        v.update(kind='key',idx=0,alg=0 if symbol=='delete' else 3,len=0 if symbol=='delete' else 16)
        if symbol=='failed_install':v['rc']=-1
    else:v.update(kind='port',authorized=int(symbol=='authorize'))
    return v


def certify():
    checked=0
    for length in range(6):
        for word in itertools.product(ALPHABET,repeat=length):
            bad=False
            for i,action in enumerate(word):
                keys=[s for s in word[:i] if s in ('install','delete')]
                if action=='authorize' and (not keys or keys[-1]!='install'):bad=True
            actual=monitor([row(s,i) for i,s in enumerate(word)])['status']
            expected='fail' if bad else 'pass' if 'authorize' in word else 'insufficient_evidence'
            if actual!=expected:raise AssertionError(word)
            checked+=1
    return {'alphabet':ALPHABET,'max_events':5,'traces_checked':checked,'monitor_matches_prefix_formula':True,'scope':'exhaustive bounded monitor conformance, not implementation-wide WPA3 proof'}


if __name__=='__main__':print(json.dumps(certify(),indent=2))
