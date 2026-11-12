# 江天豪：恢复动作语义与动作回执交付

日期：2026-11-12。真机实验在 Kali 实验机（hwsim）上完成，代码基线 `e17b457`。

本文对应 `要求.txt` 中江天豪部分，以及
[王奥鑫中期代码交接](wang-midterm-code-handoff.md) 第 54 行、
[阶段五交接](phase5-handoff.md) 第 47—55 行、
[中期复测](midterm-readiness-2026-11-10.md) 第 56—65 行列出的三项：
动作拒绝、恢复超时、重复调用的回执与事件记录；重复调用是否改变缓存、
是否触发额外自动连接、能否保持同一客户端进程；三种 PoC 复现与演示录屏。

## 交付边界

新增两个文件，未修改 `runner.py`、`comparison.py`、`model/`、
`backends/platform_executor.py`、`backends/platform_observer.py`：

| 文件 | 内容 |
|---|---|
| `backends/receipts.py` | 回执效果归属：把「命令被接受」与「动作效果已发生」分开，纯函数，可对已有结果目录事后补算 |
| `tests/test_reconnect_semantics.py` | 10 项回归：效果归属、拒绝路径、重复调用、状态门 |

`runner.py` 的集成点是一行调用，见第八节，由王奥鑫决定何时并入。

真实实验环境：`chen@100.97.21.95`（Tailscale 内网）上的 Kali，
工作目录 `/home/chen/Projects/rg-jiang-20261112`，由 `rg-phase6-EMsgsc`
复制而来，HEAD 为 `e17b457`。该目录的 `git fetch` 报
`.git/FETCH_HEAD: Permission denied`，因此本仓库新增的模块由 SFTP 上传后执行，
不是 `git pull` 得到的。这点影响的是代码版本口径，不影响实验结论：
`backends/receipts.py` 不参与实验执行，只读取产物。

## 一、回执契约：`accepted` 与 `effect_status` 是两件事

`ActionReceipt` 定义在 [model/contracts.py](../model/contracts.py) 第 19—33 行：

| 字段 | 写入者 | 含义 |
|---|---|---|
| `accepted` | `PlatformExecutor.execute` | 平台控制接口回了 `OK`；否则 `action_rejected`；`TimeoutError` → `action_timeout`；`OSError` → `control_unavailable` |
| `raw_response` | 同上 | 平台原始应答，拒绝时保留原文 |
| `run_id` / `attempt_id` / `target` / `command` | 同上 | 本轮与本次尝试编号，命令原文 |
| `started_at_utc` / `finished_at_utc` / `duration_ms` | 同上 | 发送耗时 |
| `effect_status` | **无人写入** | 默认值 `unverified` 一直到底 |

两条写入路径都不赋值：[platform_executor.py](../backends/platform_executor.py) 第 49—56 行
构造回执时不传该参数，[backends/mock.py](../backends/mock.py) 第 41 行只传前三个位置参数。
真机实证（`20261112-121108-0f072c/actions.jsonl`）：

```json
{"action": "client_disconnect", "accepted": true, "effect_status": "unverified", "attempt_id": 1, "raw_response": "OK", "duration_ms": 89.405, ...}
{"action": "client_reconnect",  "accepted": true, "effect_status": "unverified", "attempt_id": 2, "raw_response": "OK", "duration_ms": 1.007,  ...}
```

「每个恢复动作都能区分命令被接受和效果已发生」这条验收要求，
在本次之前只有语义约定（`platform_executor.py` 文件头），没有落到产物字段上。

## 二、`backends/receipts.py`：从事件补算效果归属

规则：

1. 回执按动作在列表中的出现顺序，与同类型请求事件一一配对：
   `client_disconnect` / `ap_deauth` / `ap_disassoc` ↔ `disconnect_requested`；
   `client_reconnect` ↔ `reconnect_requested`。配不上就保留 `unverified`。
2. 效果窗口是本请求之后、下一个请求之前。窗口内找对应效果事件：
   断开类动作找 `sta_disconnected`，重连找 `connection_restored`。
3. `accepted=false` 的回执直接记 `not_observed`，理由写 `命令未被接受：<error_category>`。

`effect_status` 三值：`observed`（本请求窗口内观测到效果事件）、
`not_observed`（命令被接受或拒绝，但窗口内没有效果事件）、
`unverified`（没有可配对的请求事件）。
`attribution` 三值：`same_attempt`、`attempt_mismatch`、`none`。
`same_attempt` 只表示效果事件的 `attempt_id` 与请求相同——这是 Observer
接收时记录的编号，不是协议尝试归属，与
[交接核对清单](wang-handoff-checklist.md) 第 37 行的口径一致。

