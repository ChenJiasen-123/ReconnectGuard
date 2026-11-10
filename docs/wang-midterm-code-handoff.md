# 王奥鑫：中期代码集成交付

日期：2026-11-10。基于 b2bb00b 的集成修改，随本文提交。验证时正式服务器 Git 目录保留 b2bb00b；本次真实回归使用独立目录。

## 已完成

- Runner 和 comparison 均支持 `--evaluator module:factory`。每轮加载新实例；结果记录检查器名称，批次计划记录加载配置。未指定时明确 not_evaluated。
- 工厂加载或接口错误发生在启动 Lab 之前。运行中的检查器异常保留恢复证据，结果报告 evaluation_error。既有证据引用校验继续生效。
- 案例支持可选 `recovery_policy: {"max_retries": 0}`。默认 0，允许整数 0—3；目前仅 mock 显式重连可以启用。
- 显式恢复超时可进入 FAILURE_HANDLING，再发起新 attempt_id 的重连。重试耗尽后进入 FAILED。首次失败保留在 `recovery-attempts.json` 和 result.recovery_attempts，最终成功不会删除它。
- 动作发送超时归为 action_timeout，采集超时归为 collection_error；两者不触发重试。断开超时、动作拒绝和状态错误也不触发重试。
- 重试前保留超时快照；快照失败立即结束。单轮关闭仍统一在 finally 中进行。
- attempts_ms 按尝试记录耗时；多次重连请求时，不生成含糊的顶层请求到恢复耗时。
- 标准 comparison 禁止重试配置，避免改变三组原有实验条件。

这不是“真实失败恢复全部完成”的声明。真实重试在环境创建前明确拒绝；重新认证子阶段尚未接入真实证据。现有 6 条流程覆盖统计不包含新失败恢复边，不能声称覆盖全部状态机边。

## 检查器交接：王梓权

交付一个可导入模块，其中工厂无参数，返回实现 evaluate 的实例：

```python
# 以下只说明接口，不是协议判定实现。
class Oracle:
    def evaluate(self, *, context, snapshots, events):
        # 返回 model.evaluation.CheckResult 列表。
        # 检查名为 state_consistency、authorization_timing。
        # 必须基于实际证据决定状态，不得固定返回 pass。
        raise NotImplementedError
```

模块交付后（假设名称为 observer.oracle，类为 Oracle）：

```sh
sudo python3 runner.py --case ap_deauth --backend real --evaluator observer.oracle:Oracle
sudo python3 comparison.py --backend real --repeat 10 --evaluator observer.oracle:Oracle
```

`observer.oracle` 当前不存在；上述命令是交接后的调用方式。普通命令不自动导入不存在的模块，也不把检查接口视为检查已完成。工厂应只创建检查器，不能启动环境或发无线动作。

判定状态：pass、fail、insufficient_evidence、not_evaluated。pass/fail 必须引用本轮 event_id 或 snapshots.json#/索引。失败检查不直接等于漏洞；execution_status 表示流程执行状态，检查结论应查看 checks，security_verdict 仍不自动判定。

## 可以立即运行的重试演示

```sh
python3 runner.py --cases docs/examples/retry-cases.json --case client_reconnect_retry_mock --backend mock --scenario reconnect_once_timeout
```

预期：attempt 2 恢复超时，attempt 3 成功，retries_used=1。此场景采用虚拟时钟，不执行无线动作。使用 reconnect_timeout 场景则两次都超时并终止。

## 尚需队友交付的真实依赖

- 王梓权：实际 Oracle；能够识别迟到事件与自动尝试的归属；认证阶段、密钥和授权状态的证据及有效时间范围。
- 江天豪：超时后再次 RECONNECT 的前置状态、幂等性和副作用；是否改变缓存、是否触发额外自动连接；动作拒绝和效果证据。
- 陈家森：验证上述行为在同一客户端进程内可靠发生，提供失败注入和清理记录。

依赖到位后，王奥鑫仍需把真实尝试边界接入重试入口，并将认证子阶段接入模型，再进行真实失败路径验收。当前不以收到时间或测试器的 attempt_id 冒充协议尝试归属，不为认证过程补造中间事件。

## 验证

- macOS：56 项测试，55 项通过、1 项 Linux 模块跳过。
- Kali：57 项测试全部通过。
- 新增测试覆盖超时后成功、预算耗尽、非法预算、拒绝真实重试、动作/采集失败不重试、迟到旧尝试、恢复后清理失败、检查器隔离和错误加载。
- 重试演示 CLI 已运行，保存首次失败和第二次成功。
- 独立服务器目录 `/home/chen/Projects/rg-wang-integration-20261110` 中运行三路径真实协议回归，各一次，3/3 完成并恢复，清理成功。
- 回归批次：`results/comparison-babc950d3b18467abde97569a221443b`（相对于上述服务器目录）。该目录是工作区源码快照，不是新的 Git 提交。
- 此次未重跑 30 轮；此前 30/30 结果对应 b2bb00b，不能记作本次修改的 30 轮验证。
