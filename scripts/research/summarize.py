"""汇总真实研究批次；运行时输入均为结果目录或 JSONL，不生成实验结果。"""
import argparse,json,statistics
from pathlib import Path
from collections import Counter


def summarize(cache_file, fault_files):
    s=json.loads(Path(cache_file).read_text());groups={}
    for policy in 'ABCD':
        rs=[r['result'] for r in s['runs'] if r['policy']==policy]
        samples=[r['timings_ms']['disconnect_observed_to_restored']['value'] for r in rs if r['recovery']=='pass']
        groups[policy]={'runs':len(rs),'recovered':sum(r['recovery']=='pass' for r in rs),'data_pass':sum(r['data_plane_recovery']=='pass' for r in rs),'auth_paths':dict(Counter(r['auth_path'] for r in rs)),'median_ms':statistics.median(samples) if samples else None}
    faults={}
    for f in fault_files:
        path=Path(f)
        rows=[json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        faults[path.stem]=[{'fault':r['fault'],'policy':r.get('policy'),'seed':r.get('seed'),'duration':r['duration'],'recovered':r['result']['recovery'],'execution':r['result']['execution_status'],'error':r['result']['error_category'],'output':r['output']} for r in rows]
    return {'cache':groups,'faults':faults}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('cache');p.add_argument('faults',nargs='*');a=p.parse_args();print(json.dumps(summarize(a.cache,a.faults),ensure_ascii=False,indent=2))
