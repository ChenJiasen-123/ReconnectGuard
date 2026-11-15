# 最短反例、多动作测试与授权时序（2026-11-15）

本轮增加可运行代码，并在树莓派的隔离 hwsim 中执行实验。
代码基线为 6692b44；Framework 固定为 7a173c1824caa95a848f351fe6eab2d5e5b389e1。
主机为 Raspberry Pi 5、Debian 12、内核 6.6.74。管理连接使用 eth0。
本轮结果不包含实体 MT7921AU 的授权插桩验证。

## 1. 最短失败序列

### 搜索域

`model/sequences.py` 定义以下动作。每个动作的代价为 1。

| 动作 | 前置条件 | 效果 |
|---|---|---|
| disconnect | 已连接 | 主动断开 |
| flush_ap | 已断开、AP 缓存存在 | 清除并查询 AP PMKSA |
| flush_sta | 已断开、STA 缓存存在 | 清除并查询 STA PMKSA |
| arm_reject | 已断开、拒绝策略关闭 | 对目标缓存关联持续发送 reason=9 Deauth，允许完整 SAE 回退 |
| allow | 已断开、拒绝策略开启 | 关闭拒绝策略 |
| reconnect | 已断开 | 请求重连；等待恢复事件并检查专用链路通信 |

初始条件为已连接、双端 PMKSA 均存在、拒绝策略关闭。每轮重新创建实验环境，
验证初始缓存和通信。序列必须以 reconnect 结束。成功重连后可以再次 disconnect，
因此支持多个连接周期。深度上限为 6，共有 76 条合法终止序列。

失败定义为：在上述有效初始条件下，执行 reconnect 后 20 秒内没有匹配的恢复事件。
环境错误、动作拒绝、通信探针失败和清理错误单列，不作为已知缺陷检出。
观察窗口是实验约束，不是协议要求。

### 搜索与真实重放

广度优先搜索不合并路径。模型按缓存与拒绝策略预测已知失败：
当 STA 保留缓存且拒绝策略开启时，缓存关联不能成功，旧客户端不能回退 SAE。
模型预测只是搜索依据；真实重放独立采集事件、抓包和故障记录。

找到的最短候选为：

```text
disconnect → arm_reject → reconnect
```

长度为 3。所有严格更短的合法终止序列只有 `disconnect → reconnect`。
该序列真实执行 1 次并通过；最短候选真实执行 2 次，均恢复超时。
证书保存在 [shortest-certificate.json](study-data/verification-20261115/shortest-certificate.json)。

这里有两个限制必须保留：

1. 这是所定义动作域和代价下，确定性失败模型的全局最短反例。真实设备的有限重放不能证明所有非确定执行均失败。
2. arm_reject 是持续拒绝策略，不是单个无线帧。该策略即使在 AP 缓存保留时也会拒绝缓存关联。因此最短序列省去了 flush_ap。它不等于“自然丢失 AP 缓存场景的最短无线帧序列”。

基线连接、等待时间、控制器内部发帧均不计入操作数。改变动作集合、初始状态、
失败定义或代价后，必须重新搜索。不能将普通删减得到的局部最小序列称为全局最短。

## 2. 通用多动作生成与评估

这里的“通用”指上述 6 种动作可组合并可执行多个连接周期。
它不表示支持全部 Wi-Fi 操作，也不表示穷尽任意长度。

`sequence_study.py` 直接执行合法动作序列，不依赖原固定五步案例表。
每步保存操作起止时间和快照；缓存操作保存原始表及清除确认；每次重连检查通信。
发生失败后停止本条序列，不将未执行后缀计入反馈或覆盖。

生成策略：

- fixed：循环执行 6 条固定的正常缓存及拒绝序列。
- random：对固定目录随机替换一个操作；记录不合法提案，不执行它们。
- random-valid：从全部合法终止序列中均匀抽样，允许重复；不使用执行反馈。
- guided：在尚未使用的合法序列中，选择预计新增抽象边最多的序列；仅用已完成操作更新反馈。

guided 使用连接、缓存、拒绝策略上下文。它不使用独立观测器的协议覆盖结果作为选择依据。
覆盖统一由 `observer/experiment.py` 从真实抓包和 journal 计算，分母为同一组 10 个阶段。
不能把生成器自己的边数当作协议覆盖率。

首轮无约束变异在 6 个提案中仅 1 个合法。它适合展示合法性检查，作为效率对照偏弱。
因此追加 fixed、random-valid、guided 的重复批次。每种策略每批 6 个提案，
单条最多 6 个操作，恢复等待均为 20 秒。各策略实际操作数、执行时间另行报告。
这不是相同无线帧数或相同墙钟时间的预算。

