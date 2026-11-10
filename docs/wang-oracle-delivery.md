# 王梓权：实际检查器与证据归类交付

日期：2026-11-10。基于提交 `3050854`。对应 `要求.txt` 中王梓权部分，以及
`docs/wang-midterm-code-handoff.md`、`docs/phase5-handoff.md` 的接口约定。

## 交付边界（先说清楚）

- 只新增文件，**未修改** `runner.py`、`comparison.py`、`model/evaluation.py`、
  `backends/platform_observer.py` 等队友模块。2026-11-10 08:56 已随提交 `10530d2`
  推送到 `origin/main`，队友 `git pull --ff-only` 即可获取；提交记录、排除项与
  队友验证命令见[交接核对清单](wang-handoff-checklist.md)第六节。
- **2026-11-10 补充**：已在 VMware 的 Kali 虚拟机上完成 Linux 专属测试与 31 轮真实
  hwsim 实验，真实 `run_id`、事件 seq、快照索引、帧号、PMKID 与日志行号见
  [Kali 真实验证与帧级证据补充](wang-kali-validation-20261110.md)。本文下面
  「测试样例」一节仍保留可复现的**构造场景**（用于说明检查器行为），
  不再是交付给组的证据编号。
- 检查器本体未因真实验证而放宽：`authorization_timing` 在 31 轮真实运行中全部为
  `insufficient_evidence`（平台尚未产出 `key_install` / `port_authorized` 的帧级或
  控制面阶段证据）。这是保守判定的预期结果，不是缺陷，也不是"通过"。
- `security_verdict` 仍为 `not_evaluated`。`state_consistency=pass` 只表示控制事件链与
  双端快照自洽，不等于安全通过；单个检查 fail 是待核查结果，不等于漏洞。

## 新增文件

| 文件 | 职责 |
|---|---|
| `observer/__init__.py` | 包说明 |
| `observer/evidence.py` | 认证路径分类、缓存三时点、尝试边界、帧级证据校验、报告构建与命令行 |
| `observer/oracle.py` | 实际检查器 `state_consistency` / `authorization_timing` |
| `observer/demo.py` | 10 个可运行的构造场景，输出判定与证据引用 |
| `observer/frames.py` | 真实 pcap / 日志证据提取（Kali 补充），只读，产出 `frame_evidence` |
| `tests/test_oracle.py` | 16 项回归测试，含 Runner / comparison 集成 |
| `tests/test_frames.py` | 14 项证据提取纯函数回归，不需要 scapy / Linux |
| `docs/wang-oracle-delivery.md` | 本文 |
| `docs/wang-kali-validation-20261110.md` | Kali 真实验证结果与真实帧号、日志行号 |
| `docs/wang-handoff-checklist.md` | 给队友的逐条核对、待集成项、复现命令与集成禁忌 |

## 模块加载名称与命令

加载名：**`observer.oracle:Oracle`**（工厂就是类本身，无参，每轮新建实例）。

```sh
# 无需 Linux，构造场景
python3 -m observer.demo --list
python3 -m observer.demo --scenario normal
python3 -m observer.demo --scenario all

# 本机逻辑回归
python3 -m unittest tests.test_oracle -v
python3 -m unittest discover -s tests -v

# 模拟后端接入
python3 runner.py --case ap_deauth --evaluator observer.oracle:Oracle

# 真实 hwsim（Kali，sudo）
sudo python3 runner.py --case ap_deauth --backend real --client stock --evaluator observer.oracle:Oracle
sudo python3 comparison.py --backend real --client stock --repeat 10 --evaluator observer.oracle:Oracle

# 从一轮结果目录导出证据报告
python3 -m observer.evidence results/<run_id> --output evidence-report.json

# 从真实 pcap + 日志提取帧号 / PMKID / 控制事件（需 Framework venv 的 scapy）
vendor/wifi-framework/setup/venv/bin/python -m observer.frames \
    results/<run_id> --output evidence-real.json
```

## 两个检查器的判定规则

| 检查名 | pass | fail | insufficient_evidence |
|---|---|---|---|
| `state_consistency` | 三时点双端状态自洽、事件 seq 递增且同 run、最终快照 STA=COMPLETED 且 AP=authorized；单个短暂不同步且后续消解也可 pass，并在 reason 注明 | 事件链重复/倒序/run 不一致；进程世代变化或进程退出；STA=COMPLETED 与 AP 未授权持续到最后快照或出现 ≥2 次；最终快照与已接受的恢复事件冲突 | 缺基线/恢复事件、快照少于三时点、所有双端字段 unknown、最终快照字段缺失 |
| `authorization_timing` | 有本轮 run + 本轮 attempt 的帧级证据，四次握手顺序正确，且 `key_install` 严格早于 `port_authorized` 与 `data_frame` | 端口授权或数据帧早于密钥安装；四次握手时序异常 | 无帧级证据；证据结构不完整、attempt/run 归属不明；缺握手段或授权段（可能是缓存复用或采集缺口）；授权与密钥安装时间戳相同 |

