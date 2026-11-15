"""汇总已记录批次。保留原始摘要哈希，不将多条失败序列计作多个缺陷。"""
import argparse
import hashlib
import json
from pathlib import Path

from sequence_study import metrics


def summarize(paths):
    groups = {}
    sources = []
    for path in paths:
        path = Path(path)
        batch = json.loads(path.read_text())
        mode = batch['mode']
        group = groups.setdefault(mode, {'batches': [], 'rows': []})
        group['batches'].append({'seed': batch.get('seed'), 'budget': batch['budget'],
                                 'max_depth': batch['max_depth'],'metrics':metrics(batch['runs'])})
        sources.append({'file': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'executed_source_sha256':batch.get('source_sha256')})
        for row in batch['runs']:
            compact = {key: row[key] for key in ('trial', 'plan', 'valid', 'output', 'error') if key in row}
            result = row.get('result')
            if result is not None:
                observation = result.get('independent_observation', {})
                compact['result'] = {
                    'run_id': result['run_id'], 'recovery': result['recovery'],
                    'failure': result['failure'], 'cleanup': result['cleanup'],
                    'elapsed_s': result['elapsed_s'], 'data_probes_pass': result['data_probes_pass'],
                    'operations': [{k: op[k] for k in ('action', 'started_s', 'finished_s') if k in op}
                                   for op in result['operations']],
                    'independent_observation': {key: observation.get(key) for key in
                                               ('stages', 'stage_denominator', 'effective_fault',
                                                'detected_recovery_failure', 'first_rejection')},
                }
                if result.get('observation_error'):compact['result']['observation_error']=result['observation_error']
                compact['result']['independent_observation']['rejection_reason_codes'] = sorted({
                    r['reason_code'] for r in observation.get('timeline', []) if r.get('reason_code') is not None})
            group['rows'].append(compact)
    for group in groups.values():
        group['metrics'] = metrics(group['rows'])
        # 每批独立重置引导状态；首次检出只在各批内部解释。
        group['metrics'].pop('first_detection')
    return {'sources': sources, 'groups': groups,
            'budget_unit': 'proposed sequences; maximum six operations per proposal',
            'failure_identity': 'one known cached-association rejection failure mode',
            'scope': 'bounded action grammar, one implementation and environment; not general superiority'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('summaries', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(summarize(args.summaries), ensure_ascii=False, indent=2) + '\n')
