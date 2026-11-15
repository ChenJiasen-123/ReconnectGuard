# 认证路径未分类问题的修正与复核

## 结果

原实体缓存对照 40 轮中的 7 个 unknown，均由严格的抓包顺序检查触发。
它们具有匹配的 M1/M2/M3/M4，但 pcap 记录为 M1、M2、M4、M3。
新规则用本轮双端日志和 nonce 绑定核对，不改写抓包顺序或时间戳。

| 原 40 轮 | v1 | v2 只读重分析 |
|---|---:|---:|
| 缓存复用 | 8 | 10 |
| 完整 SAE | 25 | 30 |
| 未分类 | 7 | 0 |
| 保留的 M3/M4 抓包倒序 | 未单独表示 | 7 |

分组结果为 A 的 10 轮缓存复用，B/C/D 各 10 轮完整 SAE。
33 轮按抓包顺序匹配；另外 7 轮按帧身份与双端日志互证匹配。
旧 result.json、旧派生报告和原始输入均保持不变，逐文件检查哈希。
新旧结果见 [重分析摘要](study-data/auth-evidence-review-20261115/reanalysis-summary.json)。

之后独立执行新批次 cache-7c221e270b4f4e5f95e26d538cdc13cf，每组 3 轮：

- 12 轮恢复连接，原单包通信探针 12/12 通过。
- A 的 3 轮确认缓存复用；B/C/D 共 9 轮确认完整 SAE。
- 11 轮按抓包顺序匹配；1 轮再次出现相同倒序，经本轮双端日志确认。
- 12 轮重连后的五包诊断严格发出 60 包，收到 60 个回复。
- 12 轮基线的单包和五包检查也均通过，清理均成功。

逐轮结果见 [独立复测](study-data/auth-evidence-review-20261115/independent-validation.json)。
这 12 轮使用新增诊断条件，单独报告，不合并替换原 40 轮统计。

## 1. 为什么抓包会出现倒序

本实验在 AP 所用 PHY 上创建 monitor 接口。7 轮旧数据中，M1/M3 的 Radiotap
带 TXFlags，是本机发送状态记录；M2/M4 带 RXFlags，是接收记录。
客户端日志均先记录收到 M3，再记录发送 M4；AP 日志随后记录收到 M4 和 PTKINITDONE。

例如 20261115-093248-392d3b：

| 证据 | 观测 |
|---|---|
| pcap 第 47 帧 | M4，接收记录 |
| pcap 第 48 帧 | M3，本机发送状态记录，比第 47 帧晚约 3.01 ms |
| sta.log 第 1778 行 | 收到目标 AP 的 M3 |
| sta.log 第 1783 行 | 发送 M4 |
| ap.log 第 1177 行 | 收到目标 STA 的 M4 |
| ap.log 第 1179 行 | 进入 PTKINITDONE |