```sh
python3 -m backends.receipts results/<run_id>
```

对已有结果目录事后补算，写 `actions-annotated.jsonl` 与
`actions-effect-report.json`，不需要 Lab、hwsim、sudo，也不需要重跑。
真机基线轮补算结果：

| 回执 | `accepted` | `effect_status` | 请求事件 | 效果事件 |
|---|---|---|---|---|
| `...:action:1` `client_disconnect` | true | `observed` | `raw:15:disconnect_requested` | `raw:17:sta_disconnected` |
| `...:action:2` `client_reconnect` | true | `observed` | `raw:19:reconnect_requested` | `raw:28:connection_restored` |

## 三、真机证据（2026-11-12，Kali + hwsim）

三轮实验，全部在 `/home/chen/Projects/rg-jiang-20261112` 下产生；
原始目录在服务器 `results/` 下，本机留有一份副本在 `work/evidence/`（均不入库）。

### 3.1 基线：Runner 真实轮 `20261112-121108-0f072c`

| 项 | 值 |
|---|---|
| 后端 / 模拟 | `real` / `false` |
| `execution_status` / `recovery` | `completed` / `pass` |
| `security_verdict` | `not_evaluated` |
| `client_process_unchanged` | `pass` |
| `reconnect_request_to_restored` | 11.524 ms（`raw:19` → `raw:28`） |
| `disconnect_observed_to_restored` | 17.735 ms（`raw:17` → `raw:28`） |
| `auth_path`（Runner） | `unknown` |

事后取证（`observer.frames`）：Association Request 携带 PMKID
`174e083fab3c4d41d645d845652f8b9d`（`trace.pcap#frame=59`），
四次握手 `frame=61/62/63/64`，首个受保护数据帧 `frame=65`；
重连窗口内无 SAE 认证帧，日志有 `PMKSA caching was used - skip EAPOL`，
判为 `pmksa_reuse`。Runner 的 `auth_path` 仍是 `unknown`，
说明这是离线结论，未回流结果字段。

`authorization_timing` 在帧级取证里仍是 `insufficient_evidence`
（缺 `key_install` / `port_authorized` 的帧级载体），与王梓权的结论一致。

### 3.2 E3 重复 RECONNECT：hold 轮 `20261112-121210-3dc87c`

hold 模式（`scripts/start.sh --client stock --probe connect --hold`）下人工发命令，
不经过 Runner，因此本轮没有 `actions.jsonl`；回执取自控制接口原文。

| 时刻 | 操作 | 回执 / 状态 | 缓存 | 进程 |
|---|---|---|---|---|
| T0 | 基线 | `wpa_state=COMPLETED` | 1 条，PMKID `f1c7dbf1…`，exp 43200 | PID 830231 |
| T1 | `DISCONNECT` | `OK` → `DISCONNECTED` | 条目保留，exp 43198 | 830231 |
| T2 | 第一次 `RECONNECT` | `OK` → `COMPLETED` | 同条目，exp 43198 | 830231 |
| T3 | 连发两次 `RECONNECT` | `OK`、`OK` → `COMPLETED` | 同条目，exp 43194 | 830231 |
| T4 | 再等 5 s | `COMPLETED`，无新事件 | 未变 | 830231 |

日志行号（`sta.log`）：初次关联 `855`/`877`，首次重连关联 `1289`/`1315`，
第一次 `RECONNECT` 命令 `1165`，连发两条命令 `1510`、`1512`，
两次关联都走 PMKSA 缓存 `935`、`1373`。
`1512` 之后没有任何 `Associated with` / `Trying to associate` / `CTRL-EVENT-CONNECTED`。

抓包：全程 4 个关联类帧、8 个 EAPOL 帧，即**两次关联**（初次 + 首次重连），
连发的两条 `RECONNECT` 没有产生第三次关联。

结论：stock wpa_supplicant 在已连接状态下接受 `RECONNECT` 并返回 `OK`，
但不产生新的关联、不改变 PMKSA 缓存条目、不重启客户端进程。
「命令被接受」与「产生新的连接动作」在这里明确分离。

### 3.3 E1 命令被接受但效果未发生：hold 轮 `20261112-121639-895333`

