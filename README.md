# ZLoop (gen8)

> Recoverable evidence, session binding, and isolated parallel execution around ZCode.
> 状态（round 3 诚实台账，2026-09-02）：**实现覆盖 ≈M0–M7 · 已验证能力 ≈M3+M6_mock+Kimi_K1+P-CDX1(route) · M7 真实负载闸门阻塞于 P-SEC1 隔离失败（D-17）⇒ 非生产候选**。两表分述见下。

## ZLoop 是什么

ZLoop 不替代 ZCode 的认知，只补充四件事：**可恢复的过去**（H0 精确可观察历史 / H1 有界恢复检查点 / H2 可编程精确回查）、**信息带宽**（Research Broker）、**异种反证**（C2C Auditor，跨家族 web 计划/结果审计）、**隔离并发与崩溃一致性**（SQLite 控制库 S + cold supervisor + workspace fencing）。薄的是模型可见的认知面，厚的是模型看不见的正确性冷路径：ZCode 继续像 ZCode 一样思考；ZLoop 在它看不到的地方保存时间、扩大信息、隔离物理世界、保证崩溃后确定恢复。

权责模型固定不变：ZCode root 是唯一认知权威（目标解释、证据综合、阶段边界）；supervisor 拥有物理执行权威（worker 生命周期、workspace 租约、revision fencing、验收与晋升）；机械现实（compiler/test/Git/文件系统）是正确性权威，永远排在模型自评之前——层级固定为 `mechanical oracle > primary external evidence > heterogeneous critique > same-model introspection`。H0/H1/H2 只恢复可观察的过去，不决定过去"意味着什么"；Research Broker 与 C2C 只是信息服务/异种审计，永远不直接改 canonical state。完整生产不变式（I1–I44）与硬禁令见规范库 VOL-01。

## 仓库布局

```
E:\zcode\zloop-gen8\
├── src\zloop\            # paths / ids / redact / db(S) / evidence(H0) / hook / install /
│                         # history(H2) / checkpoint(H1.semantic) / stage / wave /
│                         # workspace / materialize / promote / cli
├── tests\                # 17 个测试文件（foundation/hook/install/cli/history/checkpoint/
│                         # stage/wave/workspace/materialize/promote/backend/c2c/research/
│                         # worker_env/redact_refine/supervisor）
├── scripts\              # phase1_archaeology.py + probes\（探针脚本）
├── artifacts\
│   ├── capabilities\     # phase-1.json、manifest.json（探针状态注册表 + status_ledger
│                         #   + D-1..D-21 决策镜像）
│   └── probes\           # 各探针的 machine-readable 输出
├── docs\                 # VENDOR_CONTRACTS.md、OPERATIONS.md、COMMANDS.md、
│                         # DEFECT_CONTRACT_TRACEABILITY.md（VOL-18 §G）
└── pyproject.toml
```

## 快速开始

```bash
pip install -e ".[dev]"
PYTHONPATH=src python -m pytest
```

## 规范库（权威设计）