最终每种策略执行 3 个批次，共 18 个提案。random-valid 使用种子 1、2、3；
fixed 和 guided 的选择规则是确定的，种子不会改变它们的计划，3 个批次是重复测量。

| 策略 | 合法 / 提案 | 失败序列 | 实际尝试 / 完成操作 | 独立阶段覆盖 | 累计单轮耗时 |
|---|---:|---:|---:|---:|---:|
| fixed | 18/18 | 6 | 57/51 | 9/10 | 206.30 秒 |
| random-valid | 18/18 | 5 | 95/90 | 9/10 | 190.12 秒 |
| guided | 18/18 | 9 | 87/78 | 9/10 | 266.16 秒 |

失败操作计入尝试数，不计入完成数。三组没有环境、动作、独立观测或清理错误。
这里的耗时包含每条序列的初始化、观察等待和清理，不包含批次间调度；它不是纯重连延迟。

各批首次检出的提案位置：fixed 为 5/5/5，random-valid 为 2/6/1，guided 为 2/2/2。
对应的首次检出累计单轮耗时分别约为 43.98/44.20/43.97 秒、
31.79/49.16/24.79 秒、29.46/29.64/29.63 秒。
各批重置引导状态，不将批次串接后计算一个首次检出值。

guided 在本预算中产生更多失败序列，且三批均较早检出；random-valid 的一批更早。
三组覆盖相同的 9 类阶段，guided 的累计耗时也更长。结果不能单独证明普遍效率优势。

重复批次结果和逐条序列见
[sequence-evaluation.json](study-data/verification-20261115/sequence-evaluation.json)。
所有检出的失败序列都指向同一个已知缓存关联拒绝失败模式，不能算作多个缺陷。
本轮没有足够的实现、缺陷类别和随机种子来证明普遍检出优势。

一条已真实执行的多周期序列为：

```text
disconnect → flush_sta → arm_reject → reconnect → disconnect → reconnect
```

第一次重连使用完整 SAE 并通过通信探针。第二次重连再次带缓存，被拒绝后超时。
这说明同名 reconnect 的结果取决于此前的缓存和策略状态。

## 3. 授权时序属性、证明与实测

### 要检查的属性

本轮只证明和检查以下明确属性：

```text
对同一 peer：每次成功授权前，必须已经成功安装 CCMP-128 单播 PTK；
此安装之后、此授权之前，不得存在成功删除该 PTK 的事件。
```

形式为 `G(authorize_ok(peer) → previously_installed_and_not_deleted(peer))`。
成功指 nl80211 驱动 API 返回 rc=0。这里的“之前”按同一进程内单调时间及日志顺序解释。
AP、STA 分别检查，不跨进程比较时间。其他加密算法超出当前监视器范围。
“未删除”限定为没有记录到成功的目标密钥删除调用。内核隐式移除站点或固件内部
删除不在当前事件模型内；本属性不能证明授权时硬件仍持有该密钥。

它没有要求先发生某个推测的握手阶段，也没有用连接成功、ICMP 回复或 EAPOL-M4
代替密钥安装证据。端口撤权不会自动被当作密钥删除。

### 监视器正确性的归纳证明

对每个 peer，定义状态 K 为是否已有成功安装且未成功删除的 PTK，
V 为此前是否发生过违例。初始 K=false，V=false。

转移规则如下：

| 输入事件 | K 的后继值 | V 的后继值 |
|---|---|---|
| 成功安装目标 PTK | true | V |
| 成功删除目标 PTK | false | V |
| 安装/删除/授权调用失败 | K | V |
| 成功授权 | K | V 或非 K |
| 撤权 | K | V |

归纳不变量为：K 当且仅当历史中最近一次成功的目标密钥更新是安装；
V 当且仅当历史中至少一次成功授权没有满足上述条件。

空历史满足此不变量。安装和删除分别更新最近一次密钥操作；失败调用与撤权不改写它；
授权仅在 K=false 时将 V 置为 true，且 V 不会回到 false。因此五类事件均保持不变量。
按历史长度归纳，该监视器对任意有限的上述抽象事件序列均满足这个判定定义。
没有授权事件时返回 insufficient_evidence，避免无观测时给出通过结论。

`scripts/verification/certify_monitor.py` 另行枚举长度 0—5 的全部 3906 条事件序列，
与直接读取历史前缀的独立公式比较。全部一致。
该枚举是有界代码核对；归纳论证针对上述转移定义。两者均不能替代受测 C 实现到事件模型的完整精化证明。

### 原生证据与正负对照

`scripts/verification/instrument.py` 只修改 work/hostap-auth 实验副本。
在以下 nl80211 路径返回后记录 rc、单调时间、peer、操作类型、算法、索引和长度：

- AP / STA 的 set_key。
- AP 的 sta_set_flags。
- STA 的 set_supp_port。

