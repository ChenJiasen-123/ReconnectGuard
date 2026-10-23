# 王梓权：真实 Observer 适配器

> 更新（2026-10-23）：本次已增加统一 Runner 真实接入。下文原有“尚未接入”等描述保留为历史背景；当前接口及接入步骤见 [组员接口说明](platform-api.md)。

本适配器将 `Lab` 的原始控制事件和快照转换为 `model/contracts.py` 定义的 `Event` 与结构化快照。它只表达已观测事实，不执行无线动作，不代替 EFSM 或安全 Oracle。

## 责任边界

| 职责 | 实现 |
|---|---|
| 原始事件唯一序号 | `rg_platform/journal.py` 的 `EventJournal` |
| 控制 socket 消费 | 仍由 `Lab.listen()` 唯一负责 |
| 多读者游标 | Observer 从 journal 读取，不再 `ATTACH` socket |
| 事件标准化 | `backends/platform_observer.py` |
| 原始快照 | `Lab.snapshot()`，保留控制命令原文 |
| 标准化快照 | `PlatformObserver.snapshot()` |
| 动作与回执 | 不属于 Observer，后续由 Executor 实现 |
| 安全判定 | 不属于 Observer，当前仍为 `not_evaluated` |

`Lab.record_event()` 是单轮事件序号的唯一入口。当前 AP/STA 监听器使用它；后续 Runner 记录请求事件时也应调用该入口，使请求与观测共用同一 `seq` 空间。

## 事件映射

| 原始证据 | 标准 `kind` | 来源 | 补充证据 |
|---|---|---|---|
| `CTRL-EVENT-CONNECTED` | `baseline_confirmed` | sta | 当前 STATUS 的 COMPLETED / SAE / CCMP / PMF |
| `CTRL-EVENT-DISCONNECTED` | `sta_disconnected` | sta | 断开原文和证据引用 |
| `CTRL-EVENT-CONNECTED` | `connection_restored` | sta | 当前 STATUS 的 COMPLETED / SAE / CCMP / PMF |

AP 事件会保留在原始 journal，但不能单独完成上述三个 STA 转换。连接事件必须再读取 STATUS；控制命令的 `OK` 不会生成连接或断开证据。

## 游标、归属与去重

1. `mark()` 返回当前最大原始事件序号。
2. `wait_event(..., after=cursor)` 只接受 `seq > cursor` 的证据。
3. journal 已标注 `attempt_id` 时必须与 Context 一致；未标注时，只能依据“动作游标之后收到”归属，并在 `attempt_attribution` 中明示。
4. 同一 run / attempt / source / kind 只交付一次；后续同义事件记为 `semantic_duplicate`。
5. 错误来源、错误 attempt、噪声和重复候选保存到 `observer-observations.jsonl`。

标准事件示例：

```json
{
  "event_id": "20261023-120000-abcdef:raw:14:connection_restored",
  "run_id": "20261023-120000-abcdef",
  "attempt_id": 2,
  "source": "sta",
  "kind": "connection_restored",
  "seq": 14,
  "monotonic_s": 1234.5,
  "data": {
    "wpa_state": "COMPLETED",
    "key_mgmt": "SAE",
    "pairwise_cipher": "CCMP",
    "pmf": "2",
    "evidence_ref": "events.jsonl#seq=14",
    "attempt_attribution": "received_after_cursor"
  }
}
```

## 快照规则

Observer 同时保存 `<checkpoint>.json` 原始快照和 `<checkpoint>-observer.json` 标准快照。

| 字段 | 判定规则 |
|---|---|
| `ap_state` | AP flags 含 AUTHORIZED 时为 `authorized`；查询可用但未授权时为 `observed_not_authorized`；查询失败为 `unknown` |
| `pmksa_presence` | PMKSA 表存在数据行为 `present`，只有表头为 `absent`，命令失败为 `unknown` |
| `port_authorized` | 只依据 AP `AUTHORIZED` flag；不从 STA `COMPLETED` 推测 |
| `process_epoch` | 保存 Lab 创建的角色、PID 和进程世代 |
| `auth_path` | 当前没有足够证据，保留 `unknown` |
| `key_state` | 当前没有密钥安装时序观测，保留 `unknown` |

## 输出文件

