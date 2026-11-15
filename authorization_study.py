import argparse,json,os,hashlib,subprocess
from pathlib import Path
from backends.sessions import RealSession
from rejection import FaultLab
from runner import ROOT,load_case,run_case
from observer.authorization import verify


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--negative',action='store_true');p.add_argument('--repeat',type=int,default=2);a=p.parse_args()
    if not 1<=a.repeat<=20:p.error('repeat must be within 1..20')
    os.environ['RG_HOSTAPD']=str(ROOT/'work/hostap-auth/hostapd/hostapd')
    os.environ['RG_STOCK_WPA_SUPPLICANT']=str(ROOT/'work/hostap-auth/wpa_supplicant/wpa_supplicant')
    if a.negative:os.environ['RG_AUTH_EARLY']='1'
    else:os.environ.pop('RG_AUTH_EARLY',None)
    def session(out,scenario,client):return RealSession(out,client,lab_factory=FaultLab)
    for _ in range(a.repeat):
        r,out=run_case(load_case(ROOT/'cases.yaml','client_disconnect_reconnect'),backend='real',session_factory=session,evaluator_spec='observer.oracle:Oracle')
        manifest={'binary_sha256':{role:hashlib.sha256(Path(os.environ[name]).read_bytes()).hexdigest() for role,name in [('ap','RG_HOSTAPD'),('sta','RG_STOCK_WPA_SUPPLICANT')]},
                  'driver_source_sha256':hashlib.sha256((ROOT/'work/hostap-auth/src/drivers/driver_nl80211.c').read_bytes()).hexdigest(),
                  'verifier_source_sha256':hashlib.sha256((ROOT/'observer/authorization.py').read_bytes()).hexdigest(),
                  'negative_control':a.negative,'source_commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()}
        (out/'instrumentation-manifest.json').write_text(json.dumps(manifest,indent=2))
        report=verify(out)
        r['verified_properties']=[{'property':report['property'],'status':report['status'],'scope':report['scope'],'report':'authorization-property.json'}]
        (out/'result.json').write_text(json.dumps(r,ensure_ascii=False,indent=2))
        print(json.dumps({'negative':a.negative,'output':str(out),'execution_status':r['execution_status'],'property_status':report['status'],'roles':{k:{f:v.get(f) for f in ('status','authorizations','violations')} for k,v in report['roles'].items()}},ensure_ascii=False),flush=True)
        if r['cleanup_status']=='failed' or r['execution_status']!='completed' or report['status']!=('fail' if a.negative else 'pass'):
            raise SystemExit('Unexpected execution, cleanup, or property verdict; inspect result.json')
