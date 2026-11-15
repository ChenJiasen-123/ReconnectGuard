"""在新目录重新分析已有批次；保留旧报告、结果与全部原始输入。"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .postprocess import analyze_run


def review_batch(folder, output):
    folder, output = Path(folder).resolve(), Path(output).resolve()
    if output == folder or folder in output.parents:
        raise ValueError('new analysis must be outside original batch')
    output.mkdir(parents=True, exist_ok=False)
    summary = json.loads((folder/'summary.json').read_text())
    rows = []
    for entry in summary['runs']:
        source = folder/Path(entry['output']).name
        if source.resolve().parent != folder:
            raise ValueError('run outside batch')
        # 包含旧派生报告及结果文件，确认重分析没有改写它们。
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in source.iterdir() if p.is_file()}
        old = json.loads((source/'authentication-analysis.json').read_text())
        result = json.loads((source/'result.json').read_text())
        target = output/source.name/'authentication-analysis.json'
        new = analyze_run(source, output=target)
        for name, digest in before.items():
            if hashlib.sha256((source/name).read_bytes()).hexdigest() != digest:
                raise RuntimeError('original evidence changed: '+name)
        rows.append({'run_id':source.name, 'group':entry.get('policy', entry.get('case_id')),
                     'old_auth_path':old['auth_path']['value'], 'auth_path':new['auth_path']['value'],
                     'handshake_status':new['auth_path'].get('handshake',{}).get('status'),
                     'reason':new['auth_path']['reason'],
                     'connection_recovery':result.get('connection_recovery'),
                     'data_plane_recovery':result.get('data_plane_recovery'),
                     'report':str(target), 'original_report_sha256':before['authentication-analysis.json'],
                     'original_result_sha256':before['result.json'], 'original_files_unchanged':True})
    report = {'schema_version':2,'source_batch':str(folder),
              'source_summary_sha256':hashlib.sha256((folder/'summary.json').read_bytes()).hexdigest(),
              'runs':rows,'original_files_unchanged':all(r['original_files_unchanged'] for r in rows),
              'old_counts':dict(Counter(r['old_auth_path'] for r in rows)),
              'new_counts':dict(Counter(r['auth_path'] for r in rows)),
              'groups':{g:dict(Counter(r['auth_path'] for r in rows if r['group']==g)) for g in sorted({r['group'] for r in rows})},
              'handshake_counts':dict(Counter(r['handshake_status'] for r in rows)),
              'security_verdict':'not_evaluated'}
    (output/'reanalysis-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('batch',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=review_batch(args.batch,args.output)
    print(json.dumps({k:v for k,v in result.items() if k!='runs'},ensure_ascii=False,indent=2))
