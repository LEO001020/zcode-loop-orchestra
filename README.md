# ZLoop (gen8)

> Recoverable evidence, session binding, and isolated parallel execution around ZCode.
> 状态：**M0 substantially complete · M1 已部署 · M2/M3 核心已实现+已测试 · M6 库层进行中（round 2）** · 快照日期 2026-09-02

## ZLoop 是什么

ZLoop 不替代 ZCode 的认知，只补充四件事：**可恢复的过去**（H0 精确可观察历史 / H1 有界恢复检查点 / H2 可编程精确回查）、**信息带宽**（Research Broker）、**异种反证**（C2C Auditor，跨家族 web 计划/结果审计）、**隔离并发与崩溃一致性**（SQLite 控制库 S + cold supervisor + workspace fencing）。薄的是模型可见的认知面，厚的是模型看不见的正确性冷路径：ZCode 继续像 ZCode 一样思考；ZLoop 在它看不到的地方保存时间、扩大信息、隔离物理世界、保证崩溃后确定恢复。

权责模型固定不变：ZCode root 是唯一认知权威（目标解释、证据综合、阶段边界）；supervisor 拥有物理执行权威（worker 生命周期、workspace 租约、revision fencing、验收与晋升）；机械现实（compiler/test/Git/文件系统）是正确性权威，永远排在模型自评之前——层级固定为 `mechanical oracle > primary external evidence > heterogeneous critique > same-model introspection`。H0/H1/H2 只恢复可观察的过去，不决定过去"意味着什么"；Research Broker 与 C2C 只是信息服务/异种审计，永远不直接改 canonical state。完整生产不变式（I1–I44）与硬禁令见规范库 VOL-01。

## 仓库布局

