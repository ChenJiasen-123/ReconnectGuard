# 王梓权：Kali 真实验证与帧级证据补充

日期：2026-11-10。承接 [检查器交付](wang-oracle-delivery.md)，在 VMware 的 Kali 虚拟机上
完成 Linux 专属测试、真实 hwsim 三路径批次，以及认证路径 / 缓存 / 授权时序的**真实日志与帧号**。
本文所有编号均来自本次实际运行的产物，不是构造数据（原交付文档的 `synthetic=true` 已作废）。

## 1. 实验环境

| 项 | 值 |
|---|---|
| 虚拟机 | VMware Workstation，`E:\Virtual Machine\lab4\kali\kali-linux-2026.2-vmware-amd64.vmwarevm\...vmx`（NAT，192.168.220.130） |
| 内核 / Python | `6.19.14+kali-amd64` / `3.13.12` |
| 平台 | Framework `7a173c1824caa95a848f351fe6eab2d5e5b389e1` 的 `hostap_2_10`，hostapd v2.10；官方未修改 wpa_supplicant 2.10（w1.fi 源码编译，SHA-256 已校验） |
| 抓包 | `tcpdump --immediate-mode` 于 `mon<ap_if>`，产物 `trace.pcap` |
| 项目目录 | `/home/kali/Projects/rg-kali-20261110`（独立工作区；验证时 `observer/` 等为未提交新增文件，现已随 `10530d2` 入库） |
| 依赖 | `scripts/setup.sh` 全流程；Framework venv 内 scapy 2.6.1；hwsim 双端 + netns，sudo 运行 |

平台自检 `sudo bash scripts/start.sh --client stock --probe deauth` 通过
（`results/20261110-030004-28a25a`）。批次结束后 `ip netns list` 为空、
`mac80211_hwsim` 已卸载，31 轮 `cleanup_status` 全为 `completed`。

## 2. 跑过什么

| 类别 | 命令 / 数量 | 结果 |
|---|---|---|
| Linux 专属单测 | `python3 -m unittest discover -s tests -v` | **105 项全部通过，0 跳过** |
| Windows 对照 | 同上 | 104 项，4 项跳过（100 通过） |
| 单案例真实接入 | `sudo python3 runner.py --case ap_deauth --backend real --client stock --evaluator observer.oracle:Oracle` | 1 轮完成 |
| 三路径批次 | `sudo python3 comparison.py --backend real --client stock --repeat 10 --evaluator observer.oracle:Oracle` | 30 / 30 执行，无提前停止 |
| 合计真实轮次 | 31 | 31 完成、31 恢复、31 清理成功 |

Windows 上跳过的 4 项在 Kali 上均真实执行并通过：`test_control.py` 的 3 项
（`AF_UNIX` 条件）与 `test_snapshot.py` 的模块级 Linux 跳过（Linux 上展开为 2 项）。
因此 Linux 比 Windows 多 1 项、且无跳过。

**这两次是两台机器上的独立运行**：Kali 跑的是同步过去的代码副本，本机 Windows 是
另一份工作区。是否跳过由运行时的 `hasattr(socket, "AF_UNIX")` 与 `sys.platform`
决定，是环境属性，Kali 跑通不会让本机再跑一次时不跳过。所以报告必须并列两个数字；
只写 Windows 的「4 项跳过」会让人误以为 Linux 专属用例没测。覆盖这些用例的
**权威结论是 Kali 的 105 项全通过、0 跳过**。
`evidence/linux-unittest.log` 已更新为最终版本（105 项）的日志，
不是加入 `observer/frames.py` 之前那份 91 项的旧日志。

批次目录：`results/comparison-9e1b03410cea41b4b8cd7075f3d3cfff/summary.json`，
`requested=30 / executed=30 / stop_reason=null`。

| 案例 | 完成 / 恢复 | 恢复耗时中位数 | Runner 的 auth_path | state_consistency | authorization_timing |
|---|---|---|---|---|---|
| `client_disconnect_reconnect` | 10 / 10 | 9.913 ms | `unknown` | 10 pass | 10 insufficient_evidence |
| `ap_deauth` | 10 / 10 | 3882.017 ms | `unknown` | 10 pass | 10 insufficient_evidence |
| `ap_disassoc` | 10 / 10 | 3880.129 ms | `unknown` | 10 pass | 10 insufficient_evidence |

