# Raspberry Pi 5 实体网卡验证（2026-11-15）

## 结果

上游已快进同步至 `1a7d0249248fd3d5f4ac4f3fc8395cc870239f5b`。本次在其上增加实体平台适配，工作区修改尚未提交。树莓派运行的是对应源码与修改，不应将本次实验标记成未修改的 1a7d024。

两块 FU-AX1801D 对应 USB ID 均为 0e8d:7961、驱动 mt7921u。已实际建立 Wi-Fi 6、WPA3-SAE、CCMP、PMF Required 连接，并在断开重连后验证专用链路通信。

## 环境与实验条件

- 主机：`izumedonabe@192.168.1.101`，Raspberry Pi 5，8 GB，Debian 12 aarch64，Linux 6.6.74+rpt-rpi-v8，Python 3.11.2。
- 管理接口：eth0。有线管理未纳入实验；板载 wlan0 保留原状态。
- AP：wlan1（90:de:80:3e:2a:b7）；客户端：wlan2（90:de:80:47:bf:27）。接口编号不应作为其他主机的固定映射。
- 软件：发行版 hostapd / wpa_supplicant。**本次 AP 不是 Wi-Fi Framework 修改版 hostapd**，不能与旧 hwsim 批次合并统计。
- 频段：2.4 GHz，信道 1，20 MHz，开启 802.11ax；客户端实际 STATUS 显示 wifi_generation=6，AP station flags 包含 HE、MFP。
- 两块 USB 网卡当前为 480 Mbps USB 连接；未修改 USB 插口或升级内核/无线固件。
- 每轮独立 netns、唯一实验 SSID、固定 SAE/CCMP/PMF；单轮内不重启客户端、不清空认证缓存。
- 安装了 hostapd、tcpdump、Scapy 与必要开发依赖。Framework 仓库已下载，但实体入口不依赖它，也没有宣称完成 Framework ARM 构建。

## 运行方式

服务器目录 `/home/izumedonabe/Projects/ReconnectGuard`：

```sh
sudo python3 comparison.py --backend real --radio physical \
  --ap-iface wlan1 --sta-iface wlan2 --repeat 1 --evaluator observer.oracle:Oracle
```

单案例可用 runner.py，使用相同的 radio / 接口 / evaluator 参数，并指定 --case。

物理模式目前仅支持 stock 客户端及两块 DOWN、无地址、由 mt7921u 驱动的接口。它记录原 MAC、PHY、NetworkManager 和 rfkill 状态，使用单个平台锁。结束后停止进程、删除专用命名空间、等待实体网卡返回，再删除 monitor 并恢复管理/禁用状态。清理错误会阻止下一轮，不自动重试。

## 批次与结果

目录均位于上述服务器项目的 results/ 下：

| 批次 | 结果 | 说明 |
|---|---|---|
| 20261115-010803-4f6341 | 连接和通信恢复成功，清理失败 | 首次接入发现 netns 删除后实体 PHY 返回存在异步延迟。原结果保留，随后恢复接口并修复等待逻辑；不计入成功批次 |
| comparison-ced1da21009646b28d2cb8a6ad676356 | 三路径各一次，3/3 完成 | 修复清理后验证，发现其中一轮抓包丢包 |
| comparison-a475ef159b824b398b5f46ebd2a52c30 | 三路径各十次，30/30 完成并恢复 | 连接与数据通信验证通过；19/30 轮报告内核丢包，不能宣称抓包完整 |
| comparison-f1dc65ab77e949cdb2fb52e6cd8adb7c | 三路径各一次，3/3 完成 | 增加 4 MiB 抓包缓冲区，按本轮 AP/STA MAC 过滤；三轮内核丢包均为 0 |

30 轮的观测断开至恢复耗时中位数：

| 案例 | 恢复 | 中位数 ms |
|---|---:|---:|
| 客户端主动断开、显式重连 | 10/10 | 144.588 |
| AP Deauth、自动恢复 | 10/10 | 2479.205 |
| AP Disassoc、自动恢复 | 10/10 | 2502.609 |

30 轮的状态一致性检查均为 pass，授权时序均为 insufficient_evidence；AP 两组各捕获到 10 个对应的受保护断开帧。

三组恢复机制不同；时间为本机接受的控制事件间隔，不是精确空口时延，不能归因于缓存。采集修改后仅复测三轮，未把之前 30 轮算作修改后的验证。

## 改进采集后的证据核对

三轮均通过：执行、连接恢复、独立数据通信探针、客户端进程连续性、实际 Wi-Fi 6、SAE/CCMP/PMF 配置、配置原件哈希、清理。Oracle 状态一致性为 pass；授权时序为 insufficient_evidence，安全结论为 not_evaluated。认证路径仍为 unknown，不根据 PMKSA 存在或缺帧作强判定。

过滤后抓包记录数分别为 64、70、85；其中属于本轮 AP/STA 单播对的记录数分别为 36、34、37。每轮都能看到目标链路的 SAE 和 EAPOL 帧。AP 两轮分别捕获到对应的受保护 Deauth、Disassoc 帧。审计按本轮 MAC 过滤分析，避免把附近网络的认证帧当作实验对象。

内核丢包 0 不代表固件向主机交付了所有空口帧。AP 共用 PHY 的 monitor 不是第三块独立物理网卡。本次没有证明授权时序安全，也没有实施伪造断开或高负载注入。

最新代码在树莓派运行 106 项测试全部通过。macOS 102 项测试中 100 通过、2 个 Linux 模块跳过。覆盖新增的在用接口拒绝、原状态记录/恢复、部分恢复失败、异步 PHY 返回、radio 选择检查。

结束时核对：无本次命名空间或 monitor；wlan1/wlan2 均 DOWN、无 IP、NetworkManager managed=yes、rfkill 恢复 soft blocked；eth0 正常。vcgencmd get_throttled=0x0。检查时段未见 USB 重置、驱动超时、欠压/过流错误。

## 产物与后续

- 服务器原始批次内：summary.json、每轮原始日志、pcap、运行配置及物理身份记录。新增批次保存 source-files.json 记录源码文件哈希，meta.json 保存基线提交、差异哈希与二进制哈希。
- 本地 `results/rpi5-20261115/`：汇总及审计结果；Git 忽略此目录，原始抓包和配置不入库。
- 需要后续验证：过滤后同配置 30 轮、5 GHz Wi-Fi 6、Framework 修改版 AP 在 ARM/实体网卡上的兼容性。它们均不属于本报告已完成的结论。