不记录密钥内容。不修改 vendor 中的驱动源码和发行版程序。
报告包含驱动源文件哈希、AP/STA 二进制哈希、输入日志哈希和具体日志行号。
缺少插桩清单、哈希不匹配、日志倒序、无授权事件或不支持的密钥算法时，不能判为通过。

负对照必须显式指定 `--negative`。它通过 RG_AUTH_EARLY 在 AP 安装 PTK 前，
故意调用一次授权接口。正常运行不启用此分支。
这是检测器的故障种子，不是从原始设备发现的新漏洞。

最终有效对照：正常 2 轮均通过；负对照 1 轮失败。
每轮均完成基线连接和重连。正常轮 AP、STA 各检查到 2 次成功授权；
负对照 AP 检出 2 次提前授权，STA 检查通过。
它验证检查器能在“连接仍然成功”的条件下检出目标时序错误。
完整报告见 [正常轨迹](study-data/verification-20261115/auth-positive-property.json) 和
[负对照轨迹](study-data/verification-20261115/auth-negative-property.json)。
逐轮运行编号及结果见 [authorization-runs.json](study-data/verification-20261115/authorization-runs.json)。

成立条件是：插桩覆盖本次实际经过的密钥与授权调用；日志完整且未改写；
成功 API 返回代表规定的驱动效果。所证明的对象为事件抽象和已采集的驱动 API 轨迹。
没有证明固件内部状态、GTK/IGTK 时序、全部早期数据投递、任意漫游或全部 WPA3 执行安全。
现有通用 Oracle 的 authorization_timing 仍保留 insufficient_evidence；新结果单独记录在
result.json 的 verified_properties。不得将该局部属性写成完整 security_verdict=pass。

## 4. 复现

在已安装项目 Linux 依赖的主机中，从项目根目录运行。所有无线操作仅针对新建 hwsim。
不要并行运行两个实验入口。程序拒绝复用已有 hwsim，避免修改其他实验。

```sh
# 已有 Framework AP 构建和配置后，准备相同基线的 before 客户端。
# 此脚本也构建用于先前研究的 after 客户端，已有目录时会停止以保护材料。
bash scripts/research/build_pair.sh

sudo python3 sequence_study.py --mode shortest --depth 6
sudo python3 sequence_study.py --mode fixed --budget 6 --seed 1
sudo python3 sequence_study.py --mode random --budget 6 --seed 1
sudo python3 sequence_study.py --mode random-valid --budget 6 --seed 1
sudo python3 sequence_study.py --mode guided --budget 6 --seed 1

# 在已构建并带 .config 的 Framework 源码副本上制作插桩版本。
# 已存在 work/hostap-auth 时会停止；请保留旧证据并使用新的检出目录。
bash scripts/verification/build_auth.sh
sudo python3 authorization_study.py --repeat 2
sudo python3 authorization_study.py --negative --repeat 1
PYTHONPATH=. python3 scripts/verification/certify_monitor.py

# 指定实际批次摘要文件名；此操作不重新执行实验。
PYTHONPATH=. python3 scripts/verification/summarize.py \
  results/sequences-fixed-实际编号/summary.json \
  results/sequences-random-valid-实际编号/summary.json \
  results/sequences-guided-实际编号/summary.json --output evaluation.json

python3 -m unittest discover -s tests -q
```

默认使用 work/wpa-before。可通过 RG_STOCK_WPA_SUPPLICANT 指定其他实验客户端，
但改变客户端后必须重新验证失败模型，不能沿用旧反例结论。
完整原始材料位于树莓派 /home/izumedonabe/Projects/ReconnectGuard/results/。
本地副本位于 results/verification-20261115/，Git 中提交的是代码、说明和精选证据。

## 5. 验证与资源清理

- Linux：133 项测试全部通过。macOS：128 项，125 通过、3 项依赖 Linux/Scapy 而跳过。
- 新测试覆盖最短搜索的完整短路径集合、多周期合法性、浅深度搜索耗尽、跨周期拒绝统计、授权前缀公式及插桩来源缺失/不匹配。
- Python 编译检查、Shell 语法检查和 git diff --check 通过。
- 从固定版 vendor 驱动源文件重新应用插桩，所得文件与实际构建输入逐字节一致。
- 54 轮重复评估和 3 轮最短验证的元数据均使用同一 AP/STA 二进制对；哈希见 [binary-provenance.json](study-data/verification-20261115/binary-provenance.json)。
- 本地 raw/ 保存上述 57 轮及最终 3 轮授权实验的完整原始副本。原始材料不随 Git 提交。
- 实验结束后，hwsim 已卸载，实验命名空间已清理，eth0 管理连接正常。wlan0/1/2 仍为 DOWN 且保持原软件禁用状态。
