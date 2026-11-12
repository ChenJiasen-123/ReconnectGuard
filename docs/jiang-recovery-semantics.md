# 江天豪：恢复动作语义与动作回执交付

日期：2026-11-12。基线提交 `e17b457`。

本文对应 `要求.txt` 中江天豪部分，以及
[王奥鑫中期代码交接](wang-midterm-code-handoff.md) 第 54 行、
[阶段五交接](phase5-handoff.md) 第 47—55 行、
[中期复测](midterm-readiness-2026-11-10.md) 第 56—65 行列出的三项：
动作拒绝、恢复超时、重复调用的回执与事件记录；重复调用是否改变缓存、
是否触发额外自动连接、能否保持同一客户端进程；三种 PoC 复现与演示录屏。

## 交付边界

本次**新增两个文件**，未修改 `runner.py`、`comparison.py`、`model/`、
`backends/platform_executor.py`、`backends/platform_observer.py` 等队友模块：

| 文件 | 内容 |
|---|---|
| `backends/receipts.py` | 回执效果归属：把「命令被接受」与「动作效果已发生」分开，纯函数，可对已有结果目录事后补算 |
| `tests/test_reconnect_semantics.py` | 10 项回归：效果归属、拒绝路径、重复调用、状态门 |

`runner.py` 的集成点是一行调用，见本文第八节，由王奥鑫决定何时并入。

**未完成的部分必须写在前面**：真实 hwsim 上的 E1—E5（拒绝、超时、重复调用、
真实重试、缓存读数）本次**一条也没跑**。本机没有团队 Kali 服务器的账号，
本地 Kali 镜像是 Lab2 课程镜像，不含 `vendor/wifi-framework` 的编译依赖与补丁。
因此本文不出现任何真实 `run_id`、真实帧号或真实回执原文，
缺口逐条列在第七节，不填构造值、不把 mock 结果冒充真机结果。

已跑的是 mock 后端三条实验（虚拟时钟，不执行无线动作）与全量测试。
mock 结果能说明流程与回执归属的逻辑，不能说明协议行为。

## 一、回执契约：`accepted` 与 `effect_status` 是两件事

`ActionReceipt` 定义在 [model/contracts.py](../model/contracts.py) 第 19—33 行，
字段来源如下：

| 字段 | 写入者 | 含义 |
|---|---|---|
| `accepted` | `PlatformExecutor.execute` | 平台控制接口回了 `OK`；否则 `action_rejected`；`TimeoutError` → `action_timeout`；`OSError` → `control_unavailable` |
| `raw_response` | 同上 | 平台原始应答，拒绝时保留原文 |
| `run_id` / `attempt_id` / `target` / `command` | 同上 | 本轮与本次尝试编号，命令原文 |
| `started_at_utc` / `finished_at_utc` / `duration_ms` | 同上 | 发送耗时 |
| `effect_status` | **无人写入** | 默认值 `unverified` 一直到底 |

`effect_status` 这个字段存在，但两条写入路径都不赋值：
[platform_executor.py](../backends/platform_executor.py) 第 49—56 行构造回执时
不传该参数，[backends/mock.py](../backends/mock.py) 第 41 行只传前三个位置参数。
结果是 `actions.jsonl` 里每条回执的 `effect_status` 恒为 `unverified`。
实测：正常一轮（`results/a2c86f744ec3434781a387d38ba8a7fd`）两条回执均为
`unverified`，回归见 `tests/test_reconnect_semantics.py::test_actions_jsonl_keeps_effect_unverified`。

也就是说，「每个恢复动作都能区分命令被接受和效果已发生」这条验收要求，
在本次之前只有语义约定（`platform_executor.py` 文件头），没有落到产物字段上。

## 二、`backends/receipts.py`：从事件补算效果归属

规则：

1. 回执按动作在列表中的出现顺序，与同类型请求事件一一配对：
   `client_disconnect` / `ap_deauth` / `ap_disassoc` ↔ `disconnect_requested`；
   `client_reconnect` ↔ `reconnect_requested`。配不上就保留 `unverified`。
