"""对已有批次只读提取阶段时间；缺失/倒序区间保留 null。"""
import argparse,csv,json,io,statistics
from pathlib import Path
from observer.frames import read_pcap_frames,eapol_message


def run(folder):
    folder=Path(folder);s=json.loads((folder/'summary.json').read_text());rows=[]
    for item in s['runs']:
        p=Path(item['output']);a=json.loads((p/'authentication-analysis.json').read_text()) if (p/'authentication-analysis.json').exists() else None
        if not a:
            from observer.postprocess import analyze_run
            a=analyze_run(p)
        base=json.loads((p/'connected_baseline.json').read_text())['sta'];pair={base['bssid'],base['address']}
        start,end=a['window'].values()
        frames=[f for f in read_pcap_frames(p/'trace.pcap') if start is not None and end is not None and start<f['time']<=end and {f.get('addr1'),f.get('addr2')}==pair]
        first=min((f['time'] for f in frames if f.get('type')==0 and f.get('subtype') in (0,11)),default=None)
        references=set(a['auth_path']['frame_refs']);last=min((f['time'] for f in frames if eapol_message(f.get('eapol_key_info'))=='eapol_m4' and 'trace.pcap#frame='+str(f['frame']) in references),default=None)
        raw=[json.loads(x) for x in (p/'events.jsonl').read_text().splitlines()]
        from datetime import datetime
        offset=statistics.median(datetime.fromisoformat(r['received_at']).timestamp()-r['monotonic_s'] for r in raw)
        probe=json.loads((p/'after_reconnect-data-plane.json').read_text()) if (p/'after_reconnect-data-plane.json').exists() else {}
        data=probe.get('finished_ns',0)/1e9+offset if probe.get('status')=='pass' else None
        def diff(x,y):return round((y-x)*1000,3) if x is not None and y is not None and y>=x else None
        rows.append({'case':item.get('case_id',item.get('policy')),'run_id':p.name,'wait_to_auth_ms':diff(start,first),'auth_to_m4_ms':diff(first,last),'m4_to_connected_ms':diff(last,end),'connected_to_probe_finished_ms':diff(end,data),'note':'接收时间与pcap墙钟对齐，非精确空口时延'})
    (folder/'stage-timing.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    stream=io.StringIO();w=csv.DictWriter(stream,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows);(folder/'stage-timing.csv').write_text(stream.getvalue());return rows


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory');a=p.parse_args();print(json.dumps(run(a.directory),ensure_ascii=False))
