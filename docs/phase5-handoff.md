# 阶段五接口与分工交接

日期：2026-11-02。本文更新阶段五进度；不是全部阶段五验收通过声明。

## 王奥鑫本次完成

- Runner 在接受恢复证据时立即记录恢复成功。后续快照、清理、检查失败不抹掉该事实；整轮执行仍报告失败。
- 只有恢复步骤的观测超时才报告 recovery=fail。采集失败、动作失败等保留 not_evaluated，不自动归因到客户端恢复能力。
- Context.observations 保存每个快照的字段及 snapshots.json 引用。缺失字段覆盖为 unknown，避免沿用旧值。
- collected_during_attempt 只是采集时测试器所在尝试；快照未声明 attempt_id 时保留 null，不伪造协议归属。
- 增加 model/evaluation.py 中的 Evaluator 和 CheckResult。run_case(..., evaluator=实例) 可以接入检查器。默认不执行安全判定。
- 检查器异常保留恢复结果和证据，并报告 evaluation_error。pass/fail 必须引用本轮存在的事件或快照；只验证引用存在，不替代证据内容审查。
- attempts_ms 按 attempt_id 统计显式请求到恢复耗时。自动恢复缺少显式请求时保留 null。顶层同名事件有多个时不再随意取最后一项配对。
- summary.json 每组增加 auth_path_counts、check_counts；CSV 保留原有概要字段。

## 王梓权：Observer 与 Oracle 交付任务

请提供以下能力与对应测试样例：

1. 连接尝试归属：明确自动重试、迟到事件、无编号事件如何处理。无法确认时保留未知；不能以接收时当前编号证明协议归属。
2. 安全上下文：缓存存在、认证路径、密钥和端口状态的观测依据；各值的时间或有效范围。现有 pmksa_presence、auth_path 等字段继续使用。
3. 认证路径：能证明时分类完整 SAE / 缓存复用；不能证明则 unknown。不要只看 PMKSA 列表。
4. 检查器：实现 evaluate(*, context, snapshots, events)，返回 CheckResult 列表。

首批支持的检查名：state_consistency、authorization_timing。
状态：pass、fail、insufficient_evidence、not_evaluated。
证据引用：events 中的 event_id，或 snapshots.json#/0 这类本轮快照索引。

示例（仅演示格式，不是已实现的判定逻辑）：

```python
from model.evaluation import CheckResult

class Oracle:
    def evaluate(self, *, context, snapshots, events):
        return [CheckResult(
            name="authorization_timing",
            status="insufficient_evidence",
            reason="未获得本次尝试的密钥确认与授权时序证据",
        )]
```

不要将快照显示 AP 已授权直接判为授权时序 pass。失败快照也可能位于恢复之后，需检查时间和上下文。security_verdict 仍为 not_evaluated；单个检查 fail 是待核查结果，不自动等同漏洞。

交付验收：正常、双端短暂不同步、持续不同步、证据缺失、迟到旧事件各有样例；同一输入产生可解释结果。

## 江天豪：动作与恢复策略所需证据

现有三种动作继续复用，不重写 Executor。请补充：

- 对动作拒绝、连接拒绝和恢复超时的实际表现及回执样例。
- 如果计划重试，说明动作的前置状态、是否允许重复、是否触发自动连接、是否改变缓存。
- 提供原进程内恢复的验证；重启客户端与清空缓存不能作为未声明的正常恢复手段。

交付验收：每个恢复动作都能区分命令被接受和效果已发生，动作记录包含本轮与本次尝试编号。

## 陈家森：平台验证任务

- 在 Linux hwsim 环境运行最新回归与三路径批次；本机 macOS 不能替代该验证。
- 核对进程世代、运行配置证据、抓包收尾、失败清理。
- 如改用实体网卡，另行提供物理接口生命周期适配；当前 real 仍指真实协议程序运行在 hwsim 上。

## 王奥鑫剩余的集成工作及依赖

有界失败恢复仍归王奥鑫负责，但本次不启用真实重试。需要先拿到 Observer 的尝试边界和 Executor 的恢复动作语义。否则重试会改变实验输入，并可能错误关联迟到事件。

这些依赖到位后：定义案例中的重试预算与适用错误类别；增加 FAILURE_HANDLING 等状态及重试边；分配新 attempt_id；保留第一次失败，不以最终恢复覆盖它；用尝试级指标统计每次耗时。对进程退出、清理失败、归属不明和采集失败不盲目重试。

当前上下文用于事实记录，不驱动缓存分支决策。认证子阶段及上下文守卫要等观测字段及判定规则确认后再接入。新接口为草案，需团队核对；没有宣称其他成员已经接受。

## 验证

本轮本地测试：45 项通过，1 项 Linux 专属测试跳过。新增覆盖恢复后快照失败（显式及自动模式）、旧上下文失效、证据引用校验、判定器异常、跨尝试耗时隔离。未运行真实 Wi-Fi 测试。

## 2026-11-10 更新

Runner / comparison 已增加检查器命令行接入；mock 已支持默认关闭的有界恢复及尝试历史。真实尝试边界和动作语义依赖仍未交付，真实重试仍不启用。最新代码与验收说明见 [王奥鑫中期代码交接](wang-midterm-code-handoff.md)。