明确不采信的推理链：`pmksa_presence=present` → 缓存复用；`AP AUTHORIZED` → 授权时序正确；
`STA COMPLETED` / 恢复成功 → 安全通过；快照自称 `auth_path=full_sae` 但无帧级佐证 → 认证路径已判定。
失败快照位于恢复之后时，不会被恢复事件覆盖。

## 证据引用格式

`model.evaluation.evaluate()` 只认可两类引用，**pass / fail 必须引用它们**：

| 类型 | 格式 | 能否进 `CheckResult.evidence_refs` |
|---|---|---|
| 标准事件 | `events.jsonl#seq=N`；`event_id` 形如 `<run_id>:raw:<seq>:<kind>` | 能（用 event_id） |
| 标准快照 | `snapshots.json#/i`（0 起） | 能 |
| 抓包帧 | `trace.pcap#frame=N` | **不能**，只能写进 `reason` 或证据报告 |

例如 `frame_ok` 构造场景的 `authorization_timing` pass，引用
`snapshots.json#/2` 与 `20261110-demo-0001:raw:15:connection_restored`，
帧号 `trace.pcap#frame=1207 → 1208 → 1209` 写在 reason 里。

真实运行的实例（Kali `ap_deauth`，完整表见验证补充文档）：
`state_consistency=pass` 的引用是
`20261110-030038-3bccf2:raw:14:baseline_confirmed`、
`20261110-030038-3bccf2:raw:28:connection_restored`、
`snapshots.json#/0`、`snapshots.json#/3`；
真实帧号 `trace.pcap#frame=100..104` 出现在 `observer/frames.py` 的报告中，
不进 `evidence_refs`。

## 帧级证据契约（新提案，平台尚未产出；真实帧已可离线提取）

`platform_observer.py` 目前只输出 `pmksa_presence` / `ap_state` / `port_authorized`，
`auth_path` 与 `key_state` 固定 unknown。要让 `authorization_timing` 真正可判，
需要在标准快照或标准事件 `data` 中提供 `frame_evidence`：

```json
{
  "source": "trace.pcap",
  "run_id": "<本轮 run_id>",
  "attempt_id": 2,
  "sae_observed": true,
  "pmkid_match": null,
  "timeline": [
    {"frame": 1201, "stage": "sae_commit", "monotonic_s": 101.20},
    {"frame": 1203, "stage": "eapol_m1", "monotonic_s": 101.30},
    {"frame": 1206, "stage": "eapol_m4", "monotonic_s": 101.39},
    {"frame": 1207, "stage": "key_install", "monotonic_s": 101.42},
    {"frame": 1208, "stage": "port_authorized", "monotonic_s": 101.43},
    {"frame": 1209, "stage": "data_frame", "monotonic_s": 101.45}
  ]
}
```

`stage` 取值：`sae_commit` / `sae_confirm` / `eapol_m1..m4` / `key_install` /
`port_authorized` / `data_frame`。时间用本机单调时钟秒。
`attempt_id` 与 `run_id` 必须匹配本轮，否则判归属不明，保留证据不足。

**真实验证后补充的契约缺口**：`key_install` 与 `port_authorized` 是控制面事件，
不是空口帧，没有 `frame` 编号；实测只有 `events.jsonl#seq=N` 与单调时间
（如 ap_deauth 的 `seq=27` 密钥安装、`seq=25` AP-STA-CONNECTED）。因此建议把
`timeline` 条目扩展为 `{"frame": null, "event_seq": 27, "stage": "key_install",
"monotonic_s": ...}`，并同步放宽 `validate_frame_evidence` 与 `frame_ref()`。
本次**未**修改检查器去接受这种条目——在没有平台侧正式产出之前，保持
`insufficient_evidence`。`observer/frames.py` 已能从真实 pcap / 日志产出
不含这两个阶段的 `frame_evidence`，可直接作为该契约的实现参考。

## 认证路径、缓存行为、尝试边界

