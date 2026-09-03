# ZCode LOOP Orchestra 安装指南

这是一套面向 Windows 的本地控制层。安装器只管理 ZCode hooks 和 LOOP 自己的配置，不会替换 ZCode 客户端，也不会读取或保存模型提供方凭据。

## 运行环境

- Windows 10 或 11，x64。
- Python 3.11 或更高版本，并包含标准库 <code>sqlite3</code>。
- Git。
- 已经可以正常运行、且支持本地 hooks 的 ZCode 安装。

模型提供方、登录状态和 worker 能够维持的并发量由本机环境决定；安装器不会替用户配置凭据。

## 安装

在 PowerShell 中执行：

~~~powershell
git clone https://github.com/LEO001020/zcode-loop-orchestra.git
Set-Location zcode-loop-orchestra

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m zloop.cli doctor
.\.venv\Scripts\python.exe -m zloop.cli project attach
.\.venv\Scripts\python.exe -m zloop.cli install
~~~

<code>doctor</code> 会先报告当前环境和 hooks 状态。<code>project attach</code> 将当前 Git 工程登记到 LOOP 的本地控制库；<code>install</code> 只安装受 LOOP 管理的 hooks。安装前请让 Agent 展示检查结果和备份方案，并由你确认后再继续。

## 可选：安装角色文件

仓库中的 <code>plugin/agents/</code> 文件是可选的 ZCode 角色提示。是否使用它们、以及每个角色实际使用哪个模型，由你的 ZCode 配置决定；第三方模型应通过该环境支持的 Codex 兼容网关接入。

如果你的 ZCode 安装使用 <code>%USERPROFILE%\.zcode\agents\</code> 作为用户级角色目录，可在 PowerShell 中执行：

~~~powershell
$agents = Join-Path $env:USERPROFILE ".zcode\agents"
New-Item -ItemType Directory -Force $agents | Out-Null
Copy-Item plugin\agents\zloop-worker.md $agents
Copy-Item plugin\agents\zloop-auditor.md $agents
~~~

请先确认本机 ZCode 的角色目录和配置格式，再复制文件。不要把 API key 写进仓库、角色文件或命令行历史。

## 运行一波任务

典型顺序如下：

~~~powershell
zloop run start "Implement the next bounded change"
zloop stage begin --objective "Implement the next bounded change" --risk NORMAL
zloop wave propose packets.json
zloop wave start W1 --backend codex
zloop stage promote S01
~~~

对于 HIGH 或 CRITICAL 风险，策略可能要求先完成计划审查和结果审查。C2C 命令只负责生成有边界、已脱敏的审查包，以及记录外部回应；它不会自动向某个模型发起 HTTP 请求。

## 卸载与恢复

查看当前 hooks：

~~~powershell
.\.venv\Scripts\python.exe -m zloop.cli doctor
~~~

移除 LOOP 管理的 hooks：

~~~powershell
.\.venv\Scripts\python.exe -m zloop.cli uninstall
~~~

卸载前后都建议运行 <code>doctor</code>，并确认 ZCode 恢复到你的预期状态。LOOP 的控制库和历史证据是本地数据，不会因为卸载 hooks 自动变成公开内容；如需清理，请先确认备份和保留策略。

## 开发者验证

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests -q
~~~

不要把测试收集数量写死进安装说明；测试集会随仓库变化，CI 状态以当前运行结果为准。
