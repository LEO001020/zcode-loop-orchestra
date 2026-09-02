# ZLoop (gen8)

> Recoverable evidence, session binding, and isolated parallel execution around ZCode.
> 状态：**M0(Archaeology + Capability Truth)进行中** · 快照日期 2026-09-02

## ZLoop 是什么

ZLoop 不替代 ZCode 的认知，只补充四件事：**可恢复的过去**（H0 精确可观察历史 / H1 有界恢复检查点 / H2 可编程精确回查）、**信息带宽**（Research Broker）、**异种反证**（C2C Auditor，跨家族 web 计划/结果审计）、**隔离并发与崩溃一致性**（SQLite 控制库 S + cold supervisor + workspace fencing）。薄的是模型可见的认知面，厚的是模型看不见的正确性冷路径：ZCode 继续像 ZCode 一样思考；ZLoop 在它看不到的地方保存时间、扩大信息、隔离物理世界、保证崩溃后确定恢复。

权责模型固定不变：ZCode root 是唯一认知权威（目标解释、证据综合、阶段边界）；supervisor 拥有物理执行权威（worker 生命周期、workspace 租约、revision fencing、验收与晋升）；机械现实（compiler/test/Git/文件系统）是正确性权威，永远排在模型自评之前——层级固定为 `mechanical oracle > primary external evidence > heterogeneous critique > same-model introspection`。H0/H1/H2 只恢复可观察的过去，不决定过去"意味着什么"；Research Broker 与 C2C 只是信息服务/异种审计，永远不直接改 canonical state。完整生产不变式（I1–I44）与硬禁令见规范库 VOL-01。

## 仓库布局

```
E:\zcode\zloop-gen8\
├── src\zloop\            # 基础模块：paths / ids / redact / db(S) / evidence(H0 journal + CAS)
├── tests\                # test_foundation.py
├── scripts\              # phase1_archaeology.py（P-ARC-1 探针脚本）
├── artifacts\
│   ├── capabilities\     # phase-1.json、manifest.json（探针状态注册表）
│   └── probes\           # 各探针的 machine-readable 输出
├── docs\                 # VENDOR_CONTRACTS.md（厂商契约表）、OPERATIONS.md（运维手册）
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

## 当前状态（2026-09-02，M0 进行中）

- 基础模块已落地：paths / ids / redact / db(S) / evidence(H0 journal + CAS)；P-ARC-1 考古探针 **PASS**（`artifacts/capabilities/phase-1.json`）；foundation 测试套件已建立。
- 探针状态注册表：`artifacts/capabilities/manifest.json`；厂商契约表：`docs/VENDOR_CONTRACTS.md`。
- M0 退出判据（VOL-20 §6 六项）**尚未满足**；M1（no-op 插件 + install/uninstall/doctor）未开始。
- CLI 入口（`zloop` / `zloop-hook` console scripts）已在 `pyproject.toml` 声明，实现属 M1。

## 诚实边界（当前不可用/未验证）

1. **Codex 登录已损坏**：`codex login status` → `invalid ID token format`（rc=1，见 phase-1.json）。需要用户重新执行 `codex login`。在此之前所有 Codex/Luna 活体探针（P-CDX1/2/3、P-LUNA1）为 **BLOCKED-manual**；本机尚未验证任何 Codex SDK 行为。
2. **Hook 活测需要新的 ZCode session**：hook 配置按 session 快照，当前 session 早于任何 hook 注册；安装目录不存在 headless zcode CLI 二进制（phase-1.json `zcode_install.headless_cli_found=false`）。因此 P-HK1–HK4 / P-BIND1 / P-GC1 / P-PLAT1 为 **BLOCKED-requires-new-session**。
3. 本机 codex-cli 0.147.0 落后最新 0.152.1 五个版本、kimi 0.28.1 落后 0.40.0——厂商契约可能漂移，依赖前必须重跑对应探针（"上周能用"不是契约）。
4. `C:\ProgramData\OpenAI\Codex\requirements.toml`（旧 LOOP 机器级 hook 注册）仍然存活，会命中 ZLoop 的 Codex worker；未获用户确认前不得删除。缓解方案见 `docs/OPERATIONS.md`。
5. 尚无 wave / scheduler / backend 实现；当前仅基础模块与探针工件。
