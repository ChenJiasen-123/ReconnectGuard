# 缓存、失败恢复和状态触发研究（2026-11-15）

后续补充见 [最短反例、多动作测试与授权时序](verification-study-20261115.md)。
该补充保留本文的原始实验结果，另行增加有限动作域的最短证书、合法随机基线，
以及有原生驱动证据的局部授权属性；本文中的“尚未完成”描述对应本轮初始状态。

## 范围与复现环境

本轮在提交 405c257 上实现并执行五个方向的首轮研究。新增代码与本文一同交付。
这不是对任意设备、全部中断时长或通用自动序列探索的验收声明。

- 主机：Raspberry Pi 5，Debian 12 aarch64，192.168.1.101。
- 实体实验：两块 MT7921AU，系统 hostapd / wpa_supplicant，2.4 GHz / Wi-Fi 6，SAE、CCMP、PMF Required。
- 失败注入：隔离 hwsim，Framework AP；显式阻止操作已有 hwsim。仅解除本次创建 PHY 的 rfkill，不解除板载或实体网卡的禁用。
- 恢复观察窗口固定 20 秒。这个值是实验预算，不是协议规定的超时。
- 环境错误、采集错误、恢复超时、清理失败分别记录。授权时序结论仍为 insufficient_evidence，不能由恢复成功推导安全通过。
- 完整材料保留在树莓派项目 results/；本地汇总、图和构建说明保存在 results/research-20261115/，均不提交 Git。

## 1. 四组缓存对照

运行方式：

```sh
sudo python3 research.py --repeat 1 --ap-iface wlan1 --sta-iface wlan2
sudo python3 research.py --repeat 10 --ap-iface wlan1 --sta-iface wlan2
```

固定客户端主动 DISCONNECT → 干预 → 显式 RECONNECT。缓存操作不重启进程。
干预前确认 DISCONNECTED，读取双端 PMKSA，操作后再次读取并检查效果；若没有预期基线缓存或清除未生效，该轮不能作为有效条件。最新代码还检查干预期间是否意外重连。

先完成四组预实验，再完成 40 轮探索批次 cache-e498c8d6725a4604b5c6766324f33e49。
探索批次同时进行了依赖编译，故其耗时不作为固定负载性能结论。随后补充 4 轮验证最新干预检查，再在不并行编译的条件下进行独立 40 轮复测，逐轮轮换组别顺序。

固定负载复测批次：`cache-5d9d6fcbe330436c953dfebd2f085ece`。

| 组别 | AP / STA缓存 | 连接恢复 | 通信探针通过 | full_sae / pmksa_reuse / unknown | 连接恢复中位数 ms |
|---|---|---:|---:|---|---:|
| A | 保留 / 保留 | 10/10 | 10/10 | 0 / 8 / 2 | 153.275 |
| B | 清除 / 保留 | 10/10 | 10/10 | 8 / 0 / 2 | 289.139 |
| C | 保留 / 清除 | 10/10 | 10/10 | 8 / 0 / 2 | 176.515 |
| D | 清除 / 清除 | 10/10 | 9/10 | 9 / 0 / 1 | 174.351 |

D 组一轮（20261115-092925-c8f731）连接及安全配置恢复，但单包、1秒等待的 ICMP 探针无回复，记录为 data_plane_failed。该轮保留，不用后续成功替换。它说明连接状态和数据通信需要分别检查；还不能据此认定永久失效或安全漏洞。随后以相同 D 条件、相同探针预算追加三轮，三轮均完成并通过通信探针；异常尚未稳定复现。追加三轮单独记录，不并入原10轮。

探索与固定负载复测批次的直接机制证据一致：A 组每轮捕获 1 个去重关联序列；B 组每轮均看到首次关联响应 status=53，随后有第 2 个关联序列，恢复成功。C、D 每轮捕获 1 个关联序列。序列号去重是可见报文计数，不保证等于客户端内部连接尝试次数。

认证分类严格依赖目标 MAC、恢复窗口、双方 SAE commit/confirm、完整四次握手或 PMKID/日志互证。unknown 保留；不将预期条件直接转换成结果标签。各轮 cache-intervention.json 保存原始缓存表、操作和验证状态。固定负载复测40轮的干预均验证生效，40轮抓包均报告零内核丢包。