加上单案例那一轮，`state_consistency` 共 31 pass、`authorization_timing` 共
31 `insufficient_evidence`，31 轮 `security_verdict` 全为 `not_evaluated`。
**没有一轮被判安全通过，也没有把恢复成功写进安全结论。**
恢复耗时与队友服务器上的 18.105 / 3872.180 / 3872.726 ms 属于不同环境，不可直接比较；
三组的重连机制本来也不同，差异不能归因于断开方式。

## 3. 真实引用（替换原构造值）

`python3 -m observer.frames <run_dir>` 从 `trace.pcap` + `events.jsonl` + `sta.log`
提取真实帧号、单调时间、PMKID 与日志行号。三种案例各取一轮：

| 案例 | run_id | 标准事件 `event_id` | 快照引用 |
|---|---|---|---|
| `ap_deauth` | `20261110-030038-3bccf2` | `...:raw:14:baseline_confirmed`、`...:raw:15:disconnect_requested`、`...:raw:17:sta_disconnected`、`...:raw:28:connection_restored` | `snapshots.json#/0..#/3` |
| `ap_disassoc` | `20261110-030439-99aa36` | `...:raw:14:baseline_confirmed`、`...:raw:15:disconnect_requested`、`...:raw:18:sta_disconnected`、`...:raw:29:connection_restored` | `snapshots.json#/0..#/3` |
| `client_disconnect_reconnect` | `20261110-030424-a2e034` | `...:raw:14:baseline_confirmed`、`...:raw:15:disconnect_requested`、`...:raw:17:sta_disconnected`、`...:raw:19:reconnect_requested`、`...:raw:26:connection_restored` | `snapshots.json#/0..#/2` |

`state_consistency` 的真实 `CheckResult.evidence_refs` 例（ap_deauth）：
`20261110-030038-3bccf2:raw:14:baseline_confirmed`、
`20261110-030038-3bccf2:raw:28:connection_restored`、`snapshots.json#/0`、`snapshots.json#/3`。

### 真实帧号（重连窗口内）

| 案例 | 抓包总帧 | 断开边界(UTC) | Assoc Req 带 PMKID | EAPOL M1→M4 | 首个受保护数据帧 |
|---|---|---|---|---|---|
| `ap_deauth` | 109 | `2026-11-10T08:00:43.681667Z` | `trace.pcap#frame=98` | `100 → 101 → 102 → 103` | `trace.pcap#frame=104` |
| `ap_disassoc` | 112 | `2026-11-10T08:04:48.300515Z` | `trace.pcap#frame=98` | `100 → 101 → 102 → 103` | `trace.pcap#frame=105` |
| `client_disconnect_reconnect` | 70 | `2026-11-10T08:04:29.118005Z` | `trace.pcap#frame=59` | `61 → 62 → 63 → 64` | `trace.pcap#frame=65` |

四次握手用 EAPOL-Key 的 Key Information 位判定，实测值分别为
`0x0088 / 0x0108 / 0x13c8 / 0x0308`，与 M1/M2/M3/M4 一一对应。
帧号是 `trace.pcap` 内从 1 开始的顺序号，**只在本轮抓包内有效**。

## 4. 认证路径与缓存：三种案例的重连都是 PMKSA 复用

这是本次最重要的新增结论，且**不是**从「PMKSA 表有条目」推出来的。三条独立证据：

1. **空口帧**：重连窗口内没有 SAE 认证帧（`algo=3` 的 Auth 帧数为 0），
   只出现 Open System 认证（`algo=0`）；Association Request 的 RSN IE 携带 PMKID。
   初始连接则相反，有 SAE commit/confirm（如 ap_deauth 的 `frame=42..45`）。
2. **supplicant 日志**：以 `ap_deauth` 为例，`sta.log#line=1214`
   `PMKSA cache entry found - try to use PMKSA caching instead of new SAE authentication`、
   `#line=1305` `RSN: PMKID from assoc IE found from PMKSA cache`、
   `#line=1374` `RSN: matched PMKID`、`#line=1377` `EAPOL: PMKSA caching was used`。
3. **控制事件**：`events.jsonl#seq=6` 为 `PMKSA-CACHE-ADDED`；
   四次快照 `pmksa_presence` 全为 `present`（断开后未清空缓存）。