| 文件 | 内容 |
|---|---|
| `events.jsonl` | AP、STA 及后续 Runner 共用的原始序号日志 |
| `ap-events.jsonl` / `sta-events.jsonl` | 兼容现有平台的分角色原始日志 |
| `observer-events.jsonl` | 已交付的标准 `Event` |
| `observer-observations.jsonl` | 未采用候选及原因 |
| `*-observer.json` | 标准快照，通过 `raw_snapshot_ref` 指回原始快照 |
| `observer-check.json` | 真实 Observer smoke 汇总，不是漏洞结论 |

2026-10-23 的真实 hwsim smoke 产物已保存为 [标准事件样例](examples/real-observer-event.json) 和 [标准快照样例](examples/real-observer-snapshot.json)。样例保留原始 run ID、虚拟 AP/STA MAC 和进程证据，不包含真实无线网络数据。

## 与现有代码的对齐状态

| 组件 | 当前对齐状态 | 集成时的含义 |
|---|---|---|
| `model.contracts.Observer` | 已实现全部五个方法 | Runner 可通过 Protocol 注入，不应导入 Observer 内部方法 |
| `model.contracts.Event` | 字段完全对齐 | `data` 中新增的原始证据为附加字段 |
| `model.efsm.EFSM` | 三个观测 kind、source 和连接 profile 已兼容 | 不需要分叉或复制一份真实 EFSM |
| `rg_platform.Lab` | 监听器已接入共享 journal | 禁止再建第二个 AP/STA `ATTACH` 消费者 |
| `backends.MockBackend` | 原有测试保持不变 | mock 仍同时承担 Executor/Observer，仅作回归基线 |
| `runner.py` | 尚未对齐 | 仍硬编码 `MockBackend`，且自己生成 run ID 和结果目录 |
| `cases.yaml` | 尚未对齐 | 校验器仍只接受 `backend=mock` 和固定五步案例 |
| `ActionReceipt` | 仅有最小字段 | 真实 Executor 还需保存 run、attempt、target、原始回应、时间和错误类别 |

Observer 可以单独交付和验证，但“真实 Runner 已完成”必须以 `runner.py` 可注入 Lab、Executor 和 Observer 为准，不能以 `observer-reconnect` smoke 代替。

## 真实 Runner 必须遵守的时序

推荐由 Runner 拥有整轮生命周期，并使用下列顺序：

1. 创建 `Lab("stock")`，直接复用 `lab.run_id` 和 `lab.out`，不得再创建第二个 run ID 或结果目录。
2. 创建 Executor 和 `PlatformObserver(lab)`，先调用 `observer.start()`，再调用 `lab.start()`。Lab 的 journal 在初始连接动作前就必须可用。
3. `lab.wait_connected()` 后，使用 `wait_event("baseline_confirmed", after=0, ...)` 取得基线事件，不另行发起初始连接。
4. 每个动作前先 `mark()`；Runner 的请求记录器通过 `lab.record_event("runner", ..., attempt_id)` 取得同一序号空间中的请求 `seq`。
5. 先将请求事件交给 EFSM，再调用 Executor。等待动作效果时建议以请求 `seq` 作为 exclusive cursor；执行命令必须发生在请求记录之后。
6. 显式重连时，先将 `context.attempt_id` 增加到 2，再记录 `reconnect_requested`、执行动作和等待恢复。
7. 命令回执只判断动作是否被接受；只有 Observer 事件能推进到 `DISCONNECTED` 或 `RECONNECTED`。
8. 成功、超时、进程退出和保存失败均必须先尽力保存失败快照，再 `observer.close()`，最后在 `finally` 中 `lab.stop()`。

自动重连不应强行生成 `reconnect_requested`。它需要单独案例和 EFSM 分支，否则会把被动恢复错记为测试器显式请求。

## 已知边界和对齐风险