A 的缓存复用和 B 的完整 SAE 时间线已导出为 auth-timelines.json/png，引用具体 run_id 与帧号。缓存失配并未使这个客户端永久失效；在正常关联拒绝路径中，它能够放弃旧缓存并重新认证。该结论只覆盖本次受测实现与条件。

## 2. 已知缓存关联拒绝案例与检测能力

研究依据：[上游测试](https://lists.infradead.org/pipermail/hostap/2026-July/045377.html)、[v3 修复说明](https://lists.infradead.org/pipermail/hostap/2026-July/045376.html)、[维护者对缓存清理范围的讨论](https://lists.infradead.org/pipermail/hostap/2026-July/045372.html)。

原理是缓存关联请求尚未完成时，AP 以 reason=9 Deauthentication 拒绝，而非正常关联错误响应。它不同于原有“连接建立后 AP Deauth”案例。

成对客户端来自 Framework 仓库附带的同一 2.10 派生源码树，关闭 CONFIG_FRAMEWORK_EXTENSIONS，使用相同配置、编译器和路径构建。before 为该构建基线；after 为本地窄范围回移。没有声称 2026 年上游补丁能原样应用到 2.10，也没有把派生基线称作官方发行包。

本地回移约束：仅当前 AP、非本地事件、reason=9、WPA_ASSOCIATING、SAE 缓存关联；只清除此 AP 对应缓存。地址未知则不清除。修改与构建脚本见 scripts/research/backport_sae_reject.py 和 build_pair.sh；补丁差异、二进制哈希、编译配置与工具版本保存在 work/scoped-backport.patch、work/paired-build.json。

| 条件 | 重复次数 | 在20秒内恢复 | 自动检测 |
|---|---:|---:|---|
| 正常接受缓存重连（before） | 2 | 2 | 正常完成 |
| 用关联响应 status=53 拒绝（before） | 2 | 2 | 正常完成并回退 SAE |
| 缓存关联期间 reason=9 拒绝（before） | 2 | 0 | 两次 observation_timeout，recovery=fail |
| 相同 reason=9 条件（after） | 2 | 2 | 正常完成并观察完整 SAE |

最终目标过滤后的材料分别为 /tmp/rg-paired-assoc-final.jsonl、rg-paired-before-final.jsonl、rg-paired-after-final.jsonl；正常对照为 rg-paired-accept.jsonl。本地有同名副本，每行给出完整 run_id 目录。每轮 fault-injection.json 记录触发所依据的事件 seq。

失败和修复的报文时间线已导出为 bug-observations.json / bug-timelines.png，保留每个点对应的原始帧号。

短复现序列：正常连接建立缓存 → DISCONNECT → AP PMKSA_FLUSH → RECONNECT → 对缓存关联请求持续返回 reason=9 Deauth。它已完成正常/错误响应/修复版本对照，但没有经过全局序列最小化，不能称为已证明的全局最短失败序列。

这是已知失败模式的受控复现和检测器验证，不是新漏洞报告。回移代码用于单 AP 实验，没有完成多 AP/漫游回归，不应作为生产修复直接部署。

## 3. 重连耗时分解

从已有实体批次 comparison-c79316fa880e403e99b44bec4b6aceb4 的 30 轮读取原始帧、事件与数据探针。没有用 mock 时间。输出 stage-timing.json/csv/png。

| 路径 | 断开到开始认证/关联 | 认证/关联到M4 | M4到连接事件 | 连接事件到通信探针完成 |
|---|---:|---:|---:|---:|
| 客户端显式重连 | 35.094 ms | 95.250 ms | 23.390 ms | 304.687 ms |
| AP Deauth 自动恢复 | 2324.266 ms | 100.170 ms | 68.949 ms | 289.226 ms |
| AP Disassoc 自动恢复 | 2319.327 ms | 99.445 ms | 68.652 ms | 292.631 ms |

各列独立取中位数，不能将列之和称为总耗时中位数。第一认证/关联起点来自目标链路抓包，连接时刻来自控制事件接收时间，最后一列包含探针启动和执行开销，不是“通信权限首次获得”的时刻。

结果支持：在本批条件下，约 2.5 秒与约 156 毫秒的主要差异出现在认证/关联开始之前。不能把差异归因于缓存复用耗时，也不能只凭这些时间定位具体扫描/退避算法。

不同来源用同机 UTC/单调时钟偏移对齐；接收排队、调度和固件可见性会影响边界。缺失或倒序区间输出 null，而不填 0。命令：`sudo python3 -m observer.timing results/comparison-实际编号`。

## 4. 阶段定向报文丢失

使用 hwsim 测试控制，不丢弃 IP 包：关联阶段暂时不将目标管理帧交给 AP 处理；EAPOL 阶段暂时抑制 AP 的 EAPOL 发送。控制由实际 MGMT-RX 或 EAPOL-TX 事件触发，保存事件 seq 和干预记录，之后关闭干预并等待自动恢复。

| 作用点 | 中断预算 | 观察断开到恢复 | 结果 |
|---|---:|---:|---|
| 关联请求处理 | 0.2秒 | 219.139 ms | 恢复、通信通过 |
| 关联请求处理 | 2秒 | 2556.515 ms | 恢复、通信通过 |
| 关联请求处理 | 6秒 | 9692.621 ms | 恢复、通信通过 |
| AP EAPOL发送 | 0.2秒 | 1129.093 ms | 恢复、通信通过 |
| AP EAPOL发送 | 2秒 | 2140.479 ms | 恢复、通信通过 |
| AP EAPOL发送 | 6秒 | 6460.002 ms | 恢复、通信通过 |

每格一次，属于探索矩阵。共同离线观测器确认六次干预都来自目标链路，清理均成功。结果材料 rg-loss-matrix.jsonl、rg-loss-observations.json。

两种作用点的方向/机制不同，不能把时间差严格归因于协议阶段。这不是全向射频干扰实验。当前六格均恢复，没有发现持续失效，因此没有伪造“丢失导致的最小失败序列”；本轮可重复失败序列来自第2节。还不能据此确定最大可恢复中断时长。

## 5. 固定、无反馈扰动与状态触发

`model/protocol.py` 增加有事件依据的 AUTHENTICATING、ASSOCIATING、KEY_ESTABLISHING 阶段，故障控制器用阶段守卫决定允许的操作。它补充流程 EFSM，不把推测认证阶段写成原始事实。

实验统一使用同一 before 客户端、缓存失配前置条件、reason9 动作、控制器、观测器、20秒恢复窗口，每组3轮：

- fixed：固定在第二个目标认证/关联请求后启用故障。
- unguided：固定种子0/1/2，在第1—6个请求中选择启用位置，不根据当前阶段选择启用位置。
- guided：依据已观测关联阶段选择故障时机。

三组都经过共同执行前置检查；fixed/unguided 描述的是生成/触发策略，没有取消底层必要约束。不是拿不安全执行器与带约束执行器比较。

| 策略 | 有效故障注入 | 已知失败检出 |
|---|---:|---:|
| fixed | 3/3 | 3/3 |
| unguided | 2/3 | 2/3 |
| guided | 3/3 | 3/3 |

独立观测模块 observer/experiment.py 从同一 pcap/journal 计算阶段集合、可见关联序列、实际拒绝原因及故障目标匹配，不以引导模型自己的转换日志当覆盖率。

fixed/guided 都经过 open_system_auth、assoc_req、deauth；unguided 的一次未有效注入试验成功回退，反而经过 SAE 和完整四次握手，集合更广。因而“覆盖更多阶段”不等于“检出更多错误”。固定脚本在这个稳定场景同样有效，不能声称已证明状态机更优。

当前完成的是一个已知案例的小规模触发策略比较。通用多动作序列生成、更多缓存上下文和缺陷集合、统计显著性以及发现时间分布尚未完成。状态机目前额外提供阶段前置条件、事件驱动触发、统一证据和序列合法性检查；其普遍检出优势仍是待验证假设。

## 代码、测试与未混淆的状态

- 新入口 research.py、rejection.py；新分析 observer/timing.py、observer/experiment.py。
- 新缓存干预 backends/interventions.py；协议阶段 model/protocol.py。
- scripts/research/ 提供成对构建、故障矩阵和图表复现方式。
- 123 项 Linux 回归全部通过；macOS 118 项，115通过、3个Linux/Scapy相关跳过。
- 保留了早期外部管理帧转发不完整、rfkill 与广播目标误判的调试失败目录。最终对照结果使用修复后的目标校验，未删除失败以提高成功率。
- 原始事件和快照不改写，独立派生报告另存；安全 verdict 保持 not_evaluated。