| 步骤 | 结果 |
|---|---|
| 基线 | `COMPLETED`，PMKID `44320f36…`，PID 833621 |
| `DISCONNECT` | `OK` → `DISCONNECTED` |
| 停掉 AP（`pkill hostapd`，12:16:47 -06:00） | `hostapd 已停止` |
| `RECONNECT` | 返回 `OK` |
| 之后 14 秒采样 | `AUTHENTICATING` → `SCANNING`（持续），从未回到 `COMPLETED` |

日志行号（`sta.log`）：`1351` 是 `Control interface command 'RECONNECT'`，
`1403` 是随后一次 `Trying to authenticate`；全程 `CTRL-EVENT-CONNECTED`
只有 1 次（`1044`，基线那次）。抓包只有 2 个关联类帧、4 个 EAPOL 帧
（即基线那一次关联），接受命令之后**没有任何关联帧**。

该轮 `error.json` 记录 `{"classification": "environment_error", "error": "WPA3 connection timeout"}`：
平台把这次失败归为环境错误，而不是动作拒绝。

观测窗为 14 秒而不是 20 秒：AP 进程消失后平台在 14 秒内自行清理并删除命名空间。
这一轮证明的是「命令被接受、效果没有发生」，不是「Runner 完整跑了 20 秒超时」。

## 四、重复 RECONNECT 的语义

| 调用路径 | 是否再发命令 | `attempt_id` | 依据 |
|---|---|---|---|
| Runner，状态为 `RECONNECTING` 时再来一次 | 不发，抛 `InvalidTransition` | 不变（自增在状态门之后） | [runner.py](../runner.py) 第 168—173 行；[model/efsm.py](../model/efsm.py) 第 45—48 行 |
| Runner，恢复超时后走重试 | 发，被允许的第二次请求 | +1 | [model/efsm.py](../model/efsm.py) 第 81—86 行；[runner.py](../runner.py) 第 208—219 行 |
| 直接调用 Executor 两次 | 发两次，两条回执都 `accepted` | 由调用方决定 | `tests/test_reconnect_semantics.py::test_executor_alone_does_not_deduplicate` |
| 平台层对已连接 STA 连发两次 RECONNECT | 发两次，都回 `OK` | 与 Runner 无关 | 第 3.2 节 |

状态门在 Runner，去重不在 Executor。平台侧的幂等性结论仅限 stock 客户端、
hwsim 与这三种动作：重复 `RECONNECT` 不产生新关联、不改缓存、不换进程。
Framework 客户端与实体网卡没有测。

## 五、允许重试的前置状态

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
| 该回执的 `effect_status` 必须为 `not_observed` | 效果已发生（`observed`）却状态未推进属于状态不一致，应停下而不是再发一次 |
| 客户端进程世代不变（`_continuity`，[runner.py](../runner.py) 第 95—106 行） | 恢复动作不得靠重启客户端实现 |
| 缓存只记观测 | 缓存条目存在不等于本次复用，也不能当作恢复成功 |

第三条是本次新增的语义：**重试的准入条件是「该动作确认没有产生效果」**。
只按超时判重试会漏掉「超时但效果已发生」的情形——那种情况下再发一次
RECONNECT 是对已恢复连接的重复动作。

## 六、复现步骤

### mock（任意平台，无需 sudo，Python 3.10+）

| 步骤 | 命令 | 预期 |
|---|---|---|
| 正常显式重连 | `python3 runner.py --backend mock` | `execution_status=completed`，两条回执 `observed` |
| AP 断开自动恢复 | `python3 runner.py --case ap_deauth --backend mock` | 同上；自动恢复的 `connection_restored` 不属于回执窗口 |
| 恢复超时后重试 | `python3 runner.py --cases docs/examples/retry-cases.json --case client_reconnect_retry_mock --backend mock --scenario reconnect_once_timeout` | `retries_used=1`，attempt 2 `fail`、attempt 3 `pass`，a2 为 `not_observed` |
| 预算耗尽 | 同上一行，`--scenario reconnect_timeout` | `recovery=fail`，状态终止于 `FAILED` |
| 回执归属 | `python3 -m backends.receipts results/<run_id>` | 打印 `accepted` / `effect` / `attribution` 与汇总 |

### 真实 hwsim（Kali，sudo）

```sh
cd /home/chen/Projects/rg-jiang-20261112
sudo python3 runner.py --case client_disconnect_reconnect --backend real --client stock
```

E1、E3 这两类失败注入不经过 Runner，用 hold 模式人工发命令：

