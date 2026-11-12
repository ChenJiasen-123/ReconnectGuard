# 附录：2026-11-12 hwsim 真机证据原文

配合 [恢复动作语义与动作回执](jiang-recovery-semantics.md) 第 3 节使用。
本文只摘录原文，不做二次解释。原始目录在实验机
`/home/chen/Projects/rg-jiang-20261112/results/<run_id>/`，
本机副本在 `work/evidence/`，两者都被 Git 忽略，不随仓库分发。

环境：Kali、Python 3.14.7、hwsim 双端、stock wpa_supplicant 2.10、
代码基线 `e17b457`。时间戳为服务器本地时区（UTC-06:00）。

## 1. 基线 Runner 轮 `20261112-121108-0f072c`

### 1.1 `actions.jsonl` 原文（两行）

```json
{"action_id": "20261112-121108-0f072c:action:1", "action": "client_disconnect", "accepted": true, "effect_status": "unverified", "run_id": "20261112-121108-0f072c", "attempt_id": 1, "target": "sta", "command": "DISCONNECT", "raw_response": "OK", "started_at_utc": "2026-11-12T18:11:13.052683+00:00", "finished_at_utc": "2026-11-12T18:11:13.142071+00:00", "duration_ms": 89.405, "error_category": null}
{"action_id": "20261112-121108-0f072c:action:2", "action": "client_reconnect", "accepted": true, "effect_status": "unverified", "run_id": "20261112-121108-0f072c", "attempt_id": 2, "target": "sta", "command": "RECONNECT", "raw_response": "OK", "started_at_utc": "2026-11-12T18:11:13.146856+00:00", "finished_at_utc": "2026-11-12T18:11:13.147847+00:00", "duration_ms": 1.007, "error_category": null}
```

两条都在发送时就把 `attempt_id` 记为 1 与 2，`raw_response` 为 `OK`，
`effect_status` 为默认值 `unverified`。

### 1.2 `result.json` 摘录

```json
{"run_id": "20261112-121108-0f072c", "backend": "real", "simulated": false,
 "execution_status": "completed", "recovery": "pass",
 "client_process_unchanged": "pass", "auth_path": "unknown",
 "security_verdict": "not_evaluated",
 "security_profile": "pass",
 "checks": [{"name": "state_consistency", "status": "not_evaluated"},
            {"name": "authorization_timing", "status": "not_evaluated"}]}
```

两项检查为 `not_evaluated` 是因为本轮没有传 `--evaluator`。

耗时：

```json
{"reconnect_request_to_restored": {"value": 11.524,
  "evidence": ["20261112-121108-0f072c:raw:19:reconnect_requested",
               "20261112-121108-0f072c:raw:28:connection_restored"]},
 "disconnect_observed_to_restored": {"value": 17.735,
  "evidence": ["20261112-121108-0f072c:raw:17:sta_disconnected",
               "20261112-121108-0f072c:raw:28:connection_restored"]}}
```

### 1.3 补算后的回执归属

`python3 -m backends.receipts results/20261112-121108-0f072c`：

```text
20261112-121108-0f072c:action:1 client_disconnect  accepted=True  effect=observed  attribution=same_attempt  request=...:raw:15:disconnect_requested  effect_event=...:raw:17:sta_disconnected
20261112-121108-0f072c:action:2 client_reconnect   accepted=True  effect=observed  attribution=same_attempt  request=...:raw:19:reconnect_requested   effect_event=...:raw:28:connection_restored
```

### 1.4 帧级取证（`observer.frames`）

`auth_path=pmksa_reuse`，`pcap_frames_total=70`：

| 阶段 | 帧号 |
|---|---|
| `open_system_auth` | 57, 58 |
| `assoc_req_with_pmkid` | 59 |
| `eapol_m1` / `m2` / `m3` / `m4` | 61 / 62 / 63 / 64 |
| `data_frame` | 65, 66 |

PMKID `174e083fab3c4d41d645d845652f8b9d`；依据日志行
`sta.log#line=887`（`matched PMKID`）、`sta.log#line=890`
（`EAPOL: PMKSA caching was used - skip EAPOL`）、
`sta.log#line=1157`（`PMKSA cache entry found - try to use PMKSA caching instead of new SAE authentication`）。

`authorization_timing` 为 `insufficient_evidence`，原因写的是
`帧级证据缺少 key_install 阶段`；`key_install` 与 `port_authorized`
是控制面状态变化，没有帧号。

## 2. E3 重复 RECONNECT `20261112-121210-3dc87c`

hold 模式，接口 `rgst3dc87c`，AP 命名空间 `rg-ap-3dc87c`。

### 2.1 控制接口返回（会话记录）

hold 模式不写 `actions.jsonl`，以下值取自会话控制台输出，不落在结果目录：