| 案例 | 本轮 PMKID | 缓存命中日志行 | 本轮 SAE 帧 |
|---|---|---|---|
| `ap_deauth` | `440c661a8089039046985a11618e34f7` | `sta.log#line=1214, 1305, 1374` | 无 |
| `ap_disassoc` | `19eb58524018a57d60ed773f28481135` | `sta.log#line=1239, 1329, 1398` | 无 |
| `client_disconnect_reconnect` | `2eeceae2c3cee5fae595303f3eaef100` | `sta.log#line=1156, 1245, 1313` | 无 |

结论：本轮重连路径为 `pmksa_reuse`（事后证据分析），**不是** `full_sae`。
但 Runner 的 `context.auth_path` 与快照 `auth_path` 仍固定 `unknown`，
30 轮批次的 `auth_path_counts` 全是 `unknown: 10`——这是平台侧尚未接入认证子阶段的
已知缺口，不是"没有复用"。要让 Runner 自己写出正确路径，需由平台把
`observer.frames` 的判定接进标准快照。

尝试边界方面：四/五个标准事件的 seq 严格递增且同属本轮，
`attempt_id=2` 对应恢复阶段；`client_disconnect_reconnect` 额外有
`...:raw:19:reconnect_requested`，另两个案例是自动恢复、无该请求事件。
未观察到可证实的迟到旧尝试事件；`events.jsonl` 中未标注 attempt 的 Lab 原始事件
按 `received_after_cursor` 处理，不用于证明协议归属。

### 4.1 缓存时点汇总（标准产物视角）

`docs/midterm-readiness-2026-11-10.md` 要求「汇总三时点缓存观测」。实测快照数并不
统一为三：两个 AP 案例各 4 份，显式重连案例 3 份。全部列出，不裁剪成三行。
对回传目录各跑一次 `python3 -m observer.evidence <run_dir> --output <file>`
（不需要 Linux / scapy），产出三份标准报告：

| 案例 | 报告 | 快照时点 | `present` | `reuse_claimed` | 标准报告 `auth_path` | 帧报告 `auth_path` |
|---|---|---|---:|---|---|---|
| `ap_deauth` | `report-ap_deauth.json` | 0 baseline / 1 after_disconnect / 2 connection_restored / 3 after_reconnect | 4 / 4 | false | `unknown` | `pmksa_reuse` |
| `ap_disassoc` | `report-ap_disassoc.json` | 同上 4 时点 | 4 / 4 | false | `unknown` | `pmksa_reuse` |
| `client_disconnect_reconnect` | `report-client_disconnect_reconnect.json` | 0 / 1 / 2 after_reconnect（无 connection_restored 快照） | 3 / 3 | false | `unknown` | `pmksa_reuse` |

三份报告位于 `D:/CS_project/kali-validation-20261110/evidence/`。
所有时点 `pmksa_presence=present`，说明断开后缓存未被清空；快照的
`attempt_id` 全为 `null`（平台未声明），按 `docs/observer-design.md` 的约定
保留空值，不伪造协议归属。`port_authorized` 在 after_disconnect 为
`not_authorized`、恢复后为 `authorized`——这是读取时的 AP flag，不是授权时序证据；
`auth_path` 与 `key_state` 在所有快照中均为 `unknown`。

`attempt_boundary` 的实测结果需要一并说明，否则容易误读为解析失败：三个标准观测事件
（`baseline_confirmed` / `sta_disconnected` / `connection_restored`）的
`data.attempt_attribution` 全是 `received_after_cursor`，因此被归入
`attribution_unproven`，`observations=0`；`*_requested` 事件无该标记，归为
`requests`（AP 案例 1 个、显式重连 2 个）。三份报告 `old_or_unattributed` 均为 3，
**没有任何一条被冒充为当前尝试的观测**。这是保守归类的预期行为：
`docs/observer-design.md` 明确「接收顺序不等于空口顺序」，不以接收游标证明协议归属。

### 4.2 两套证据链为什么给出不同结论

`unknown` 与 `pmksa_reuse` 不是矛盾，而是两个不同证据层的结论：