Linux 6.6 的 [mac80211 status.c](https://github.com/torvalds/linux/blob/v6.6/net/mac80211/status.c)
在发送状态处理路径中向 monitor 交付发送帧。
[Radiotap TX flags 定义](https://www.radiotap.org/fields/TX%20flags.html)说明该字段用于发送帧属性。
结合本批帧元数据与日志，我们将现象解释为本机发送状态记录与接收记录交付顺序的差异。
这与源码机制一致，但没有进行运行时内核追踪，不能进一步指定固件或 USB 队列的具体延迟来源。

不能从 pcap 文件顺序推导设备先处理 M4 再处理 M3，也不能把时间戳重新排序后称为真实空口时间。
具体帧号、日志行号和输入哈希见 [7 轮倒序证据](study-data/auth-evidence-review-20261115/reviewed-inversions.json)。

## 2. v2 的判定规则

认证路径与握手观测质量分开保存：

- auth_path.value：full_sae、pmksa_reuse 或 unknown。
- auth_path.handshake.status：按抓包顺序匹配、倒序经互证匹配，或证据不足。
- capture_order：原观测消息顺序。
- protocol_frame_refs：按消息类型列出参考帧号，不代表重写后的抓包。
- endpoint_corroboration：本轮双端日志引用。

所有分类仍要求同一恢复窗口、同一 AP/STA、完整四类消息、正确方向、
M1/M2 重放计数一致、M3/M4 重放计数一致且增加、M1/M3 ANonce 一致、
非零 SNonce 和零 M4 nonce。完全相同 EAPOL 载荷的重传可去重；
多个不同握手身份有歧义时不选一个凑合通过。坏 FCS、坏 PLCP 和失败 TX 记录不用于握手匹配。

只对 M1、M2、M4、M3 这一种相邻倒序增加条件分支。必须同时满足：

1. M1/M3 明确为本机 TX 状态，M2/M4 明确为 RX，且捕获报告零内核丢包。
2. STA 日志在同一断开—连接窗口中，唯一且依次出现收到 M1、发送 M2、收到 M3、发送 M4、密钥协商完成。
3. AP 日志在同一目标 STA 的断开—连接窗口中，唯一且依次出现收到 M2、收到 M4、PTKINITDONE。
4. 帧中的 ANonce/SNonce 必须与上述窗口内双端日志对应，不能使用旧连接日志。

缺少任一条件仍返回 unknown，并保存具体拒绝原因。没有采用任意毫秒容差、
任意重排或“最终 ping 通了就算握手正常”的规则。

握手匹配通过后，还要分别检查认证路径：完整 SAE 需要双方成功的 commit/confirm；
缓存复用需要本次关联 PMKID 与本轮缓存命中及匹配日志一致，且窗口未见 SAE。
SAE 错误响应不计为成功认证。

这些结果是认证路径的观测证据。没有验证握手 MIC，也没有由此证明授权时序或完整协议安全。
security_verdict 保持 not_evaluated。倒序的 M3 时间不能用于估计实际 M3 发送时刻或 M3→M4 时延。

## 3. 单包探针与固定预算诊断

原 40 轮仍记为：连接恢复 40/40，单包探针 39/40。
20261115-092925-c8f731 的认证路径现可确认完整 SAE，但其单包探针仍为失败。
认证路径的修正不会改变这个通信结果。旧条件追加三轮均通过；根因仍未确认。

新 evidence_study.py 保留原单包检查，并在每次基线及重连后执行固定五包诊断。
无论单包是否通过，都执行相同预算，不能一直重试到成功。

- 五包诊断：ping -c 5 -i 0.2 -W 1，进程最多等待 5 秒。
- 记录实际发包数、收包数、回复序号、状态、AP 授权快照和邻居表。
- status=pass 要求实际发出且收到全部 5 包，并保持连接及 AP 授权。
- reachability=pass 只表示本诊断窗口有回复，不能等同于零丢包。
- 超时或执行错误标为 collection_error；实际发包不符合预算标为 invalid_budget。
- 原单包失败不会被后续成功覆盖。

新批次的 60/60 回复验证了本次有限窗口内通信正常，仍不能解释过去那一个未回复的数据包，
也不能证明设备在任意条件下都不会丢包。

## 4. 保留的预实验错误

两批预实验完整保留，未用成功复测覆盖：

- cache-e08c101ad6e04158b174dc11aefe8ad4：12 个案例中 1 个在重连前被缓存校验拒绝。
  条目身份未变，剩余寿命从 43199 秒变为 43198 秒；v1 整行比较将正常倒计时误判为修改。
  现按 peer、PMKID、opportunistic 标志比较，要求条目仍有效且倒计时下降不超过实际查询间隔的向上取整。
  缓存变更、延期、异常大幅减小、到期或无法解析仍拒绝。索引不作为缓存身份。
- cache-af360a9a771c4575adf4b700afae2f5d：12 轮均完成连接和认证分类，但 2 轮诊断实际发出 6 包。
  原参数组合 -c 5 -w 3 并不保证最多发送 5 包。
  [iputils 20221126 发送循环](https://github.com/iputils/iputils/blob/20221126/ping/ping_common.c)
  在设置 deadline 时不会按普通计数上限停止发送。现去掉 -w，并在进程外设超时，同时核对实际数量。

这三条记录属于测试器条件或预算问题，不作为设备缺陷。
原始摘要见 [预实验错误记录](study-data/auth-evidence-review-20261115/preserved-preflight-errors.json)。

## 5. 复现与检查

```sh
# 新输出目录必须不存在；旧输入目录不会被改写。
sudo python3 -m observer.reanalysis \
  results/cache-5d9d6fcbe330436c953dfebd2f085ece \
  --output results/cache-review-v2-new

# 两块闲置 mt7921u 网卡，主机通过独立网线管理。
sudo python3 evidence_study.py --repeat 3 --ap-iface wlan1 --sta-iface wlan2
python3 -m unittest discover -s tests -q
```

原始材料和新派生报告已保存到树莓派 results/，本地副本位于
results/evidence-review-20261115/。Git 只提交代码、说明及精选证据。
回归覆盖旧连接日志、错误 peer、错误 nonce/重放计数、缺帧、任意重排、
多握手歧义、真实重传、倒序的独立认证证据、错误 SAE 状态、缓存倒计时、
诊断预算超出及主探针失败不被后续结果覆盖。

最终检查：Linux 143 项全部通过；macOS 138 项，135 通过、3 项因平台依赖跳过。
Python 编译检查和 git diff --check 通过。额外 hwsim 正常重连案例
20261115-110606-2613b3 完成，分类为缓存复用，通信及清理通过。
实验后无残留 hwsim 或实验命名空间；eth0 管理连接正常，wlan1/2 恢复 DOWN 及原软件禁用状态。
