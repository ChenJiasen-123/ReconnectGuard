# 王梓权：中期交接核对清单

日期：2026-11-10。基线提交 `3050854`。本文给队友逐条核对用，不替代
[检查器交付](wang-oracle-delivery.md)与[Kali 真实验证](wang-kali-validation-20261110.md)。

> **交付状态**：下列文件全是本地**未跟踪的新增文件**，未 `git commit`、未 `git push`。
> 队友现在执行 `git pull --ff-only` 取不到任何一行。需我提交或先打包转交。
> 已跟踪文件零改动（`git diff --stat` 为空），未修改任何队友模块。
> 精确提交清单、排除项与可直接复制的命令见第六节。

## 一、已完成，可直接验收

| 要求 | 出处 | 交付物 / 证据 |
|---|---|---|
| 实际检查器 `evaluate(*, context, snapshots, events)` → `CheckResult`；检查名 `state_consistency` / `authorization_timing`；四状态；不固定 pass | `要求.txt` L10、`wang-midterm-code-handoff.md` L20-29、`phase5-handoff.md` L23-27 | `observer/oracle.py`，加载名 **`observer.oracle:Oracle`** |
| 正常 / 持续不一致 / 短暂不同步 / 证据缺失 / 迟到旧事件各有样例，判定可追溯到证据 | `phase5-handoff.md` L45、`midterm-readiness` L58 | `observer/demo.py` 10 场景；`tests/test_oracle.py` 16 项 |
| 模块加载名 + 正常/异常/证据不足样例 + 对应日志或帧号 | `要求.txt` L13 | 交付文档第 5 节 + 验证文档第 3 节：真实 `run_id`、`event_id`、`snapshots.json#/i`、`trace.pcap#frame=N`、`sta.log#line=N` |
| 认证路径、缓存行为、尝试边界的证据；缓存存在 ≠ 缓存复用；恢复成功 ≠ 安全通过 | `要求.txt` L12、`phase5-handoff.md` L20-22 | `observer/evidence.py`、`observer/frames.py`；三案例 `pmksa_reuse` 三方互证 |
| 每组一条日志与抓包时间线；汇总缓存时点；引用具体 `run_id` / 事件 / 帧号；列出证据不足项 | `midterm-readiness` L59、L63 | 验证文档第 3、4、4.1 节；三份 `report-*.json` + 三份 `evidence-*.json` |
| 不为了让结果「通过」而固定返回 pass | `要求.txt` L27 | 31 轮真实运行 0 次安全通过 |
| 命令行 / 对照入口接入检查器 | `midterm-readiness` L58 | 王奥鑫在 `3050854` 提供 `--evaluator`；我用它跑完 31 轮真实实验 |

验证数字：Windows 104 项（100 通过 / 4 项 Linux 专属跳过），Kali **105 项全通过、0 跳过**。
两者是两台机器上的独立运行；Windows 的 4 项跳过由 `AF_UNIX` 缺失与非 linux 平台
的运行时条件触发，**不是未测**，覆盖这些用例的权威结论以 Kali 为准，日志见
`evidence/linux-unittest.log`（105 项）。31 轮 hwsim 全部完成、恢复、清理成功；
`state_consistency` 31 pass，`authorization_timing` 31 `insufficient_evidence`，
`security_verdict` 31 `not_evaluated`。

## 二、需队友集成（会限制结论，但不是本次遗漏）