| | `observer.evidence`（标准产物视角） | `observer.frames`（事后取证视角） |
|---|---|---|
| 输入 | `snapshots.json` + `runner-events.jsonl` + `context.json` | `trace.pcap` + `events.jsonl` + `sta.log` |
| 时机 | Runner 运行期间即可得 | 事后离线，需 Framework venv 的 scapy |
| 可见字段 | `pmksa_presence`、`auth_path=unknown`、`key_state=unknown` | 空口帧、RSN IE 的 PMKID、supplicant 日志命中 |
| `frame_evidence` | 标准产物**没有该字段**，`frame_evidence_count=0`、`frame_refs=[]` | 可生成含 `eapol_m1..m4` / `data_frame` 的时间线，但缺 `key_install` / `port_authorized` |
| 结论 | `auth_path=unknown` | `auth_path=pmksa_reuse` |

检查器 `observer.oracle:Oracle` 只从 `run_case` 传入的 `snapshots` / `events`
读取证据，**不会去读离线报告**。所以：

- `result.json` 中 `auth_path=unknown` 与标准报告自洽，反映的是 Runner 运行时
  能看到的世界；30 轮 `auth_path_counts` 全为 `unknown: 10` 不是判定错误。
- `pmksa_reuse` 是原始证据层的事后结论。要让它变成 Runner 自己写出的路径，
  必须由平台在采集时把 `observer.frames` 的判定写进标准快照，或由 Runner 回填。
- 即便把 `observer.frames` 的 `frame_evidence` 直接喂给检查器，
  `authorization_timing` 仍会保持 `insufficient_evidence`：其 timeline 只有五个
  空口阶段，缺少 `key_install` 与 `port_authorized` 两个控制面阶段（见第 5 节）。

**不要为了让标准报告显示 `pmksa_reuse` 而把离线结论手工填进 `snapshots.json`。**
那会篡改原始产物、破坏证据可回溯性（`docs/observer-design.md` 第 162、191 行）。
正确路径是平台侧产出该字段。给队友的逐条状态与集成入口见
[交接核对清单](wang-handoff-checklist.md)。

## 5. 授权时序：31 轮全部 insufficient_evidence，以及为什么

真实帧级时间线（ap_deauth，`monotonic_s` 已由 `events.jsonl` 的
`received_at`/`monotonic_s` 固定偏移换算到同一时钟）：

| 单调时间(s) | 引用 | 含义 |
|---|---|---|
| 1357.853352 | `trace.pcap#frame=100` | EAPOL M1 |
| 1357.853604 | `trace.pcap#frame=101` | EAPOL M2 |
| 1357.853726 | `trace.pcap#frame=102` | EAPOL M3 |
| 1357.853894 | `trace.pcap#frame=103` | EAPOL M4 |
| 1357.854033 | `events.jsonl#seq=25` | AP-STA-CONNECTED |
| 1357.880554 | `events.jsonl#seq=27` | WPA: Key negotiation completed（STA 密钥安装） |
| 1357.880883 | `events.jsonl#seq=28` | CTRL-EVENT-CONNECTED |
| 1357.892213 | `trace.pcap#frame=104` | 首个受保护数据帧 |

`key_install` 与 `port_authorized` 是**控制面状态变化，不是空口帧**，没有帧号；
当前 `frame_evidence` 契约要求每个 `timeline` 条目都有数值 `frame`，因此无法表示它们。
`observer.oracle:Oracle` 按约定返回 `insufficient_evidence`（缺 `key_install`），
不因恢复成功、AP `AUTHORIZED` flag 或 `STA COMPLETED` 改判。

特别提醒：`events.jsonl#seq=25` 的 `AP-STA-CONNECTED` 只表明关联成功，
**不是端口授权**。它比 STA 侧密钥安装早约 26.5 ms；若把它误当 `port_authorized`
塞进帧级时间线，会得出"授权早于密钥安装"的伪 fail。这正是检查器坚持保守判定的原因。

要让 `authorization_timing` 真正可判，需扩展契约：允许 `timeline` 条目用
`event_seq` + `monotonic_s` 表示控制面事件（`frame` 可为空），并由平台在标准快照/事件中
产出该字段。这属于平台/Runner 集成工作，本次**没有**为了让结果通过而放宽检查器。

## 6. 新增代码与产物

| 文件 | 职责 |
|---|---|
| `observer/frames.py` | 真实 pcap / 日志证据提取：RSN IE PMKID 解析、EAPOL Key Info 分类、帧分类、时钟换算、`frame_evidence` 生成、保守的 `authorization_timing` 说明；只读，不改 Runner |
| `tests/test_frames.py` | 14 项纯函数回归，不需要 scapy / Linux / hwsim |