| 时刻 | 命令 | 返回 | `wpa_state` | `pmksa` 条目 | PID |
|---|---|---|---|---|---|
| T0 | `status` / `pmksa` | — | `COMPLETED` | `1 02:00:00:00:00:00 f1c7dbf104a707a6415b10f728a5ca17 43200 0` | 830231 |
| T1 | `DISCONNECT` | `OK` | `DISCONNECTED` | 同条目，exp `43198` | 830231 |
| T2 | `RECONNECT` | `OK` | `COMPLETED` | 同条目，exp `43198` | 830231 |
| T3 | `RECONNECT`、`RECONNECT` | `OK`、`OK` | `COMPLETED` | 同条目，exp `43194` | 830231 |
| T4 | 等待 5 s 后 `status` | — | `COMPLETED` | 未变 | 830231 |

PMKID 全程为 `f1c7dbf104a707a6415b10f728a5ca17`，只有过期计时递减。

### 2.2 `sta.log` 关键行原文

```text
855:  nl80211: Associated with 02:00:00:00:00:00
877:  rgst3dc87c: Associated with 02:00:00:00:00:00
935:  EAPOL: PMKSA caching was used - skip EAPOL
1165: rgst3dc87c: Control interface command 'RECONNECT'
1289: nl80211: Associated with 02:00:00:00:00:00
1315: rgst3dc87c: Associated with 02:00:00:00:00:00
1373: EAPOL: PMKSA caching was used - skip EAPOL
1510: rgst3dc87c: Control interface command 'RECONNECT'
1512: rgst3dc87c: Control interface command 'RECONNECT'
```

第 1512 行之后到日志结束，没有 `Associated with`、`Trying to associate`、
`CTRL-EVENT-CONNECTED`。日志以 `CTRL-EVENT-TERMINATING` 结束（平台停止时关闭客户端）。

### 2.3 抓包计数

`trace.pcap` 共 906 帧，其中关联类帧 4 个（两次关联，每次一个请求与一个响应）、
EAPOL 帧 8 个（每次关联 4 个）。连发的两条 `RECONNECT` 没有对应的关联帧。

## 3. E1 命令被接受但效果未发生 `20261112-121639-895333`

hold 模式，接口 `rgst895333`。

### 3.1 时间线（会话记录）

| 时刻 | 事件 | 结果 |
|---|---|---|
| T0 | 基线 `status` / `pmksa` | `COMPLETED`；`1 02:00:00:00:00:00 44320f36a2edda3a33d20dcd47b1d2a6 43199 0`；PID 833621 |
| T1 | `DISCONNECT` | `OK` → `DISCONNECTED` |
| T2 | `pkill -f "hostapd.*895333"` | `hostapd 已停止`，时间 `2026-11-12T12:16:47-06:00` |
| T3 | `RECONNECT` | `OK` |
| T3+1 s | `status` | `AUTHENTICATING` |
| T3+2…14 s | `status` | `SCANNING`（未回到 `COMPLETED`） |
| T3+15 s 起 | `status` | 命名空间已不存在，采样无法继续 |

观测窗为 14 秒，未到 20 秒；平台在 AP 进程消失后清理了命名空间。

### 3.2 `sta.log` 关键行原文

```text
1044: rgst895333: CTRL-EVENT-CONNECTED - Connection to 02:00:00:00:00:00 completed [id=0 id_str=]
1083: rgst895333: CTRL-EVENT-DISCONNECTED bssid=02:00:00:00:00:00 reason=3 locally_generated=1
1351: rgst895333: Control interface command 'RECONNECT'
1403: rgst895333: SME: Trying to authenticate with 02:00:00:00:00:00 (SSID='ReconnectGuardLab' freq=2412 MHz)
```

全程 `CTRL-EVENT-CONNECTED` 只有 1 次（第 1044 行，基线那次）。
接受命令之后只有一次认证尝试（第 1403 行），日志以
`rgst895333: CTRL-EVENT-TERMINATING` 结束。

### 3.3 `error.json` 原文

```json
{
  "classification": "environment_error",
  "error": "WPA3 connection timeout"
}
```

### 3.4 抓包计数

`trace.pcap` 共 87 帧，关联类帧 2 个、EAPOL 帧 4 个，即基线那一次关联；
`RECONNECT` 被接受之后没有新增关联帧。

## 4. 复现命令

```sh
cd /home/chen/Projects/rg-jiang-20261112
sudo bash scripts/start.sh --client stock --probe connect --hold   # 终端 A
sudo ip netns list                                                 # 终端 B
sudo ip netns exec rg-sta-XXXX iw dev                              # 取 rgstXXXX
sudo ip netns exec rg-sta-XXXX wpa_cli -i rgstXXXX status
sudo ip netns exec rg-sta-XXXX wpa_cli -i rgstXXXX disconnect
sudo ip netns exec rg-sta-XXXX wpa_cli -i rgstXXXX reconnect
sudo ip netns exec rg-sta-XXXX wpa_cli -i rgstXXXX pmksa
sudo bash scripts/stop.sh
```

E1 与 E3 的唯一差别：断开后执行 `pkill -f "hostapd.*<后缀>"` 再发 `reconnect`。
