# ZLoop 运维手册（Operations）

> 适用对象：ZLoop 的安装、卸载、诊断、回滚，以及本机遗留（OLD-LOOP）卫生项处置。
> 契约依据：`E:\zcode\zloop-spec\VOL-05-HOOK-BINDING.md`（安装面）、VOL-20 P-HYG1、VOL-21 M1 gate。
> **现状（2026-09-02，round 2）**：`zloop install / uninstall / doctor` 已实现并有测试（M1 交付物；hooks 已在本机用户级 config 安装，5 事件，见 §2 与 D-9）。`zloop rollback` 是 M10 交付物，尚未实现。`zloop-hook` capture / bind-token / recovery 已实现（M2）。§8 是 v0.2 命令面参考——其中标注 "wiring in progress" 的命令是本轮并行 agent 正在接线、**尚未在代码中出现**的，在 `zloop --help` 确认之前不要假定可执行。

---

## 1. 数据根与路径

- 数据根 `%ZLOOP_DATA%` = `~/.zloop`（Windows：`C:\Users\<user>\.zloop`）。
- 布局：`%ZLOOP_DATA%/registry.json`；`%ZLOOP_DATA%/projects/<project_id>/{control.sqlite3, history/, blobs/, workspaces/, runs/, research/, c2c/}`；备份目录 `%ZLOOP_DATA%/hygiene-backup/`。
- 测试与演练**绝不**使用真实 `~/.zloop`——一律临时目录/隔离数据根。

## 2. 安装 hook（`zloop install`）

1. `zloop install` 将 zloop hook 以 **process 型**注册写入**用户级** `~/.zcode/cli/config.json` 的 `hooks` 块（argv、无 shell、同步执行）。
   - 只走用户级 config（或 plugin `hooks/hooks.json`）；**workspace 级 `.zcode/config.json` 的 hooks 会被 ZCode 整体忽略**。
   - 注册前若 config 中已有 hooks，先做备份到 `~/.zloop/hygiene-backup/`。
2. `hooks` 块必须带 `enabled:true`（否则默认禁用）。默认 `timeoutMs=60000`、`maxOutputBytes=32768`（hook 自身 stdout 上限）。
3. 注册 **5 个 post-execution 事件**（D-9：SessionStart / UserPromptSubmit / PostToolUse / PostToolUseFailure / Stop；PreToolUse / PermissionRequest 因热路径进程税被删除）。hook 代码本身保留 7 事件分派——若配置里手工注册了全部 7 个，仍能正常工作。
4. **关键限制**：hook 配置按 ZCode session **快照**——安装/改动只对**新开的 session** 生效（见 §7 验证清单）。

## 3. 卸载（`zloop uninstall`）

1. `zloop uninstall` **只移除 zloop 管理的 hooks 块**；用户可能存在的其他 hook 注册原样保留。
2. 移除前把当前 config 状态备份到 `~/.zloop/hygiene-backup/`。
3. 卸载后需**新 session** 才能观察到 native 语义恢复（I1：无注入、无 hook 进程、turn 延迟与 Z0 无可感差）。
4. **数据不在卸载范围内**：`~/.zloop`（registry、各项目的 control.sqlite3、history/、blobs/ 等）在卸载后全部保留；删除数据根是单独的、显式的手动动作。

## 4. 诊断（`zloop doctor`）

`zloop doctor` 检查并报告：

- 用户级 `~/.zcode/cli/config.json` 中 zloop hooks 块是否存在、`enabled` 是否为 true；
- 数据根 `~/.zloop` 可写性、journal/DB 路径布局；
- SQLite 运行时版本 vs D-1 版本闸门（≥3.50.7 backport / ≥3.51.3 才允许 WAL，本机 3.50.4 ⇒ DELETE+EXTRA）；
- 探针注册表（`artifacts/capabilities/manifest.json`）中 BLOCKED/PARTIAL 项摘要。

## 5. 回滚（rollback）

