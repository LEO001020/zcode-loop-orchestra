<!-- size-justified: 中文项目主页；不内联日志、清单和运行状态。 -->
<div align="center">

# ZCode LOOP Orchestra

**给 ZCode 加上一层真正的控制回路：让 Agent 工作持续推进、结果可验证、发布由人决定。**

[![CI](https://github.com/LEO001020/zcode-loop-orchestra/actions/workflows/ci.yml/badge.svg)](https://github.com/LEO001020/zcode-loop-orchestra/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![平台：Windows](https://img.shields.io/badge/Platform-Windows-0078D4.svg)](docs/INSTALL.zh-CN.md)

[控制回路](#控制回路就是产品) · [你会得到什么](#你会得到什么) · [快速开始](#快速开始) · [工作方式](#工作方式) · [English](README.md)

</div>

ZCode 可以启动多个 Agent，但“把 Agent 启动起来”并不等于一套能稳定推进的工程流程。LOOP 补上的是启动之后的控制层：记录一次运行，把工作拆成边界明确的任务包，在不阻塞其他任务的情况下推进已就绪工作，把执行结果重新放回暂存区验收，并把最终晋升命令留给人。

实际使用时，你只需要给出项目目标和任务包清单。LOOP 会在受监督的波次中推进已提交的工作，拒绝越界或过期的结果，并留下可以追溯的状态与证据。它是一套本地 Python CLI 加上受管理的 ZCode hooks；它不替代 ZCode，也不包含 ZCode 客户端本身。

> **Agent 负责做事，程序负责记状态，机械检查决定结果是否合格，人类决定什么可以发布。**

## 控制回路就是产品

原生 harness 擅长启动 Agent，但一项真正的工程任务还必须处理：任务完成后谁来补位、文件写错了怎么办、验收失败后如何恢复、计划变化后旧结果还能不能接收。LOOP 把这些容易被遗漏的环节固定成一条控制回路：

1. 根对话只输出有边界的决策骨架，不亲自承担批量执行工作。
2. 每个任务包都写清目标、授权路径、验收命令和约束条件。
3. DAG 门禁在派发之前拒绝循环依赖和重叠的写入范围。
4. 波次监督器只启动依赖已经满足的任务，并用非阻塞的 <code>poll()</code> 收取报告。
5. 物化阶段把执行者的差异重新应用到私有暂存区，在那里重新执行宿主验收；失败时恢复到此前的暂存 SHA。
6. 审计记录可以阻断、要求重做或升级，但不能发布。
7. 最终晋升命令始终由人执行，规范分支不属于 worker 的直接写入范围。
8. 等待、轮询、计数、常规重试和状态迁移都由程序与持久化状态处理，不浪费额外的协调层轮次。

一句话概括 LOOP：**让有价值的工作继续向前，让坏的变更停在边界内，让发布权始终在人手里。**

## 你会得到什么

下面按使用者真正遇到问题的顺序说明：原生 harness 的局限是什么，LOOP 具体改了什么，以及这样做带来的好处。

| 原生 harness 的局限 | LOOP 做的改变 | 你得到的好处 |
|---|---|---|
| 一批任务会随着 Agent 完成而逐渐变空，常常还要再次输入“继续”。 | 监督器跟踪待执行和运行中的任务，在同一波次中启动下一个已就绪任务。 | **只要还有已提交的有效工作，流程就能继续推进。** |
| 多个 Agent 共用可变状态，或者直接碰同一个规范工作区。 | 每次启动使用独立的工作目录，集成发生在单独的 staging worktree 中。 | **一个 worker 不会直接覆盖另一个 worker 的集成状态。** |
| 执行者说“完成了”，这句话就被当成最终结果。 | LOOP 枚举实际差异，检查路径范围，把差异重新应用到 staging，再运行宿主验收命令。 | **候选结果在真正要被集成的位置接受检查。** |
| 同步驱动器卡在第一个任务上，其他任务只是看起来并行。 | 波次监督器使用非阻塞轮询，哪个任务先返回就先推进哪个任务。 | **任务真正重叠执行，协调层不会被单个任务拖住。** |
| 一个坏候选会污染后续任务使用的暂存状态。 | 物化或验收失败时恢复物化前的 SHA，并阻塞该任务包。 | **失败被限制在当前任务内，不会级联污染整批工作。** |
| 计划或结果审查脱离了产生它的那次运行，事后很难核对。 | C2C 审查包有边界、先脱敏、记录哈希，并绑定到 Run 和 Stage；外部回应明确标为不可信输入。 | **审查有身份、有证据链，也有清楚的信任边界。** |
| 进程重启或任务失败后，很难还原事情发生的顺序。 | SQLite 状态、H0 日志、内容寻址 Blob、会话绑定和恢复命令共同保存运行记录。 | **可以查清发生过什么，也能从降级历史继续恢复。** |

## 快速开始

### 让 Agent 引导安装

把仓库交给 ZCode 或其他编码智能体，并要求它先阅读仓库说明：

~~~text
请从
https://github.com/LEO001020/zcode-loop-orchestra
安装 ZCode LOOP Orchestra。
先检查我的 Windows 环境，展示试运行结果和备份方案；得到我确认后，
再启用受管理的 hooks 并验证安装结果。不要读取、显示或修改任何 API 凭据。
~~~

### 手动安装

~~~powershell
git clone https://github.com/LEO001020/zcode-loop-orchestra.git
cd zcode-loop-orchestra

python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m zloop.cli doctor
.venv\Scripts\python -m zloop.cli project attach
.venv\Scripts\python -m zloop.cli install
~~~

安装器适合交给 Agent 执行：它会检查环境、备份受管理的 hooks 配置、只改动自己负责的文件，并提供卸载路径。仓库不包含 ZCode 二进制文件，也不包含任何提供方凭据。

## 先看完整的产品流程

这张图展示一次运行真正经历的路径：用户目标进入 Run，任务包经过波次监督，执行差异成为候选结果，审查与晋升门逐一通过，最后由人触发发布。右侧的控制与证据主干说明了为什么这条流程可以被核对和恢复。

![ZCode LOOP 完整产品架构：用户目标、Run 与 Stage、任务波次、监督器、独立启动工作目录、物化与验收、审查记录、受控晋升、持久化状态、证据和人工发布](docs/assets/architecture-overview.zh-CN.svg)

## 再看五步简化图

如果你只想先理解产品做什么，看下面五个大步骤即可；具体实现名称留在完整图和后文中。

![ZCode LOOP 五步产品流程：输入目标、生成有边界的任务包、持续推进已就绪工作、验收候选结果，并由人晋升发布](docs/assets/architecture-simplified.zh-CN.svg)

## 工作方式

### 用户实际会执行的主线

~~~text
zloop run start "Implement the next bounded change"
zloop stage begin --objective "Implement the next bounded change" --risk NORMAL
zloop wave propose packets.json
zloop wave start W1 --backend codex
zloop stage promote S01
~~~

<code>stage promote</code> 故意放在最后，并且由人触发。对于 HIGH 或 CRITICAL 风险的工作，当前策略可能要求先记录结果审查包，晋升命令才会被允许。

### 各个部分到底做什么

- **Run 与 Stage：** 把目标、基线引用、风险底线和阶段版本写入控制库。
- **Wave 提案：** 在启动任何 worker 之前，检查任务包结构、依赖引用、风险策略和写入范围冲突。
- **波次监督器：** 先记录启动意图，再启动已就绪任务；使用非阻塞轮询收取报告，并结算每个任务的终态。
- **启动工作区：** 为每次启动提供独立工作目录，返回有边界的差异供集成使用。规范分支不是 worker 的直接写入目标。
- **物化：** 把差异重新应用到 staging，检查授权路径，运行宿主验收命令；通过后形成带来源信息的候选提交，失败则恢复 staging。
- **控制库：** 在 <code>control.sqlite3</code> 中持久化 Run、Stage、Packet、Attempt、Launch、版本和生命周期状态。
- **证据路径：** <code>zloop.hook</code> 捕获有作用域的生命周期事件，写入 H0 journal；较大的或敏感的内容先脱敏，再通过内容寻址 Blob 保存。
- **晋升：** 校验预期状态和 Git 身份，使用 CAS 保护的 fast-forward-only 晋升；最终命令仍然由人执行。

### 模型与审查边界

根对话规划、worker 执行和结果审查是三个不同的职责。只要宿主配置支持独立路由，就可以分别为这些职责选择模型；第三方模型需要通过该安装环境支持的 Codex 兼容网关接入。

C2C 层故意保持窄化：<code>zloop c2c prepare</code> 生成有边界、已脱敏的审查包，<code>zloop c2c record</code> 保存带有任务包哈希和身份字段的回应。当前 C2C 模块不会自动发起 HTTP 请求或调用模型，而是把外部回应记录为 <code>external_untrusted</code>。

### 用最直白的话解释失败恢复

1. worker 报告自己产生的变更。
2. LOOP 检查实际改了哪些文件，以及这些文件是否在允许范围内。
3. LOOP 把差异重新应用到 staging，并在那里重新运行验收命令。
4. 如果检查失败，staging 回到已知的物化前 SHA，该任务包进入阻塞状态。
5. 如果候选结果符合条件，流程才会继续经过审查和晋升门。
6. 是否执行最终晋升命令，由人决定。

## 安装与运行

### 环境要求

- Windows 10 或 11，x64。
- Python 3.11 或更高版本。
- Git，以及支持本地 hooks 的 ZCode 安装。
- 可用的 ZCode 登录状态；如果所选后端需要，也可以使用 Codex 兼容模型网关。

### 常用命令

~~~powershell
zloop run start "Implement the next bounded change"
zloop stage begin --objective "Implement the next bounded change" --risk HIGH
zloop c2c prepare --role plan --file plan.txt
zloop wave propose packets.json
zloop wave start W1 --backend codex
zloop c2c prepare --role result --file result.txt
zloop c2c record --c2c <C2C_ID> --file auditor-response.txt
zloop stage promote S01
~~~

使用 <code>zloop --help</code> 查看当前安装提供的完整选项，也可以阅读 [docs/](docs/) 下的操作指南。本地运行测试：

~~~powershell
.venv\Scripts\python -m pytest tests -q
~~~

并发规模取决于 worker 后端、模型提供方和本机资源；本项目不把某次实验中的数字写成所有机器都必须达到的保证。

## 仓库结构

~~~text
src/zloop/        控制平面、worker 后端、生命周期、证据与 CLI
spec/             架构契约、决策记录、不变量和进度记录
tests/            单元、集成、并发与混沌测试
plugin/           ZCode 插件分发文件与 hooks
docs/             操作文档和架构资料
tools/prompt-lab/ 提示词实验与上下文预算检查
pyproject.toml    构建元数据与依赖
~~~

## 安全与边界

- hooks 以当前用户身份运行，不会自动提升权限。
- 提供方凭据保存在用户自己的 ZCode 或网关配置中，不进入本仓库。
- 路径检查保护的是集成边界；它不会把较宽的 worker 沙箱变成操作系统级安全边界。
- 机械门禁和审计门可以阻断或升级，但不会授予发布权限。
- 本项目是单机生命周期控制器，不是分布式调度器。

## 许可证

ZCode LOOP Orchestra 基于 [MIT License](LICENSE) 发布。