运行方式（需 Framework venv 里的 scapy）：

```sh
vendor/wifi-framework/setup/venv/bin/python -m observer.frames \
    results/<run_id> --output evidence-real.json
python3 -m unittest tests.test_frames -v
```

证据产物：

- Kali：`/home/kali/Projects/rg-kali-20261110/kali-validation/evidence/`
  （三案例 `evidence-*.json`、`comparison-summary.json`、`linux-unittest.log`、
  三轮原始 `result.json` / `snapshots.json` / `runner-events.jsonl` / `events.jsonl` / `sta.log` / `trace.pcap`）
- Windows 回传：`D:/CS_project/kali-validation-20261110/`（同结构 + `batch-summary.json`）。
  另有三份 `evidence/report-*.json`：回传后在本机用 `python3 -m observer.evidence`
  对三轮结果目录生成（第 4.1 节），不依赖 Linux / scapy，Kali 侧未重复产出。

`results/`、`evidence/`、`vendor/`、`work/`、`__pycache__/`、`*.pyc` 均在
`.gitignore` 中，未进入版本控制。**真实 pcap、日志与证据报告不入库**：本次 32 个
产物保存在 `D:/CS_project/kali-validation-20261110/` 与 Kali 的
`.../kali-validation/evidence/`，本文所有 `trace.pcap#frame=N`、`sta.log#line=N`
与 `run_id` 都指向这两处。队友若要核对原始帧，需向王梓权索取或到 Kali 目录取；
团队原本就把原始证据留在服务器，不随代码提交。
文中的 `D:/CS_project/...` 与 `/home/kali/Projects/...` 是本次验证的本机路径，
在各自机器上复现时替换为本地 `results/<run_id>` 即可。
本次验证只新增文件、未修改队友模块；验证完成后代码与文档已随提交 `10530d2`
（2026-11-10 08:56）推送到 `origin/main`，队友 `git pull --ff-only` 即可获取。
真实 pcap、日志与证据报告仍不入库，需另行索取。

## 7. 环境踩坑记录（可复用）

1. `scripts/setup.sh` 的 `apt-get update` 在 Kali 的 aliyun 镜像上卡在
   `Contents (deb) [54.5 MB]` 超过 7 分钟。用
   `apt-get update -o Acquire::IndexTargets::deb::Contents-deb::DefaultEnabled=false -o Acquire::Languages=none`
   跳过 Contents 索引，3 秒完成；随后补装 `pkg-config libnl-3-dev libnl-genl-3-dev
   libnl-route-3-dev libssl-dev libdbus-1-dev` 即可编译。
2. libnl 3.12 的头文件在 `/usr/include/libnl3/netlink/...`，不是 `/usr/include/netlink/...`；
   hostap 靠 `pkg-config --cflags libnl-3.0` 找到它，所以 `pkg-config` 不能缺。
3. `wpa_supplicant -v` 会以退出码 1 结束（版本信息写 stderr）。
   任何 `set -e` 的校验脚本里要单独处理，否则会误报"构建失败"——本次二进制其实已正确生成。
4. Git Bash 会把以 `/` 开头的参数自动转成 Windows 路径。用
   `MSYS2_ARG_CONV_EXCL='*' python kali.py put ... /home/kali/...`，
   或改用 PowerShell 调用，避免把文件传到 `C:\d\...` 之类的错误落点。
5. `sudo` 在 Kali 上是免密的，但 `sudo` 产出的结果目录属主是 root；
   回传前需 `chown -R kali:kali`，否则 SFTP 以 kali 身份读不到。

## 8. 仍待团队处理

- 平台 / Runner 侧把 `frame_evidence` 接进标准快照或事件，并扩展为支持
  `event_seq` 的控制面阶段，才能让 `authorization_timing` 有判 pass 的可能。
- Runner 的 `auth_path` 仍是 `unknown`；本次的 `pmksa_reuse` 是事后证据分析结论，
  尚未回流到 `result.json`。
- 真实重试仍禁用、认证子阶段未接入、中期 PPT / 录屏 / 独立交付包未产出——
  这些仍是 `docs/midterm-readiness-2026-11-10.md` 中的未完成项。
- 未跟踪的 `backends/real_observer.py` / `tests/test_real_observer.py` 旧草稿
  仍未被生产代码引用，本次未动。