| # | 待办 | 负责人 | 为什么王梓权这边闭不了 |
|---|---|---|---|
| 1 | 在标准快照 / 事件中产出 `frame_evidence`，并把 timeline 条目扩展为支持 `{"frame": null, "event_seq": N, "stage": ...}` 的控制面阶段 | 陈家森（平台）+ 王奥鑫（Runner） | `platform_observer.py` 不产该字段；`key_install` / `port_authorized` 是控制面事件，没有帧号，当前契约装不下。契约草案见交付文档「帧级证据契约」节 |
| 2 | 让 Runner 的 `auth_path` 从 `unknown` 回填为 `pmksa_reuse` | 王奥鑫 | `pmksa_reuse` 目前是 `observer.frames` 的**事后离线结论**，未回流 `result.json`；30 轮批次 `auth_path_counts` 仍全为 `unknown: 10` |
| 3 | 真实失败恢复的尝试边界（拒绝 / 超时 / 重复调用回执、自动连接） | 江天豪 → 王奥鑫 | 真实重试仍禁用，三案例中未出现可证实的迟到旧尝试事件；该分类目前只在构造场景验证过 |
| 4 | 确认「默认 CLI 接入检查器」的口径 | 王奥鑫 | 若指「CLI 能传 `--evaluator」则已完成；若指「裸命令自动加载 Oracle」则未做，且与 `wang-midterm-code-handoff.md` L39「普通命令不自动导入不存在的模块」相冲突，不宜由我单方改默认值 |

在第 1 项落地前，`authorization_timing` 在真实运行中**必然**是
`insufficient_evidence`。这是保守判定的预期结果；我没有放宽检查器去换一个 pass。

## 三、复现命令

```sh
# 纯逻辑，任意平台
python3 -m observer.demo --scenario all
python3 -m unittest discover -s tests -v

# 模拟后端接入检查器
python3 runner.py --case ap_deauth --evaluator observer.oracle:Oracle

# 真实 hwsim（Kali，sudo；31 轮已按此跑过）
sudo python3 comparison.py --backend real --client stock --repeat 10 --evaluator observer.oracle:Oracle

# 标准证据报告：三/四时点缓存、尝试边界、帧引用
python3 -m observer.evidence results/<run_id> --output report-<case>.json

# 事后帧级报告：需 Framework venv 的 scapy
vendor/wifi-framework/setup/venv/bin/python -m observer.frames results/<run_id> --output evidence-<case>.json
```

## 四、产物位置

- 代码：`observer/`（5 个模块）、`tests/test_oracle.py`、`tests/test_frames.py`、`docs/` 本文及另两份。
- 真实证据：`D:/CS_project/kali-validation-20261110/`（32 个文件，含三轮原始
  `trace.pcap` / `sta.log` / `snapshots.json` / `events.jsonl`、三份 `evidence-*.json`、
  三份 `report-*.json`、`comparison-summary.json`、`linux-unittest.log`）。
- Kali 侧：`/home/kali/Projects/rg-kali-20261110/kali-validation/evidence/` 为原始证据
  与三份 `evidence-*.json`；三份 `report-*.json` 是回传后在 Windows 本机用
  `observer.evidence` 生成的（不依赖 Linux / scapy），未在 Kali 侧重复产出。

## 五、集成时请勿这样做

1. 不要把 `trace.pcap#frame=N` 塞进 `CheckResult.evidence_refs`——`model/evaluation.py`
   只认本轮 `event_id` 与 `snapshots.json#/i`，帧号只能写进 `reason` / 证据报告。
2. 不要把 `pmksa_presence=present`、AP `AUTHORIZED` flag、`STA COMPLETED` 或恢复成功
   当作 `authorization_timing` 的 pass 依据。特别提醒 `AP-STA-CONNECTED` 只表示关联，
   实测比 STA 密钥安装早约 26.5 ms，误当端口授权会造出「授权早于密钥安装」的伪 fail。
3. 不要手工把离线 `pmksa_reuse` 回填进 `snapshots.json`，会破坏原始证据可回溯性。
4. 不要启用 `backends/real_observer.py` / `tests/test_real_observer.py`：这两个未跟踪旧草稿
   已被 `platform_observer.py` + 共享 journal 方案取代，无任何生产代码引用，且事件归属
   逻辑与「不得强行关联旧事件」的要求冲突。
5. 不要把 31 轮的恢复耗时与队友服务器那组（18.105 / 3872.180 / 3872.726 ms）直接比较，
   环境不同，三组重连机制也不同。

