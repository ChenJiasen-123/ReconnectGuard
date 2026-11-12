"""证据接口与检查结果汇总。协议判定由外部 evaluator 提供。"""
from dataclasses import dataclass, field
from typing import Protocol

FIELDS = ('pmksa_presence', 'auth_path', 'key_state', 'port_authorized')
CHECKS = ('state_consistency', 'authorization_timing')


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    evidence_refs: list[str] = field(default_factory=list)
    reason: str = ''


class Evaluator(Protocol):
    def evaluate(self, *, context, snapshots, events) -> list[CheckResult]: ...


def record_snapshot(context, snapshot, index):
    """只保存观测历史；快照可能跨越自动重连，不推断其协议尝试归属。"""
    ref = f'snapshots.json#/{index}'
    context.observations.append({
        'evidence_ref': ref, 'checkpoint': snapshot.get('checkpoint'),
        'collected_during_attempt': context.attempt_id,
        'attempt_id': snapshot.get('attempt_id'),
        'values': {key: snapshot.get(key, 'unknown') for key in FIELDS}})
    # 缺失字段覆盖为 unknown，避免继承上一快照的已知值。
    for key in FIELDS:
        setattr(context, key, snapshot.get(key, 'unknown'))


def evaluate(evaluator, context, snapshots, events):
    checks = {name: CheckResult(name, 'not_evaluated', reason='未接入判定器') for name in CHECKS}
    if evaluator is None:
        return list(checks.values())
    valid_refs = {e.event_id for e in events}
    valid_refs.update(f'snapshots.json#/{i}' for i in range(len(snapshots)))
    seen = set()
    for result in evaluator.evaluate(context=context, snapshots=snapshots, events=events):
        if not isinstance(result, CheckResult) or result.name not in checks or result.name in seen:
            raise ValueError('检查名称无效或重复')
        if result.status not in {'pass', 'fail', 'insufficient_evidence', 'not_evaluated'}:
            raise ValueError('检查状态无效')
        if result.status in {'pass', 'fail'} and not result.evidence_refs:
            raise ValueError('pass/fail 必须包含证据引用')
        if any(ref not in valid_refs for ref in result.evidence_refs):
            raise ValueError('检查引用了不存在的证据')
        checks[result.name] = result
        seen.add(result.name)
    return list(checks.values())


def load_evaluator(spec):
    """加载用户明确指定的本地检查器工厂 module:factory，每轮创建新实例。"""
    import importlib
    if not spec:
        return None
    module, separator, name = spec.partition(':')
    if not separator or not module or not name.isidentifier():
        raise ValueError('检查器格式必须为 module:factory')
    try:
        factory = getattr(importlib.import_module(module), name)
        evaluator = factory()
    except Exception as exc:
        raise ValueError(f'无法加载检查器 {spec}: {exc}') from exc
    if not callable(getattr(evaluator, 'evaluate', None)):
        raise ValueError('检查器必须实现 evaluate')
    return evaluator