| 边界 / 风险 | 当前行为 | 后续处理 |
|---|---|---|
| 接收顺序不等于空口顺序 | `seq` 是本机两个监听线程进入 journal 的顺序 | 涉及协议时序的 Oracle 必须同时使用 pcap，不得只比较 `seq` |
| 无法精确归属的原始事件 | AP/STA 监听记录的 `attempt_id` 为 `null` | Observer 只在 cursor 之后做接收窗口归属；不得将所有迟到事件盲目标为当前 attempt |
| 迟到的旧事件 | 如果它在新 cursor 后才被收到，单凭控制通道无法完全排除 | 后续结合 BSSID、断开 reason、pcap 时间和动作相关 ID；证据不足时保留 unknown |
| 连接事件与 STATUS 不原子 | Observer 收到 CONNECTED 后另行读 STATUS | 保留两份时间证据；状态已变化时当作候选无效，不补造 profile |
| 读操作混入 `meta.actions` | `Lab.status()` 和快照查询也经过 `Lab.command()` | Executor 接入时按 `semantic`/`target` 区分读取和动作；后续可将 read receipts 拆分为独立证据集 |
| 语义去重可能过宽 | 同 run/attempt/source/kind 只交付一次 | 连接抖动、多次断开或重试需引入 connection generation，不得仅删除去重 |
| 多个 Observer 实例 | journal 支持多读者，但它们会同时追加相同输出文件 | 每轮只允许一个负责产物的 Observer；调试读者不得写同名文件 |
| 只覆盖三个标准 kind | AP 连接/断开仅保留原文 | 扩展 AP 事件时先与 EFSM 和 Oracle 共同确定 kind/source，不在各模块自定名称 |
| profile 值严格匹配 | EFSM 要求字符串 `SAE` / `CCMP` / `2` | 更换 supplicant 版本后先保留原值，再在统一映射层处理别名，禁止在 EFSM 前无记录地改写 |
| PMKSA 存在不等于被使用 | 快照只输出 present/absent/unknown | Oracle 判断缓存路径必须增加 SAE/PMKID/pcap 证据 |
| `AUTHORIZED` 不证明授权时序正确 | 快照只表示读取时 AP flag | 授权 Oracle 需要密钥安装、EAPOL 和数据帧时序 |
| 进程世代是平台启动记录 | 当前 epoch 为进程启动 UTC 字符串 | 需抵抗 PID 复用时，应增加 `/proc/<pid>/stat` starttime |
| 错误类别尚未细分 | STATUS 补全失败会被记录并最终表现为 timeout | Runner 应区分 observation timeout、collection error、process exit、artifact write error 和 cleanup error |
| 快照未进入事件 `seq` | 快照使用读取开始/结束墙钟 | 需严格排序时，Runner 应为 checkpoint 记录带 seq 的请求/完成事件 |
| root 结果目录 | sudo 运行产物默认属于 root | 后处理使用 sudo，或由平台在已验证 `SUDO_UID/GID` 后统一转移所有权；不做宽泛 `chmod 777` |

## 其他成员后续设计注意事项

### Runner / EFSM 负责人

- 将 `run_case()` 重构为接收 lifecycle、Executor、Observer 和请求记录器，不通过 `isinstance` 分支 mock/真实逻辑。
- 真实模式的 `run_id` 和输出目录由 Lab 唯一创建；mock 仍可由 Runner 创建。
- 请求事件必须进入 `Lab.record_event()` 的序号空间，同时转换成 `source=runner` 的 model `Event`。
- 不要把 `observer-observations.jsonl` 中的噪声交给 EFSM；只有 `wait_event()` 返回值可以推进模型。
- 不要从最终快照倒推中间状态；缺失事件应是 timeout/insufficient evidence。
- 失败边应保留原始 Observer 产物，不要将一轮真实失败重跑后覆盖。

### Executor 负责人

- Executor 只封装 `Lab.command()` 并返回回执；禁止直接调用 Observer 或修改 EFSM state。
- `OK` 只能是 `accepted=true, effect_status=unverified`，不能标记断开/恢复成功。
- 回执应补充 run ID、attempt ID、target、semantic、raw response、单调时间、墙钟和错误分类，同时保持现有字段兼容。
- 动作执行前由 Runner 记录请求序号；Executor 不应再分配另一套 `seq`。
- 命令拒绝、socket 超时、目标进程退出和环境错误必须是不同错误类别。

### Lab / 平台负责人