- `zloop rollback`（演练命令 `zloop rollback --run`，M10 release gate 必测）：停用 hooks 与 supervisor，恢复纯 ZCode native 语义，同时保全用户 repo 状态与 `~/.zloop` 数据。
- 原则：**回滚/卸载永不损失数据**——`~/.zloop` 数据、用户 repo 的未提交修改都不在删除范围（硬禁令 7）。

## 6. OLD-LOOP 卫生项（P-HYG1，M0 硬前置）

**事实**：`C:\ProgramData\OpenAI\Codex\requirements.toml` 是**存活中的旧 LOOP 机器级 Codex hook 注册**（`global_loop_mode.py` 等，含硬编码 `E:\codex-LOOP` 路径）。ZLoop 的 first-class worker 是 Codex 进程 ⇒ 会被旧钩子命中。

**当前状态（2026-09-02）**：

- 已清点并**备份**到 `~/.zloop/hygiene-backup/`（4571 字节，sha256 `6c0f4f7b461ccc2bdf57725d0e65478062dfdc8a0755dc59c7d8815f63af8de4`）。
- **移除需要用户显式确认**——未经确认绝不删除任何 ProgramData 文件。确认移除后：复跑一次干净 Codex headless turn（临时 cwd），验证不再触发任何旧 LOOP hook。

**worker 进程缓解（移除前/用户拒绝移除时）**：

- 为 Codex worker 设置环境变量 `CODEX_LOOP_REQUIREMENTS_TOML`，指向一个**空的 requirements 文件**，绕过旧版 managed hooks。
- 替代 fallback（VOL-20 P-HYG1）：为 first-class worker 使用隔离的 `CODEX_HOME`（全新 codex home + 显式登录），并在 manifest 记录原因。

**其他遗留**：`~/.codex` 下 5 个 `auth.json` 变体与 config 备份一律禁止作为凭据来源（割接时轮换/清理，见 VOL-17 §6）；旧树 `E:\codex-LOOP` 只读归档，不再修改。

## 7. 首个 session 验证清单（用户操作）

hook 安装后按此顺序验证（hook 配置按 session 快照，**旧 session 永远看不到新 hooks**）：

1. **新开一个 ZCode session**（关闭当前会话，重新打开）。
2. 在新 session 里**运行任意 Bash 命令**（例如 `echo zloop-check`）——触发 PostToolUse。
3. **检查 journal 文件出现**：`~/.zloop/projects/<project_id>/history/sessions/` 下应生成对应 session 的 H0 journal 文件。
4. （可选）运行 `zloop doctor` 确认绑定与路径状态。
5. 若第 3 步没有文件：
   - 确认 session 确实是新开的（不是恢复/复用的旧会话）；
   - 检查 `~/.zcode/cli/config.json` 中 zloop hooks 块存在且 `enabled:true`；
   - 运行 `zloop doctor` 查看诊断输出。

---

## 8. Command reference (v0.2)

> 快速参考：`docs/COMMANDS.md`（一页版，含退出码）。本节记录 v0.2 新命令面的**运维语义**。
> 诚实边界：以下 "wiring in progress" 的命令由本轮并行 CLI agent 接线——快照时 `src/zloop/cli.py` 已出现 stage/wave/workspace 导入与脚手架（21:23 仍在写入），但**子命令尚未在 parser 注册**；以 `zloop --help` 实测为准（no-fake-success，VOL-01 §3.3）。

### 8.1 退出码（全命令统一契约）

| code | 含义 |
|---|---|
| 0 | ok |
| 2 | usage（argparse 参数错误） |
| 3 | S_DEGRADED — S 损坏/不可写，fail-closed（I4） |
| 4 | bind-token wait 超时（`--wait-claim` 到期未被 hook claim；通常是跑在了后台，P2-13） |
| 5 | blocked — 前置条件不满足（未注册项目 / 未知 run / 状态不允许） |
| 130 | Ctrl-C 中断 |

### 8.2 v0.2 新命令面（wiring in progress this round）

