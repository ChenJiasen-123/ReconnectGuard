# 真实 Runner 接入验收

日期：2026-10-23。基于 d222aca 完成真实 Runner 集成；提交编号以 Git 历史为准。

## 本次做了什么

| 改动 | 结果 |
|---|---|
| Runner 改为统一调用 Session、Executor、Observer | 同一流程支持 mock / real |
| RealSession 复用 Lab | 一轮一个 run_id、目录、事件序号空间 |
| 最小真实 Executor | 主动 DISCONNECT 和显式 RECONNECT；带原始响应、时间、错误分类 |
| 健康检查与失败处理 | 子进程退出和采集错误不再只表现为超时 |
| 多轮执行 | --repeat 10；清理失败或用户中断停止后续轮次 |
| 对外接口 | 见 [给组员的接口说明](platform-api.md) |

## 验证结果

| 检查 | 结果 |
|---|---|
| Windows 逻辑回归 | 适用测试通过；4 项 Linux 专属测试跳过 |
| Kali 完整测试 | 19 / 19 通过 |
| 最终版本真实十轮 | 10 / 10 成功；simulated=false |
| 每轮状态 | RECONNECTED；观察到断开和显式恢复 |
| 客户端连续性 | 每轮内部客户端进程世代不变；轮与轮之间重新准备环境 |
| 原始与标准事件 | 原始 seq 连续，标准事件引用同一序号；attempt=1,1,1,2,2 |
| 清理 | 每轮无错误；最终无 rg 命名空间及 hwsim 残留 |
| 官方客户端 | 下载后校验 SHA-256，再在 Kali 编译未修改版 2.10；最终十轮使用默认 stock 路径 |
| 原有环境 | 验证在独立目录进行，未覆盖原有部署目录 |

## 最终十轮

| run_id | 最终状态 | 原始事件数 | 客户端连续性 |
|---|---|---|---|
| 20261023-123448-8bac93 | RECONNECTED | 28 | pass |
| 20261023-123454-17de30 | RECONNECTED | 28 | pass |
| 20261023-123500-b3a687 | RECONNECTED | 28 | pass |
| 20261023-123506-397a7e | RECONNECTED | 28 | pass |
| 20261023-123512-e8642b | RECONNECTED | 28 | pass |
| 20261023-123518-da4933 | RECONNECTED | 28 | pass |
| 20261023-123524-d2afaa | RECONNECTED | 28 | pass |
| 20261023-123530-b65289 | RECONNECTED | 28 | pass |
| 20261023-123536-7e1ab5 | RECONNECTED | 28 | pass |
| 20261023-123542-9b948a | RECONNECTED | 28 | pass |

## 真实失败注入

| 注入 | 观察到的错误分类 | 清理 |
|---|---|---|
| 向实验客户端发送无效控制命令 | action_rejected | 成功 |
| 丢弃真实断开事件的标准化交付，保留原文 | observation_timeout | 成功 |
| 终止本轮自己创建的实验客户端进程 | process_exit | 成功 |

注入仅作用于独立 hwsim 实验；不终止系统网络服务。单元测试另覆盖启动失败、采集失败、清理失败和用户中断。

## 复现入口

```bash
# 在已完成 scripts/setup.sh 的 Linux 项目根目录
sudo python3 runner.py --backend real --client stock --repeat 10
```

本次验证目录：`/home/chen/Projects/ReconnectGuard-runner-20261023-01`。
批次文件：`results/batch-84123ca46c4a4bf79a9cd75753eff78a.json`。
完整摘要与逐文件代码 SHA-256 在本地 `evidence/runner-acceptance.json`；原始日志和抓包不提交 Git。

## 2026-10-24 案例接入进展

本次补充了 `ap_deauth` 和 `ap_disassoc` 两个五步案例。两者在 Mock 后端使用自动恢复模式，真实后端复用同一个 Executor、Observer 和 EFSM 路径；Windows 逻辑回归已覆盖动作回执、自动恢复和真实装配替身。

当前本机没有 Linux hwsim 环境，因此以下真实验证仍需在 Kali 完成：

```bash
sudo python3 runner.py --case ap_deauth --backend real --client stock --repeat 10
sudo python3 runner.py --case ap_disassoc --backend real --client stock --repeat 10
```

## 还没做

| 项目 | 当前范围 |
|---|---|
| AP Deauth / Disassoc 真实十轮 | 案例和动作已接入；等待 Linux hwsim 验证 |
| 自动重连行为的真实分类 | Runner 已记录恢复事件；具体认证路径仍由 Observer/Oracle 分析 |
| 完整 SAE / 缓存使用分类 | auth_path 保留 unknown |
| 授权时序和漏洞判定 | insufficient_evidence / not_evaluated，尚未实现 Oracle |

配置档案的 case 保留输入定义；实际执行模式看 meta.json 的 runner_backend 和 simulated。文档描述的是本次实现，不替代团队对后续扩展的确认。