2. 效果窗口是本请求之后、下一个请求之前。窗口内找对应效果事件：
   断开类动作找 `sta_disconnected`，重连找 `connection_restored`。
3. `accepted=false` 的回执直接记 `not_observed`，理由写 `命令未被接受：<error_category>`，
   不因为它前后恰好有事件就记成生效。

`effect_status` 只有三值：

| 值 | 含义 |
|---|---|
| `observed` | 本回执自己的请求窗口内观测到对应效果事件 |
| `not_observed` | 命令被接受或拒绝，但窗口内没有对应效果事件 |
| `unverified` | 没有可配对的请求事件，归属不明 |

`attribution` 三值：`same_attempt`、`attempt_mismatch`、`none`。
`same_attempt` 只表示效果事件的 `attempt_id` 与请求相同——这是 Observer
接收时记录的编号，不是协议尝试归属，与
[交接核对清单](wang-handoff-checklist.md) 第 37 行的口径一致。

用法：

```sh
python3 -m backends.receipts results/<run_id>
```

对已有结果目录事后补算，写 `actions-annotated.jsonl` 与
`actions-effect-report.json`，不需要 Lab、hwsim、sudo，也不需要重跑。
已有真实批次（含 `actions.jsonl` 的）都能直接补算。

### mock 实测

| 实验 | `run_id`（本机，`results/` 已被忽略未入库） | 回执序列 |
|---|---|---|
| `reconnect_once_timeout`，预算 1 | `9eda5c596642480f852372711afc9dfb` | a1 `client_disconnect` attempt 1 `observed`（请求 e2 → 效果 e3）；a2 `client_reconnect` attempt 2 `not_observed`（请求 e4，窗口 e4—e5 内无效果）；a3 `client_reconnect` attempt 3 `observed`（e5 → e6） |
| `normal`，显式重连 | `a2c86f744ec3434781a387d38ba8a7fd` | 两条均 `observed` + `same_attempt` |
| `ap_deauth`，自动恢复 | `8607c13343fb441e834418f608b46500` | a1 `ap_deauth` `observed`，效果事件是 `sta_disconnected` e3；自动恢复的 `connection_restored` e4 带 attempt 2，不属于任何回执窗口 |

第一行就是「命令被接受但效果没有发生」的完整样本：a2 的 `accepted=true`，
同一轮结果里 `recovery_attempts` 为
`[{attempt_id: 2, outcome: fail, error_category: observation_timeout}, {attempt_id: 3, outcome: pass}]`，
`retries_used=1`，`security_verdict=not_evaluated`，转换覆盖里有
`RECONNECTING->FAILURE_HANDLING` 与 `FAILURE_HANDLING->RECONNECTING`。
最终成功没有删除第一次失败，也没有把它写成安全通过。

## 三、重复 RECONNECT 的语义

| 调用路径 | 是否再发命令 | `attempt_id` | 依据 |
|---|---|---|---|
| Runner，状态为 `RECONNECTING` 时再来一次 | 不发，抛 `InvalidTransition` | 不变（自增在状态门之后） | [runner.py](../runner.py) 第 168—173 行；[model/efsm.py](../model/efsm.py) 第 45—48 行 |
| Runner，恢复超时后走重试 | 发，这是被允许的第二次请求 | +1 | [model/efsm.py](../model/efsm.py) 第 81—86 行；[runner.py](../runner.py) 第 208—219 行 |
| 直接调用 Executor 两次 | 发两次，两条回执都 `accepted` | 由调用方决定 | `tests/test_reconnect_semantics.py::test_executor_alone_does_not_deduplicate` |
| 真实平台侧对同一 STA 连发两次 RECONNECT | 未知 | 未知 | 第七节 E3 |

结论：**状态门在 Runner，去重不在 Executor**。Runner 路径上重复调用不会产生
第二条命令；Executor 和平台是否幂等，本次没有证据，不作结论。
`prepare_retry` 只接受 `RECONNECTING` 且 `reconnect_mode="explicit"` 的状态，
自动恢复模式无法进入重试。