| 命令 | 语义（契约） | 状态 |
|---|---|---|
| `zloop stage begin <objective-slice> [--risk REQUESTED]` | 在当前 run 创建 stage（PLANNING→EXECUTING）：确定性 risk floor（VOL-08 §2）、clean-base 门槛（I37）、锁定 base ref/tree | **wiring in progress**（stage.py 库层已实现+测试；CLI 子命令待并行 agent 落地） |
| `zloop stage status [RID]` / `zloop stage close` | stage 行 / FSM 终态 | **wiring in progress** |
| `zloop wave propose` | 提交 wave proposal → host-side final ruling（`validate_wave`：DAG 无环、write_scope 两两不相交或显式 depends_on 序列化、risk ≥ floor、network_policy 形状） | **wiring in progress**（wave.py 库层已实现+测试） |
| `zloop wave start` | 启动 wave：每 packet 全新 launch_id + 全新 workspace（I34）；结果过 I6 四重 fence | **wiring in progress**；**FOREGROUND 命令**（见 8.3） |
| `zloop wave cancel` | 写 `cancel_requested`（D-8 语义，见 8.4） | **wiring in progress** |
| `zloop research run <query>` | Research Broker 单次检索（Kimi K1 单路，D-10；lane 代码待 M4） | **wiring in progress**（M4） |
| `zloop verify-run [RID]`（升级版） | 目标完成判据：run 存在且 CLOSED +（升级方向）promoted stage 全部落地。当前已实现版本：run 存在且 CLOSED 即 ok，否则 exit 5 | 已实现（基础版）；升级项 **wiring in progress** |

已稳定实现的命令（doctor / project / run / attach / detach / binding / history / checkpoint / install / uninstall / verify-run）见 `docs/COMMANDS.md`。

### 8.3 FOREGROUND 规则（run start / attach / wave start）

- `zloop run start` / `zloop attach` /（落地后的）`zloop wave start` 是**前台命令**：
  1. bind-token marker（`ZLOOP_BIND_TOKEN=<nonce>`）必须出现在**前台** Bash tool_response 的 stdout 里，PostToolUse hook 才能扫到并 claim（I32）。后台运行的 tool_response 不含 stdout ⇒ token 永远无法被 claim（P2-13）。
  2. 平台硬上限：单次前台 Bash 调用 ≤ **600,000 ms（600s）**。因此 `--wait-claim N` 的 N 必须远小于 600；超时未 claim ⇒ exit 4 + stderr 警告"re-run in foreground"。
  3. 超过 10 分钟的 wave **不得**在前台死等：用 `wave start`（后台化）+ 结束 turn，等 task-notification 重新唤醒 root（D-2：await 参考实现 = background + notification；`await --timeout` 上限 540s）。

### 8.4 Controller-token cancel 语义（D-8）

- run 的所有权 = S 内 **CAS token**（`runs.controller_nonce/controller_pid/controller_pid_start`），不是长持 OS 锁——旧设计（wave 长进程持 run.lock + cancel 需要 S 事务）会自我锁死，已废弃。
- `cancel` 是**命令输入，不是生命周期迁移**：`request_cancel` 只写 `cancel_requested=1`（无需 controller token）；owner 在自己的下一个 tick 观察到它，由 **owner** 执行状态迁移（ACTIVE→CANCELLED 等）。
- 接管（takeover）需要机械死亡证明：新 controller 只有带着旧 `(pid, pid_start_time)` 的死亡证据才能 CAS 换主（`claim_controller(expected_old=…)`）；TTL 永不单独决定所有权（I43）。
- OS `RunLock` 只包住单次 mutation，绝不跨 wave 生命周期持有。

### 8.5 5-hook 注册（D-9）

- `zloop install` 只注册 **5 个 post-execution 事件**：SessionStart / UserPromptSubmit / PostToolUse / PostToolUseFailure / Stop（`process` 型、无 matcher、`timeoutMs=8000`、`maxOutputBytes=32768`、用户级 config）。
- 理由：PreToolUse / PermissionRequest 会在**每次工具调用前**同步拉起进程（热路径税）；PostToolUse(+Failure) 已携带完整结构化结果。第二次独立审计（2026-09-02）裁定。
- hook 进程本身仍分派全部 7 个已文档事件（防御性兼容：手工注册 7 个也能工作）。