```sh
sudo bash scripts/start.sh --client stock --probe connect --hold    # 终端 A，保持运行
sudo ip netns list                                                  # 终端 B，取 rg-sta-XXXX
sudo ip netns exec rg-sta-XXXX iw dev                               # 取接口名 rgstXXXX
sudo ip netns exec rg-sta-XXXX wpa_cli -i rgstXXXX status
sudo ip netns exec rg-sta-XXXX wpa_cli -i rgstXXXX disconnect
sudo ip netns exec rg-sta-XXXX wpa_cli -i rgstXXXX reconnect
sudo ip netns exec rg-sta-XXXX wpa_cli -i rgstXXXX pmksa
sudo bash scripts/stop.sh
```

E1 与 E3 的差别只在一个动作：断开后 `pkill -f "hostapd.*<后缀>"` 再发 `reconnect`。

hold 模式不写 `actions.jsonl`，因此这两轮的回执是控制接口原文加日志行号；
Runner 的 `ActionReceipt` 见第 3.1 节。

## 七、演示录屏镜头表

录制时长约 4—6 分钟，录屏加讲解：

| 镜头 | 操作 | 屏幕上应出现 | 讲解一句 |
|---|---|---|---|
| 1 | `git log --oneline -3`、`git status` | 当前提交与干净工作区 | 说明演示的是哪个提交 |
| 2 | `python3 runner.py --backend mock` | `recovery=pass`、两条回执 | 基线正常流程 |
| 3 | `python3 runner.py --case ap_deauth --backend mock` | 自动恢复 | AP 侧动作路径 |
| 4 | 恢复超时重试的 mock 命令 | `retries_used=1`，attempt 2 fail、3 pass | 首次失败保留 |
| 5 | 打开该轮 `actions.jsonl` | `accepted=true`，`effect_status=unverified` | 回执只说命令被接受 |
| 6 | `python3 -m backends.receipts results/<run_id>` | 回执 `effect=observed` 与事件引用 | 效果来自事件，不来自 `OK` |
| 7 | 打开 `recovery-attempts.json` | attempt 2 的失败记录 | 失败记录不因成功消失 |
| 8 | 真机：`sudo python3 runner.py --case ap_deauth --backend real --client stock --evaluator observer.oracle:Oracle` | 检查器判定与 `security_verdict=not_evaluated` | 检查结论与执行状态是两件事 |

镜头 8 需要实验机。前七段在任意装了 Python 的机器上可复现。

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
`checks`；`annotate` 输入为空时返回空列表。不接这一行也不影响现有功能，
`actions.jsonl` 保持恒为 `unverified`。

## 九、仍未取得的证据

| 项 | 状态 | 需要什么 |
|---|---|---|
| E2 动作拒绝的真实回执（`action_rejected`） | 未取得 | 需要 AP 侧拒绝场景：对未关联 STA 发 `DEAUTHENTICATE`，让控制面返回非 `OK`。hold 模式的 `wpa_cli` 不产生 `ActionReceipt`，Runner 案例结构也不允许插入该顺序。E1 轮证明的是「命令被接受」，`error.json` 记为 `environment_error`，不是动作拒绝 |
| E4 真实重试 | 未取得 | `runner.py` 第 80—81 行拒绝真实后端重试；需要王奥鑫按第五节条件放开后再跑 |
| 20 秒完整超时窗 | 未取得 | E1 的平台在 AP 消失后 14 秒自行清理，观测窗不足；需要在 AP 仍存活但拒绝关联的条件下重测 |
| `ap_deauth` / `ap_disassoc` 两路的重复调用 | 未测 | 本次只测了显式重连路径 |
| Framework 客户端与实体网卡 | 未测 | 结论仅覆盖 stock 客户端与 hwsim |

## 十、验证

- Windows（Python 3.12.4）：全量 96 项通过，4 项 Linux 专属跳过；新增 10 项全绿。
- Kali（Python 3.14.7）：全量 97 项通过，0 跳过；新增 10 项在实验机上单跑通过。
- 真机三轮：基线 Runner 轮、E3 hold 轮、E1 hold 轮，见第三节。
- 中文 locale 的 Windows 下需设 `PYTHONUTF8=1`，否则 3 项既有测试因默认文件编码失败；
  改动前 86 项在同样条件下就已出现。
- 原始产物在服务器 `results/` 与实验机 `/tmp`，本机副本在 `work/evidence/`，
  两者都被 Git 忽略。