## 四、允许重试的前置状态

代码当前只允许这一组条件同时成立：

- 显式重连（`reconnect_mode="explicit"`）；
- mock 后端；真实后端在创建环境前就被拒绝（[runner.py](../runner.py) 第 80—81 行）；
- `max_retries` 为 0—3 的整数；
- 失败类别是恢复超时（`observation_timeout`），且发生在第 4 步、尚未恢复；
- 动作失败、采集失败、断开超时、状态错误、清理失败、归属不明都不重试
  （[runner.py](../runner.py) 第 208—226 行；`tests/test_retry_integration.py` 第 45—65 行）。

放开真实重试前，本文给出的补充判定：

| 判定 | 理由 |
|---|---|
| 上一条 attempt 已写入 `recovery-attempts.json` 且 `outcome=fail` | 没有失败记录的重试会把失败抹掉 |
| 当前状态必须是 `FAILURE_HANDLING`，不得存在未决 `RECONNECTING` | 同一时刻只能有一个进行中的尝试 |
| 该回执的 `effect_status` 必须为 `not_observed` | 效果已发生（`observed`）却状态未推进，属于状态不一致，应停下而不是再发一次 |
| 客户端进程世代不变（`_continuity`，[runner.py](../runner.py) 第 95—106 行） | 恢复动作不得靠重启客户端实现 |
| 缓存只记观测 | 缓存条目存在不等于本次复用，也不能当作恢复成功 |

第三条是本次新增的语义：**重试的准入条件是「该动作确认没有产生效果」**。
只用超时时间来判会漏掉「超时但效果已发生」的情形——那种情况下再发一次
RECONNECT 是对已恢复连接的重复动作。

## 五、三种 PoC 复现步骤

### mock（任意平台，无需 sudo，Python 3.10+）

| 步骤 | 命令 | 预期 |
|---|---|---|
| 正常显式重连 | `python3 runner.py --backend mock` | `execution_status=completed`，`recovery=pass`，两条回执 `observed` |
| AP 断开自动恢复 | `python3 runner.py --case ap_deauth --backend mock` | 同上；第三次尝试的 `connection_restored` 不属于回执窗口 |
| 恢复超时后重试 | `python3 runner.py --cases docs/examples/retry-cases.json --case client_reconnect_retry_mock --backend mock --scenario reconnect_once_timeout` | `retries_used=1`，attempt 2 `fail`、attempt 3 `pass`，a2 为 `not_observed` |
| 预算耗尽 | 同上一行，`--scenario reconnect_timeout` | `recovery=fail`，状态终止于 `FAILED` |
| 回执归属 | `python3 -m backends.receipts results/<run_id>` | 打印每条回执的 `accepted` / `effect` / `attribution` 与汇总 |

退出码：失败场景为 1，正常场景为 0。

### 真实 hwsim（Kali，sudo）

```sh
sudo python3 runner.py --backend real --client stock
sudo python3 runner.py --case ap_deauth --backend real --client stock
sudo python3 runner.py --case ap_disassoc --backend real --client stock
sudo python3 runner.py --case ap_deauth --backend real --client stock --evaluator observer.oracle:Oracle
```

前置依赖与已部署目录见 [中期复测](midterm-readiness-2026-11-10.md) 第 17 行：
独立新部署需准备 `vendor/wifi-framework`（含补丁）与 `vendor/wpa_supplicant-2.10`
的编译依赖，只 clone 仓库不能直接运行。这三条命令本次未在本机执行。

## 六、演示录屏镜头表

录制时长约 4—6 分钟，录屏加讲解，按顺序拍：

