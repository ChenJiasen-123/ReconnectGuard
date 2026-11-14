# 实体测试复测与认证证据集成（2026-11-15）

## 已交付

实体平台首个提交 `9f6b19e` 已推送。后续本次提交增加监听清理修复、认证路径证据分析及 Runner / comparison 自动接入。源码、测试和文档随本文件提交。原始抓包、配置和实验日志留在树莓派 results/，不入 Git。

## 实际复测

主机 `192.168.1.101`，目录 `/home/izumedonabe/Projects/ReconnectGuard`，硬件与软件条件见 physical-rpi5-validation-20261115.md。仍使用发行版 hostapd AP，不与 Framework hwsim 的批次合并统计。

1. 提交 9f6b19e 后启动的 comparison-3d83e7c4ddc84b4da74e23b475580df7 在第 4 轮遇到监听线程 1 秒内未退出。4 次连接均恢复，但批次按清理错误停止。失败记录保留，接口已还原。
2. 监听等待预算从 1 秒改为 5 秒。新增测试用真实线程模拟停止后还需 1.1 秒写入的情形；等待有界，超时仍报告清理失败。
3. 在 9f6b19e 加上述清理修复的源码上，comparison-c79316fa880e403e99b44bec4b6aceb4 完成三种案例各 10 次，共 30 次。该批次不是未修改的 9f6b19e，逐轮源码哈希已保存。
4. 每轮核对均通过：连接恢复、独立通信探针、同一客户端进程、实际 Wi-Fi 6、SAE/CCMP/PMF、运行配置哈希、清理及目标抓包。30 轮内核丢包均为 0。AP 两组分别捕获受保护 Deauth / Disassoc 各 10 帧。

| 案例 | 完成 / 恢复 / 通信恢复 | 观测恢复时间中位数 ms | 范围 ms |
|---|---|---:|---:|
| 客户端主动断开 | 10/10 | 155.575 | 143.331—185.355 |
| AP Deauth | 10/10 | 2519.058 | 2439.289—2599.075 |
| AP Disassoc | 10/10 | 2508.470 | 2426.737—2607.489 |

时间是本机控制事件接收间隔，包含调度等开销。三组显式/自动恢复机制不同，不能将差异单独归因于断开动作或缓存。

## 认证路径分析

`observer/postprocess.py` 基于以下证据生成派生报告：

- 基线中的 AP 和 STA MAC 对。附近其他链路的 SAE/EAPOL 不参与判定。
- 状态机接受的断开与恢复事件对应原始 journal 的 UTC 接收时间。缺失、跨尝试或多个重试请求时保留 unknown。
- 恢复窗口中的四次握手方向、顺序、重放计数配对及 M1/M3 ANonce 一致性。
- 缓存复用还要求关联请求携带的 PMKID 与本次断开后、连接完成前的缓存命中和 matched PMKID 日志逐字节相同；窗口内无 SAE，抓包报告内核丢包为零。
- 完整 SAE 要求双方的 commit 和 confirm，随后有一致的四次握手。仅部分 SAE 或缺握手阶段不判为完整认证。

上述 30 轮的离线分析全部分类为 pmksa_reuse，每组 10 次。每个结论有明确帧号、日志行号和输入文件 SHA256。派生文件为 authentication-analysis.json；批次认证汇总为 authentication-summary.json / csv。旧批次的 result.json、summary.json 和原始快照不被改写，因此其中 auth_path 仍可为 unknown，应结合派生报告阅读。

这是一份可审计的观测路径分类，不是握手 MIC 校验或安全证明。零内核丢包不能证明固件向主机交付了所有空口报文。控制事件接收时间可能排除窗口边缘的帧，遇到证据不足应保留 unknown。

## 自动集成

```sh
sudo python3 comparison.py --backend real --radio physical \
  --ap-iface wlan1 --sta-iface wlan2 --repeat 10 \
  --evaluator observer.oracle:Oracle --analyze
```

Runner 在清理并保存原始产物后分析。新 result.json 的 auth_path 和 auth_path_evidence 来自派生报告；Context 与快照不改写。分析异常使 execution_status=failed、analysis_status=failed，保留已观测的恢复事实和错误信息。对照 CSV 根据各轮真实分析结论填写，组内多种结论时为 mixed。

接入后的 comparison-75e881ed90ce4b1aac62354f0825de63 三条真实流程均完成，auth_path_counts 各为 pmksa_reuse:1，核对无抓包/配置/清理错误。之后针对 CSV 汇总及分析器源码哈希记录补充的变更通过回归测试。

最新 Linux 回归 116 项全部通过；macOS 111 项测试中 109 通过、2 个 Linux 模块跳过。测试涵盖旧连接日志污染、其他网络报文、错误 nonce/重放计数/方向、缺阶段、丢包、原始产物不变、分析失败保留恢复结果以及 CSV 结论一致性。

## 仍然明确保留的边界

- state_consistency 在 30 轮中均为 pass；authorization_timing 仍为 insufficient_evidence；security_verdict 为 not_evaluated。
- 尚未获取可信的密钥安装和端口授权时序。不会用 AP-STA-CONNECTED、STATUS COMPLETED、捕获到加密数据帧或事后 ping 成功替代它们。
- 真实重试继续禁用。本次解决了正常路径和认证证据闭环，没有把接收顺序轮次当作每次协议重传的准确边界。
- 5 GHz 和 Framework 修改版 AP 的实体兼容性未在本次验收范围内。

本地汇总副本在 results/rpi5-20261115-final/（Git 忽略目录）；完整原始材料按以上批次目录保留在树莓派。
