# ZLoop 运维手册（Operations）

> 适用对象：ZLoop 的安装、卸载、诊断、回滚，以及本机遗留（OLD-LOOP）卫生项处置。
> 契约依据：`E:\zcode\zloop-spec\VOL-05-HOOK-BINDING.md`（安装面）、VOL-20 P-HYG1、VOL-21 M1 gate。
> **现状（2026-09-02，M0）**：`zloop install / uninstall / doctor / rollback` 是 M1 交付物，CLI 入口已在 `pyproject.toml` 声明但尚未实现。本文描述的是已冻结的**运维契约**；在 M1 落地前不要假定这些命令可执行。

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
3. 注册全部 **7 个已文档事件**（SessionStart / UserPromptSubmit / PreToolUse / PermissionRequest / PostToolUse / PostToolUseFailure / Stop），M1 阶段为 no-op。
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
