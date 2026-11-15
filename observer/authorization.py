"""有界驱动确认轨迹的时序属性检查；不等于完整 WPA3 安全证明。"""
import json,re,hashlib
from pathlib import Path

PROPERTY='G(successful_authorize(peer) -> prior_successful_pairwise_key(peer) since last confirmed deletion)'


def monitor(rows):
    installed=False;last=None;authorized=0;violations=[];key_ref=None
    for row in rows:
        if last is not None and row['ns']<last:raise ValueError('nonmonotonic instrumentation')
        last=row['ns']
        if row['rc']!=0:continue
        if row['kind']=='key' and row.get('idx')==0:
            if row.get('alg')==0 or row.get('len')==0:installed=False;key_ref=None
            elif row.get('alg')==3 and row.get('len')==16:installed=True;key_ref=row['ref']
            else:raise ValueError('pairwise key outside CCMP-128 scope')
        elif row['kind']=='port':
            if row['authorized']==1:
                authorized+=1
                if not installed:violations.append({'authorization':row['ref'],'key_reference':key_ref,'ns':row['ns']})
    return {'status':'fail' if violations else 'pass' if authorized else 'insufficient_evidence','authorizations':authorized,'violations':violations}


def parse(text,role,peer):
    rows=[]
    for number,line in enumerate(text.splitlines(),1):
        m=re.search(r'RG_AUTH t=(\d+)\.(\d{9}) (.*)',line)
        if not m:continue
        fields=dict(re.findall(r'(\w+)=([^ ]+)',m.group(3)))
        if fields.get('peer')!=peer:continue
        row={'ns':int(m.group(1))*10**9+int(m.group(2)),'kind':fields['kind'],'rc':int(fields['rc']),'ref':f'{role}.log#line={number}'}
        for key in ('idx','alg','len','authorized'):
            if key in fields:row[key]=int(fields[key])
        rows.append(row)
    return rows


def verify(folder):
    folder=Path(folder);b=json.loads((folder/'connected_baseline.json').read_text())['sta'];roles={};hashes={}
    for name in ('connected_baseline.json','meta.json'):
        hashes[name]=hashlib.sha256((folder/name).read_bytes()).hexdigest()
    for role,peer in [('ap',b['address']),('sta',b['bssid'])]:
        p=folder/(role+'.log');text=p.read_text(errors='replace');hashes[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
        rows=parse(text,role,peer)
        try:r=monitor(rows)
        except ValueError as exc:r={'status':'insufficient_evidence','error':str(exc)}
        if 'CTRL-EVENT-TERMINATING' not in text and r['status']!='fail':r['status']='insufficient_evidence';r['reason']='no normal termination marker'
        r['trace']=rows;roles[role]=r
    provenance=None
    manifest_path=folder/'instrumentation-manifest.json'
    if manifest_path.exists():
        hashes[manifest_path.name]=hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        provenance=json.loads(manifest_path.read_text())
        meta=json.loads((folder/'meta.json').read_text())
        expected=provenance.get('binary_sha256',{})
        if (not isinstance(expected,dict) or set(expected)!={'ap','sta'} or
                any(not re.fullmatch('[0-9a-f]{64}',str(h)) for h in expected.values()) or
                expected != meta.get('binary_sha256') or
                not re.fullmatch('[0-9a-f]{64}',str(provenance.get('driver_source_sha256','')))):
            for r in roles.values():
                r['status']='insufficient_evidence'
                r['provenance_error']='instrumentation provenance incomplete or binary hashes do not match'
    else:
        for r in roles.values():
            r['status']='insufficient_evidence'
            r['provenance_error']='instrumentation manifest missing'
    statuses=[r['status'] for r in roles.values()]
    report={'property':PROPERTY,'status':'fail' if 'fail' in statuses else 'pass' if statuses==['pass','pass'] else 'insufficient_evidence',
            'roles':roles,'instrumentation':provenance,'input_sha256':hashes,'scope':'this finite driver-API trace, matching peers, cold baseline plus reconnect',
            'premises':['instrumented source covers the exercised key/authorization driver paths','complete unmodified process logs','successful driver operation represents the specified API effect'],
            'does_not_prove':['firmware internal key state','absence of every possible premature data delivery','unobserved executions or full protocol security']}
    (folder/'authorization-property.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));return report