## 六、提交清单（由王梓权本人执行）

2026-11-10 核对：本地 `HEAD = origin/main = 3050854`，无分叉；`main` 跟踪 `origin/main`；
`user.name` / `user.email` 已配置；暂存区空；已跟踪文件零改动。
`.gitignore` 已覆盖 `results/`、`evidence/`、`vendor/`、`work/`、`__pycache__/`、`*.pyc`；
`.gitattributes` 会把 `*.py` 规范为 LF。**按下面精确清单提交即可，不要用 `git add -A`。**

### 应提交的 10 个文件

| 类别 | 文件 |
|---|---|
| 检查器与证据模块 | `observer/__init__.py`、`observer/oracle.py`、`observer/evidence.py`、`observer/demo.py`、`observer/frames.py` |
| 测试 | `tests/test_oracle.py`、`tests/test_frames.py` |
| 文档 | `docs/wang-oracle-delivery.md`、`docs/wang-kali-validation-20261110.md`、`docs/wang-handoff-checklist.md` |

### 必须排除的 3 个文件

| 文件 | 原因 |
|---|---|
| `backends/real_observer.py` | 10-20 旧草稿，已被 `platform_observer.py` + 共享 journal 取代，无生产代码引用；按「收到事件时的当前 attempt」归属事件，与「不得强行关联旧事件」冲突 |
| `tests/test_real_observer.py` | 同上，17 项测试跑的是假 Lab，不代表当前链路 |
| `要求.txt` | 队友组内汇报原文，属个人沟通材料，不是项目交付物 |

保留在本地即可，不要删除、也不要入库。

### 完整命令（Git Bash / PowerShell 通用）

```sh
cd D:/CS_project/ReconnectGuard

# 1. 提交前再确认远端未前进（本次核对时双方均为 3050854）
git fetch origin
git status -sb

# 2. 精确暂存；单行命令，两种 shell 都能直接粘贴
git add observer/__init__.py observer/oracle.py observer/evidence.py observer/demo.py observer/frames.py tests/test_oracle.py tests/test_frames.py docs/wang-oracle-delivery.md docs/wang-kali-validation-20261110.md docs/wang-handoff-checklist.md

# 3. 复核：暂存区应恰好 10 个文件，且没有 要求.txt / real_observer / .pyc
git status --short
git diff --cached --stat

# 4. 提交（沿用仓库英文 conventional commit 风格）
git commit -m "feat(observer): add evidence-driven oracle and real-frame evidence analysis"

# 5. 推送；当前为纯快进
git push origin main
```

### 提交后队友验证

```sh
git pull --ff-only
python3 -m unittest discover -s tests -v
python3 -m observer.demo --scenario all
python3 runner.py --case ap_deauth --evaluator observer.oracle:Oracle
```

预期：本机 Windows 104 项（100 通过 / 4 项 Linux 专属跳过），Kali 105 项全通过 0 跳过；
mock 冒烟返回两个 `insufficient_evidence` 与 `security_verdict=not_evaluated`，
无 `evaluation_error`。真实 hwsim 需 Linux + sudo。

### 三点提醒

- **真实 pcap、日志与证据报告不入库**：32 个产物在 `D:/CS_project/kali-validation-20261110/`
  与 Kali 的 `.../kali-validation/evidence/`，文档中的帧号、日志行号、`run_id` 都指向这两处。
  队友需要原始帧请另行索取；团队原本就把原始证据留在服务器。
- 若 `git push` 返回 403 / permission denied，说明账号对
  `ChenJiasen-123/ReconnectGuard` 没有写权限，需仓库所有者添加协作者，或改走
  fork + Pull Request。这与本地文件状态无关。
- `README.md` 本次**未改**（保持已跟踪文件零改动）。若希望队友在第一屏就看到新模块，
  可在 README「中期检查器与失败恢复接入」一节末尾补一行指向本清单与加载名
  `observer.oracle:Oracle`。