| 镜头 | 操作 | 屏幕上应出现 | 讲解一句 |
|---|---|---|---|
| 1 | `git log --oneline -3`、`git status` | 当前提交与干净工作区 | 说明演示的是哪个提交，避免与旧批次混淆 |
| 2 | `python3 runner.py --backend mock` | `recovery=pass`、两条回执 | 基线正常流程 |
| 3 | `python3 runner.py --case ap_deauth --backend mock` | 同上，自动恢复 | AP 侧动作路径 |
| 4 | `python3 runner.py --cases docs/examples/retry-cases.json --case client_reconnect_retry_mock --backend mock --scenario reconnect_once_timeout` | `retries_used=1`，attempt 2 fail、3 pass | 首次失败保留，第二次成功不覆盖它 |
| 5 | 打开该轮 `actions.jsonl` 与 `result.json` | `accepted=true` 与 `effect_status=unverified` | 回执只说命令被接受，不说效果 |
| 6 | `python3 -m backends.receipts results/<run_id>` | a2 `accepted=True effect=not_observed` | 效果归属来自事件，不来自 `OK` |
| 7 | 打开 `recovery-attempts.json` | attempt 2 的失败记录 | 失败记录不因最终成功而消失 |
| 8 | （有真实环境时）`sudo python3 runner.py --case ap_deauth --backend real --client stock --evaluator observer.oracle:Oracle` | `state_consistency` 判定与 `security_verdict=not_evaluated` | 检查器结论与流程执行状态是两件事 |

镜头 8 需要真机，本次没有。前七段在任意装了 Python 的机器上可复现。

## 七、证据缺口

| 用例 | 需要什么 | 现在缺什么 | 产物将落在 |
|---|---|---|---|
| E1 命令被接受但效果未发生（真机） | Kali hwsim，断开后阻止重连（停 AP 或改密钥） | 环境；预期产物：`actions.jsonl` 中 `accepted=true`、`result.recovery=fail`、`error_category=observation_timeout`、抓包无成功关联 | `results/<run_id>/`，被 Git 忽略，留在服务器 |
| E2 动作拒绝（真机） | Framework AP 对未关联 STA 发 `DEAUTHENTICATE` | 环境；预期 `error_category=action_rejected` 且不触发重试 | 同上 |
| E3 重复 RECONNECT（真机） | 同一进程内连发两次，前后读 `wpa_cli pmksa` | 环境；需要关联帧数、PMKSA 条目数、`process_epoch` 三项前后对比 | 同上 |
| E4 真实重试 | E1/ E3 的结论 + 王奥鑫放开 `runner.py:80` | 两侧都未到位 | `recovery-attempts.json` |
| E5 缓存三时点读数（真机） | 断开前、断开后、恢复后各读一次 | 环境；本次只观测不判定 | `snapshots.json` |

mock 侧的 E1、E2、E3 已有确定性覆盖（第二节、第三节及对应测试），
但这只能证明软件流程，不能替代真机证据。

## 八、给王奥鑫的集成点

`runner.py` 第 291 行写 `actions.jsonl` 之前，把回执换成补算后的版本：

```python
from backends.receipts import annotate
...
receipts, _ = annotate(executor.receipts, session.events(), context.run_id)
for name, rows in {event_file: [asdict(e) for e in session.events()],
                   "actions.jsonl": [asdict(r) for r in receipts],
                   "transitions.jsonl": machine.transitions}.items():
```

只改 `effect_status`，不改 `execution_status`、`recovery`、`security_verdict`、
`checks` 中的任何判定；也不需要改 `evaluate` 的入参。
`annotate` 是纯函数，输入为空时返回空列表，不会抛异常。
不接这一行也不影响现有功能，`actions.jsonl` 保持恒为 `unverified`。

## 九、验证

- 新增 10 项测试全通过；全量 `discover` 96 项通过、4 项 Linux 专属跳过
  （Windows、Python 3.12.4）。新增测试覆盖：效果归属三态、同 attempt 与
  跨 attempt、缺请求事件、拒绝路径端到端、重复 RECONNECT 的状态门、
  Executor 无去重、CLI 产物。
- 中文 locale 的 Windows 下必须设 `PYTHONUTF8=1`（或 `-X utf8`），
  否则有 3 项既有测试因读文件默认编码失败。这是本机环境问题，
  与本次改动无关，改动前 86 项在同样条件下就已出现。
- 本机 mock 三条实验按第五节命令跑通，产物在 `results/` 下，未入库。