| 项 | 当前结论 |
|---|---|
| 认证路径 | `full_sae` / `pmksa_reuse` / `unknown`。有本轮 SAE + 完整四次握手帧证据才判 `full_sae`；有 PMKID 匹配且明确无 SAE 才判 `pmksa_reuse`；其余一律 `unknown` |
| 缓存行为 | 逐快照记录 `present` / `absent` / `unknown` 与 `snapshots.json#/i` 引用。缓存条目存在不等于本次复用，`reuse_claimed` 目前恒为 false。**真实实测**：三案例共 11 个快照时点（4+4+3）`pmksa_presence` 全为 `present`，断开后缓存未清空；快照 `attempt_id` 全为 `null`，不伪造归属。三份 `report-*.json` 与汇总表见验证补充文档第 4.1 节 |
| 尝试边界 | 按 seq 排序，区分本轮请求、本轮观测、`late_old_attempt`（更大 attempt 出现后又退回旧值）与 `attribution_unproven`（`received_after_cursor`）。迟到旧事件不参与当前判定，不以接收游标证明协议归属。**真实实测**：三案例各 3 个标准观测事件全部带 `received_after_cursor`，归入 `attribution_unproven`（`observations=0`），无一条被冒充当前尝试；这是保守归类的预期行为，不是解析失败 |

`observer/evidence.py` 还提供 `python3 -m observer.evidence <run_dir>`，从
`snapshots.json` + `runner-events.jsonl`（或 `events.jsonl`）+ `context.json`
生成含 `frame_refs`、`auth_path`、`cache`、`attempt_boundary` 与 `limitations` 的报告。

## 测试样例（构造场景，用于说明检查器行为；交付给组的真实编号见补充文档）

以下命令全部是可复现的**构造输入**，用于证明检查器不是固定返回 pass。
它们不是实验日志；真实 `run_id` / 事件 seq / 帧号请以
[Kali 真实验证与帧级证据补充](wang-kali-validation-20261110.md) 为准。

| 类别 | 场景 | 命令 | 预期 | 关键引用 |
|---|---|---|---|---|
| 正常 | `normal` | `python3 -m observer.demo --scenario normal` | `state_consistency=pass`；`authorization_timing=insufficient_evidence`（无帧证据） | `...:raw:11:baseline_confirmed`、`...:raw:15:connection_restored`、`snapshots.json#/0`、`snapshots.json#/2` |
| 正常（帧证据齐） | `frame_ok` | `--scenario frame_ok` | 两项 `pass` | 同上 + `snapshots.json#/2`；帧号 `trace.pcap#frame=1207/1208/1209` |
| 异常 | `persistent_mismatch` | `--scenario persistent_mismatch` | `state_consistency=fail` | `snapshots.json#/1`、`snapshots.json#/2`、恢复事件 |
| 异常 | `process_restart` | `--scenario process_restart` | `state_consistency=fail`（进程世代变化） | `snapshots.json#/0`、`snapshots.json#/1` |
| 异常（时序） | `frame_bad` | `--scenario frame_bad` | `authorization_timing=fail`，reason 含 `trace.pcap#frame=1208` | `snapshots.json#/2`、恢复事件 |
| 短暂不同步 | `transient_mismatch` | `--scenario transient_mismatch` | `state_consistency=pass`，reason 注明短暂不同步 | `snapshots.json#/1` |
| 证据不足 | `missing_evidence` | `--scenario missing_evidence` | `state_consistency=insufficient_evidence` | 无（不明确引用） |
| 证据不足 | `old_attempt_frame` | `--scenario old_attempt_frame` | `authorization_timing=insufficient_evidence`（帧证据属 attempt 1） | 无 |
| 证据不足 | `pmksa_present_only` | `--scenario pmksa_present_only` | `auth_path=unknown`，快照自称 full_sae 不采信 | 报告中 `cache.checkpoints` |
| 迟到旧事件 | `late_old_attempt` | `--scenario late_old_attempt` | 迟到事件被排除，reason 注明 | `attempt_boundary.old_or_unattributed` |

`run_id` 统一为 `20261110-demo-0001`，事件 seq 11—15（迟到事件 19），
快照索引 0/1/2，帧号 1201—1209。**这些全是构造值。**

回归测试：本机 Windows / Python 3.13，`python3 -m unittest tests.test_oracle -v`
16 项通过；全量 `discover` 见下节验证记录。

## 真实验证已完成（Kali，2026-11-10）

原「待补清单」已执行完毕，结果见
[Kali 真实验证与帧级证据补充](wang-kali-validation-20261110.md)。要点：