本仓库**不复制**规范内容。权威设计位于 `E:\zcode\zloop-spec\`（VOL-00…VOL-22 + `PROGRESS.md` + `DECISIONS.md`）：

- 装载协议与卷注册表：VOL-00 §2–§4；里程碑与闸门：VOL-21；探针目录：VOL-20。
- 规范与真机观察冲突时**现实获胜**：先更新对应卷 + 在 `DECISIONS.md` 记一条，再继续工作。

## 当前状态（2026-09-02，round 3 诚实台账）

> 第三方审计要求：**实现覆盖面**与**已验证能力**分开陈述，不得合并成单一里程碑口号。机器可读镜像见 `artifacts/capabilities/manifest.json` 的 `status_ledger` 键；不变式→测试映射见 `docs/DEFECT_CONTRACT_TRACEABILITY.md`。

### Table A — Implementation coverage（代码面存在性，不是能力证明）

**≈ M0–M7 代码面已存在。** 测试基线：round 2 时点 193 通过 / 2 skip（`E:\zcode\zloop-spec\PROGRESS.md` 记录）；round 3 并行 agent 正在追加新套件（`test_redact_refine.py`、`test_supervisor.py` 等已入库——**本文档 agent 未执行任何测试套件**，绿否以各套件 owner 的报告为准）。

| 里程碑 | 代码面 |
|---|---|
| M0 | 探针注册表 + 决策镜像（manifest.json，D-1..D-21） |
| M1 | `zloop install/uninstall/doctor`；5 个 post-execution hook 已装真实用户级 config（D-9）；D-16 plugin 打包本轮接线 |
| M2/M3 | H0 journal（I13 脱敏先于落盘、CAS blob）/ hook capture / bind-token claim（I32）/ S 控制库（I22 版本闸门、I4 fail-closed）/ H1.semantic（I14/I15）/ H2 检索 |
| M4 | research broker + Kimi K1 单路（D-10）；D-18 三轴语义与 D-19 searcher-only 工具面本轮接线 |
| M5 | c2c host prepare/record（脱敏、bounded、D-11 分线程策略） |
| M6 | stage/wave/workspace/materialize/promote/supervisor（D-8 controller CAS、D-20 接管死亡证明）；`zloop stage promote` CLI 本轮接线 |
| M7 | backend/codex_sdk.py（D-5 旧 hook 中和、agents 禁用）+ worker_env allowlist |

### Table B — Validated capability（已验证能力，round 3 定级）

| 能力 | 定级 | 依据 |
|---|---|---|
| Architecture（整体架构） | **CONDITIONAL GREEN** | 单测覆盖 + 两轮审计闭环；条件 = 下述 RED/YELLOW 项全部收口 |
| H0 / binding / SQLite | unit GREEN · **live YELLOW** | 活体验证（P-HK1–HK4 / P-BIND1 / P-PLAT1）需新 ZCode session（无 headless CLI） |
| Kimi K1 lane | live 契约已验证（P-KIM1 K1 全链 PASS）；provider 当前**配额降级** | 配额自动重置；K2 单路取消（D-10） |
| Luna | **RED** | membership 路由未测；cliproxy 路由暴露的是 provider 侧 web_search ⇒ P-LUNA 探针**待重定义**后再测 |
| Codex SDK route | live 单轮已验证（P-CDX1）+ 公共出口封禁；**但 P-SEC1 FAIL：文件系统读取不设限 + loopback 可达** | ⇒ 外部 worker = trusted-content only，直到 OS 边界落地（D-17） |
| C2C | host 协议 GREEN · **browser E2E 未验证** | P-C2C1 PENDING（Browser 仅 root agent） |
| G-COG | **RED（最高优先）** | P-GC1 BLOCKED-requires-new-session |
| M8 / M9 / M10 | **未运行** | 真实 G-L 循环 / 原生-vs-外部对比臂 / context-quality 臂均未跑 |

**Production candidate: NO** —— M7 real-workload gate 阻塞于 P-SEC1 隔离失败（D-17 缓解纪律 + OS 边界为前置条件）。

## 诚实边界（当前不可用/未验证，round 3 快照）

1. **P-SEC1 FAIL（当前最高优先阻塞）**：Codex worker 的 `workspace_write` 沙箱**只限制写、不限制读**——跨盘 sentinel（`C:\zloop-private-sentinel\` 与用户 profile）均被逐字读出；公共出口封禁但 **127.0.0.1 loopback 可达**（worker 可读 `~/.kimi-code/server.token` 并触达本机 Kimi server 的 sessions/fs/shell 面）。缓解（D-17，supervisor 已落码）：Kimi loopback server 存活期间 `wave start` 直接拒绝；**波次期间绝不运行 kimi web**。结构性修复 = 专用低特权 OS 身份/边界（未落地）——在此之前 Codex worker 仅限 trusted-content 工作负载。
2. **Hook 活测需要新的 ZCode session**：hooks 已安装（用户级，5 事件 D-9），但 hook 配置按 session 快照，当前 session 早于任何 hook 注册；安装目录不存在 headless zcode CLI 二进制。因此 P-HK1–HK4 / P-BIND1 / P-GC1 / P-PLAT1 仍为 **BLOCKED-requires-new-session**——审计批准的用户行动顺序见 `docs/OPERATIONS.md` §7。
3. 本机 codex-cli 0.147.0 落后最新 0.152.1 五个版本、kimi 0.28.1 落后 0.40.0——厂商契约可能漂移，依赖前必须重跑对应探针（"上周能用"不是契约）。
4. `C:\ProgramData\OpenAI\Codex\requirements.toml`（旧 LOOP 机器级 hook 注册）仍然存活。**新路由下并非必需**（workers 已携带 `CODEX_LOOP_REQUIREMENTS_TOML` 空 override，D-5），但建议按 rename-first 程序安全停用以移除混淆变量——见 `docs/OPERATIONS.md` §6。
5. **代码边界（round 3 快照）**：`zloop rollback` 未实现（M10 交付物）；本轮 GLM 自修项——D-16 hook 项目级 scoping + plugin 打包、D-20 接管死亡证明、D-18 research 三轴语义、D-19 searcher-only 工具面、`zloop stage promote` CLI、D-21 redact 精化——**均在进行中**（部分已可见于代码），验证以各套件 owner 报告 + `zloop --help` 实测为准（no-fake-success）。本文档 agent 未执行任何测试套件。完整的不变式覆盖现状见 `docs/DEFECT_CONTRACT_TRACEABILITY.md`。