- `Lab.listen()` 仍是控制事件的唯一 socket 消费者。新观测功能应读 journal，不直接 `ATTACH`。
- 所有新事件生产者必须通过 `record_event()`，不直接 append `lab.events` 或自行分配 `seq`。
- 保留 `lab.events` 三元组只为兼容现有 probes；新代码不应将它作为标准接口。
- 修改原始日志格式时必须保持 `events.jsonl` 与 evidence reference 可回溯，不得重排或复用序号。
- 停止顺序为先结束上层等待和 Observer，再停监听线程；否则尾部事件可能丢失。

### Oracle / 案例负责人

- Oracle 使用标准事件、标准快照和原始证据引用，但不修改 Observer 产物。判定结果应单独保存。
- `pmksa_presence=present` 只能证明表中有记录；`port_authorized=authorized` 只能证明快照时 AP flag 存在。
- 恢复超时、证据不足、环境失败和候选安全异常必须分开；不得将 `unknown` 自动判为 fail 或 pass。
- AP Deauth、Disassoc、显式重连和自动重连应是不同案例，分别设置 `disconnect_method` 和 `reconnect_mode`。

## 错误分类建议

| 类别 | 典型来源 | 是否可直接解释为安全异常 |
|---|---|---|
| `action_rejected` | Executor 收到 FAIL / UNKNOWN COMMAND | 否 |
| `observation_timeout` | 期限内无匹配标准事件 | 否，先视为恢复/观测失败 |
| `collection_error` | STATUS、socket、pcap 或日志采集失败 | 否 |
| `process_exit` | AP/STA/capture 意外退出 | 否，环境或实现错误 |
| `artifact_write_error` | JSON/JSONL/pcap 保存失败 | 否，本轮证据不完整 |
| `cleanup_error` | 进程、namespace 或 hwsim 未清理 | 否，需阻止下一轮 |
| `insufficient_evidence` | auth path / key timing 无法判断 | 否，保留 unknown |
| `candidate_security_anomaly` | 独立 Oracle 有完整证据触发 | 仅是候选，需配对重试 |

当前 Observer 对 STATUS 补全失败的做法是记录 `status_error:*` 候选后继续等待；若最终超时，Runner 应检查 observations 并优先报告 `collection_error`，不应丢失原始错误。后续可增加共同异常类型，但必须同时修改 mock 和真实测试。

## 数据契约和合并规则

- 新增字段优先保持向后兼容；修改字段语义、删除字段或重命名 kind 时必须提升后续统一 schema version。
- `event_id`、`run_id`、`attempt_id`、`source`、`kind`、`seq` 是跨模块关联键，任何消费者都不得重写。
- 原始事件和标准事件必须通过 `evidence_ref` 可双向查找；只提交标准结果而丢失原文不算有效证据。
- 进行 Git 合并时，`rg_platform/lab.py`、`rg_platform/probes.py`、`runner.py` 和 `model/contracts.py` 是主要冲突热点。解冲突后必须重跑全部单元测试和一轮真实 smoke。
- 不要只复制 `platform_observer.py`；完整交付至少包含 journal、Lab 接入、Observer、测试、设计文档和真实样例。

## 联调验收清单

1. mock 正常、噪声和两个超时场景仍保持原结果。
2. 真实模式只创建一个 run ID、一个结果目录和一组 Lab 进程。
3. `events.jsonl` 的 `seq` 从 1 连续增长，Runner 请求与 AP/STA 观测共用该序列。
4. 断开事件属于 attempt 1，显式恢复事件属于 attempt 2，错误 attempt 不能完成等待。
5. 观测事件必须在动作 cursor 之后，命令回执本身不能推进效果状态。
6. 基线、断开和恢复快照均有原始/标准两份文件，且证据引用可解析。
7. 中间不重启客户端，进程世代一致；PMKSA 是否存在与是否被使用分开报告。
8. 失败路径保存 observations、失败快照、原始日志和清理结果。
9. Observer 关闭不关闭 Lab socket；Lab 清理后无命名空间、hwsim 或子进程残留。
10. 连续十轮之前先完成至少一个动作拒绝、一个观测超时和一个进程退出失败注入测试。

## 验证

```sh
python3 -m unittest tests.test_observer -v
sudo bash scripts/start.sh --client stock --probe observer-reconnect
```

当前限制：单调序号表示本机收到顺序，不证明无线帧真实发生顺序；未归属原始事件仍以 `attempt_id=null` 保存。完整认证路径、密钥安装和授权时序仍需独立 Oracle 及更细粒度证据。