```
E:\zcode\zloop-gen8\
├── src\zloop\            # paths / ids / redact / db(S) / evidence(H0) / hook / install /
│                         # history(H2) / checkpoint(H1.semantic) / stage / wave /
│                         # workspace / materialize / promote / cli
├── tests\                # 15 个测试文件（foundation/hook/install/cli/history/checkpoint/
│                         # stage/wave/workspace/materialize/promote/backend/c2c/research/worker_env）
├── scripts\              # phase1_archaeology.py + probes\（探针脚本）
├── artifacts\
│   ├── capabilities\     # phase-1.json、manifest.json（探针状态注册表 + D-1..D-12 决策镜像）
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

## 当前状态（2026-09-02，round 2 快照）

以 `artifacts/capabilities/manifest.json` 为准（含 D-1..D-12 决策镜像）；不变式→测试映射见 `docs/DEFECT_CONTRACT_TRACEABILITY.md`。

- **M0 substantially complete**（go/no-go 集合已闭合）：
  - PASS：P-ARC-1（考古快照）、P-SQL2（单权威主机）、P-WS1（worktree_fast vs clone_strong：前者 git-admin 不隔离 130ms，后者全隔离 353ms ⇒ HIGH/CRITICAL 用 clone_strong）。
  - DEGRADED（fallback 已执行）：P-SQL1（pysqlite3 0.6.0 捆绑 sqlite 3.51.1 仍在受影响区间 ⇒ 终案 DELETE+EXTRA，D-1/D-7；崩溃原子性/并发 claim/UNC fail-closed 全 PASS）；P-KIM1（K1 web server 全链路 PASS；K2 末次跑撞配额 rc=1——同账号配额 ⇒ 无互备价值，D-10 单路 K1）。
  - PARTIAL：P-HYG1（ProgramData 旧 LOOP hook 已清点+备份；移除待用户确认）、P-NAT1（8 并发 subagent 活体观察；完整 N 阶梯未跑）。
  - BLOCKED-manual：P-CDX1/2/3、P-LUNA1（codex 登录损坏，D-6，见下）。
  - BLOCKED-requires-new-session：P-HK1–HK4、P-BIND1、P-GC1、P-PLAT1（hook 配置按 session 快照；需新开 ZCode session）。
  - PENDING：P-C2C1。
- **M1 已部署**：`zloop install/uninstall/doctor` 实现并有测试；hooks 已于 2026-09-02 在本机用户级 `~/.zcode/cli/config.json` 安装（**5 个 post-execution 事件**，D-9；PreToolUse/PermissionRequest 因热路径税删除）。首个新 session 的活体验证清单见 `docs/OPERATIONS.md` §7——**尚未执行**。
- **M2/M3 核心已实现+已测试**：H0 journal（脱敏先于落盘 I13、CAS blob、fail-soft I3）、hook capture（7 事件分派）+ bind-token claim（I32，单次 nonce/TTL/前台约束 P2-13）、recovery 注入（I28）；S 控制库（版本闸门 I22、事务回滚、fail-closed I4、OS run lock）、H1.semantic 检查点（I15 降级、16KB 硬顶、去重、I14 machine 字段剥离）、H2 检索/验证。测试套件在 D-9 时点全绿（105 项，manifest 记录）。
- **M4 Research**：`research/broker.py` + `research/kimi_server.py`（K1 单路，D-10；K2 有意不实现）**本轮已落地**，`test_research.py` 以 stub HTTP server 验证 P-KIM1 契约（I27 终态判据、I42 隔离 temp cwd）；唯一的真实配额测试门控在 `ZLOOP_KIMI_LIVE` 之后。
- **M5 C2C**：`c2c.py` host-side prepare/record **本轮已落地**（脱敏、bounded、`audit_coverage="text_packet_only"` 诚实标注、D-11 分线程策略），配 `test_c2c.py`；浏览器交互由 root 原生执行，真实 transport 实测 = P-C2C1（未派发）。
- **M6 库层进行中**：stage（risk floor / clean-base I37 / FSM）、wave（四级 fencing I6/I7、supersede）、workspace（两级 workspace + delta 机器解析）已实现+测试；`materialize.py`（I38）/`promote.py`（I30/I39）**本轮落地且带测试文件**（`test_materialize.py` / `test_promote.py`，21:1x 由并行 agent 写入——本文档 agent 未执行该套件，绿否以套件 owner 报告为准）；D-8 controller CAS 已在 `db.py` 但**尚无专属测试**；supervisor 未开始。
- **M7 backend**：`backend/base.py`（AgentBackend 契约 + I27 终态纪律）+ `backend/codex_sdk.py`（D-5 legacy-hook 中和、agents 禁用配置）**本轮已落地**，配 `test_backend.py`（fake SDK 全链路）。活体测试全部阻塞在 codex 重新登录（D-6）；已知 v1 限制在模块 docstring 诚实记录（无 bounded wait、network/max_turns 尚未物理强制）。

## 诚实边界（当前不可用/未验证）

1. **Codex 登录已损坏**：`codex login status` → `invalid ID token format`（rc=1，见 phase-1.json）。需要用户重新执行 `codex login`。在此之前所有 Codex/Luna 活体探针（P-CDX1/2/3、P-LUNA1）为 **BLOCKED-manual**；M7 后端的一切活体测试（含 I29/I36 网络物理边界 canary）都阻塞在此。
2. **Hook 活测需要新的 ZCode session**：hooks 已安装（用户级，5 事件 D-9），但 hook 配置按 session 快照，当前 session 早于任何 hook 注册；安装目录不存在 headless zcode CLI 二进制（phase-1.json `zcode_install.headless_cli_found=false`）。因此 P-HK1–HK4 / P-BIND1 / P-GC1 / P-PLAT1 仍为 **BLOCKED-requires-new-session**——首验清单见 `docs/OPERATIONS.md` §7。
3. 本机 codex-cli 0.147.0 落后最新 0.152.1 五个版本、kimi 0.28.1 落后 0.40.0——厂商契约可能漂移，依赖前必须重跑对应探针（"上周能用"不是契约）。
4. `C:\ProgramData\OpenAI\Codex\requirements.toml`（旧 LOOP 机器级 hook 注册）仍然存活，会命中 ZLoop 的 Codex worker；未获用户确认前不得删除。缓解方案见 `docs/OPERATIONS.md` §6。
5. **代码边界（round 2 快照）**：supervisor、`zloop rollback` 未实现；D-8 controller CAS（db.py）尚无专属测试；CLI 的 stage/wave/research 子命令正在接线（以 `zloop --help` 实测为准）。本轮并行 agent 落地的 `materialize/promote/backend/c2c/research/worker_env` 模块带测试文件，但本文档 agent **未执行**这些套件（绿否以套件 owner 报告为准）。完整的不变式覆盖现状见 `docs/DEFECT_CONTRACT_TRACEABILITY.md`。