| 原待补项 | 状态 |
|---|---|
| 三路径各 10 轮 + `--evaluator observer.oracle:Oracle` | 30/30 完成；批次 `comparison-9e1b03410cea41b4b8cd7075f3d3cfff`；另有 1 轮单案例，共 31 轮 |
| 每案例取一轮原始产物（runner-events / snapshots / context / trace.pcap / sta.log） | 已取并回传 Windows：`D:/CS_project/kali-validation-20261110/evidence/<run_id>/` |
| 用真实帧号替换构造帧号 | 已由 `observer/frames.py` 完成；例：ap_deauth EAPOL `frame=100..103`、首个受保护数据帧 `frame=104`、带 PMKID 的 AssocReq `frame=98` |
| `synthetic` 标记 | 构造场景仍标注为构造输入；交付证据全部改用真实 `run_id` / `event_id` / `events.jsonl#seq=N` / `snapshots.json#/i` / `trace.pcap#frame=N` / `sta.log#line=N` |
| `authorization_timing` | 31 轮全部 `insufficient_evidence`，已如实写明缺 `key_install` / `port_authorized` 的帧级载体，未把恢复成功写进安全结论 |
| 汇总三时点缓存观测 | 已对三案例各跑 `observer.evidence`，产出 `report-ap_deauth.json` / `report-ap_disassoc.json` / `report-client_disconnect_reconnect.json`；实测为 4+4+3 个时点（不裁剪成三行），见验证补充文档第 4.1 节 |
| 两套证据链口径统一 | 标准报告 `auth_path=unknown` 与帧报告 `pmksa_reuse` 分属标准产物层与事后取证层，已在验证补充文档第 4.2 节说明差异、自洽性与正确回流通路 |

额外结论：三种案例的重连在空口上都**没有 SAE 认证帧**，Association Request 带 PMKID，
supplicant 日志有 `PMKSA cache entry found` / `matched PMKID` / `PMKSA caching was used`，
即实际路径为 `pmksa_reuse`；Runner 的 `auth_path` 仍为 `unknown`，属平台未接入，
不是"没有复用"。

## 依赖与后续集成

逐条状态、负责人划分、复现命令与集成禁忌见
[交接核对清单](wang-handoff-checklist.md)。要点：

- 王奥鑫：确认 `frame_evidence` 契约后，可把它接入标准快照/事件；建议按验证补充文档
  第 5 节支持 `event_seq` 型控制面阶段。`--evaluator` 接口本次未改动，可直接加载本模块。
  另需确认 `midterm-readiness` L58「默认 CLI 接入」的口径，以及 Runner 的
  `auth_path` 是否从 `unknown` 回填 `pmksa_reuse`。
- 江天豪：恢复动作语义、拒绝与超时回执，用于确认重复 RECONNECT 是否产生额外自动连接。
- 陈家森：真实 pcap 与日志本次已由本机 Kali 复现并回传；仍缺同一客户端进程内的失败注入记录。
- 本次未接入认证子阶段（Runner 的 `auth_path` 仍 `unknown`），未启用真实重试，
  未生成 PPT/录屏，未固定依赖交付包；这些仍是团队中期检查中列出的未完成项。

## 验证记录

- 加载：`load_evaluator('observer.oracle:Oracle')` 返回新实例，空证据不判 pass。
- 单测：`tests/test_oracle.py` 16 项 + `tests/test_frames.py` 14 项通过。
  全量 `python3 -m unittest discover -s tests`：
  **Windows 104 项（100 通过 / 4 项 Linux 专属跳过）；Kali 105 项全部通过、0 跳过**。
  Windows 的 4 项跳过是运行时环境条件（无 `AF_UNIX` / 非 linux 平台）导致，不是未测；
  覆盖这些用例的权威结论以 Kali 为准，详见验证补充文档第 2 节。原有测试全部未被破坏。
- 命令行冒烟（mock）：`python3 runner.py --case ap_deauth --evaluator observer.oracle:Oracle`
  返回 `execution_status=completed`、`evaluator=observer.oracle:Oracle`、
  两个检查均为 `insufficient_evidence`、`security_verdict=not_evaluated`，无 evaluation_error。
- 真实 hwsim：31 轮（30 批次 + 1 单案例）全部完成并恢复、清理成功；
  `state_consistency` 31 pass、`authorization_timing` 31 `insufficient_evidence`、
  `security_verdict` 31 `not_evaluated`。详见验证补充文档。
- 标准证据报告：对回传的三轮真实结果目录各跑一次 `python3 -m observer.evidence`，
  三份 `report-*.json` 均成功产出（无需 Linux / scapy），含 4 / 4 / 3 个缓存时点、
  `attempt_boundary` 分类与 `limitations`；汇总见验证补充文档第 4.1 节。
- 覆盖：正常、异常、证据不足、迟到旧事件、缓存不等于复用、帧级时序正确/错误/归属不明、
  证据引用有效性、CLI 报告、Runner 与 comparison 集成；另加真实 RSN PMKID 解析与
  EAPOL Key Info 分类。
- 保守性：不同输入产生 6 种以上不同判定组合，不存在固定返回 pass 的路径；
  真实环境下 31 轮未出现一轮安全通过。
- 工作区：开发期间 `git diff --stat` 为空、已跟踪文件零改动；10 个新增文件均随
  `10530d2` 入库并推送。后续修订本文需另一次 `commit` / `push` 才能同步给队友。
