<!-- size-justified: project landing page; raw logs and generated state are excluded. -->
<div align="center">

# ZCode LOOP Orchestra

**Turn one ZCode conversation into a restartable multi-agent engineering workflow.**

[![CI](https://github.com/LEO001020/zcode-loop-orchestra/actions/workflows/ci.yml/badge.svg)](https://github.com/LEO001020/zcode-loop-orchestra/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-0078D4.svg)](docs/INSTALL.zh-CN.md)

[Highlights](#highlights) · [Control loop](#the-control-loop-is-the-product) · [Quickstart](#quickstart) · [Why ZLoop](#why-zloop-exists) · [Architecture](#how-zloop-works) · [中文](README.zh-CN.md) · [Docs](docs/)

</div>

ZCode can reason well inside one conversation. But a long engineering project cannot safely live in conversation memory alone: agents finish at different times, results may arrive late, parallel changes need isolation, and every accepted result must be reproducible.

ZLoop is a local control layer for ZCode. It saves job state outside the chat, schedules ready tasks, gives each coding agent a separate workspace, rechecks returned changes in a clean integration area, and preserves the evidence needed to continue after an interruption. ZCode keeps making decisions; code keeps the workflow coherent; a human controls the final merge.

## Highlights

- **Persistent agent memory:** captured events, compact checkpoints, and exact evidence lookup survive context compression, restarts, and interrupted sessions.
- **Parallel work without shared-file collisions:** each worker receives a separate workspace; accepted changes are brought back through one controlled staging path.
- **Dependency-aware scheduling:** tasks run when their prerequisites are ready instead of relying on the main conversation to remember what should start next.
- **Checks based on real changes:** ZLoop inspects the files that actually changed and reruns acceptance commands in its integration area. A worker saying “done” is never enough.
- **Failure containment and recovery:** failed candidates restore known staging state; late or outdated results are recorded but cannot overwrite a newer plan.
- **Independent model review:** planning, execution, and review can use different model families where the host supports it, reducing the risk of one model checking its own assumptions.
- **Source-backed research:** optional research lanes collect external facts with provenance in a separate workspace, then return a bounded evidence summary to the main agent.
- **Human-controlled release:** agents may propose, execute, test, and challenge a result, but the final merge remains an explicit human action.

![ZCode LOOP simplified architecture: user goal, main ZCode agent, task graph, automatic scheduling, persistent memory, separate coding and research agents, real-result checks, independent AI review, recovery, and human-approved merge](docs/assets/architecture-simplified.en.svg)

<p align="center"><sub>The main agent decides. ZLoop remembers and schedules. Agents work separately. Real checks and an independent AI review the result. A human merges.</sub></p>

## The control loop is the product

Launching several agents is easy. Keeping a real engineering job coherent after those agents finish, fail, return late, or modify the wrong files is the harder problem.

> **In plain language:** give ZLoop one project goal and a list of tasks. It starts work when dependencies are ready, keeps each worker isolated, verifies the returned changes, and resumes from saved state when something is interrupted.
>
> **What that means for you:** the main conversation no longer has to act as a fragile task database. Parallel work is safer, failures stay local, and you can inspect why a candidate was accepted or rejected before anything reaches the main branch.

The loop follows eight rules:

1. The main ZCode agent decides what the project needs; it does not have to perform every implementation task itself.
2. Every task states its goal, allowed files, checks, dependencies, and limits.
3. The task graph rejects circular dependencies and conflicting write areas before workers start.
4. Each worker receives a fresh workspace and a unique execution identity.
5. Code—not extra model turns—handles waiting, scheduling, ordinary retries, cancellation, and lifecycle state.
6. Returned changes are combined in a safe integration workspace and checked again by the host.
7. Independent review may approve, request rework, or raise a concern; it cannot publish.
8. A human triggers the final merge after the candidate, repository state, and release checks agree.

**Models make judgments. Code owns state. Repeatable checks decide what passes. Humans release.**

## Quickstart

Give this repository to ZCode or another coding agent:

~~~text
Install ZCode LOOP Orchestra from
https://github.com/LEO001020/zcode-loop-orchestra.
Read the repository instructions first. Inspect my Windows environment,
show the dry run and backup plan, wait for my approval, then install the
managed hooks and verify the result. Never read, print, or change API credentials.
~~~

Prefer to install manually? Continue to [Installation](#installation), or read the [Chinese installation guide](docs/INSTALL.zh-CN.md).

## Why ZLoop exists

Every major component answers a failure mode that appears in long-running agent work:

| What goes wrong without a control layer | What ZLoop changes | Practical benefit |
|---|---|---|
| Context is compressed, a session restarts, or the main agent forgets an earlier decision. | Save observable history, bounded checkpoints, and exact evidence references outside the model context. | **Resume from durable state instead of reconstructing the job from memory.** |
| Parallel agents edit the same checkout or consume one another’s unfinished state. | Give each worker a separate workspace and integrate through one safe workspace. | **Workers do not overwrite one another or write directly to the main branch.** |
| The main conversation becomes the scheduler and repeatedly checks which task is finished. | Store task dependencies and lifecycle state in code, then advance ready tasks without blocking on one worker. | **Real parallel progress without repeated “continue” prompts.** |
| A worker reports success even though it changed the wrong files or its tests are stale. | Inspect the real delta, enforce the allowed-file boundary, and rerun checks where the change will be integrated. | **Acceptance follows evidence, not confidence.** |
| A result arrives after the task or plan has changed. | Match every result to the current task version and execution identity. | **Late results become evidence, not accidental writes into the new plan.** |
| One model both creates and approves the same solution. | Allow a separately routed reviewer to challenge the plan or result using a bounded review packet. | **Reduce self-review blind spots without giving the reviewer release authority.** |
| External research is mixed with trusted project state. | Run research separately, preserve source provenance, and mark external content as untrusted input. | **Use fresh information without letting web content control the workflow.** |
| An agent can merge as soon as it believes the task is complete. | Require a clean, checked candidate and an explicit human promotion command. | **Keep irreversible release authority with the maintainer.** |

## How ZLoop works

![ZCode LOOP full architecture: user objective, main ZCode agent, work plan, task graph, automatic scheduling, parallel coding agents, research with sources, real-result checks, independent AI review, safe integration, persistent memory, recovery, and human-approved merge](docs/assets/architecture-overview.en.svg)

The diagram uses familiar AI engineering terms. Internally, the CLI gives these boundaries stable IDs so a crashed process or late result cannot be mistaken for current work.

- **Main agent:** understands the objective, chooses the next plan, and resolves genuine uncertainty.
- **Task graph and scheduler:** hold dependencies, start ready work, and keep routine lifecycle decisions out of the model conversation.
- **Worker agents:** implement bounded tasks in separate workspaces. The current default execution backend uses the Codex SDK.
- **Repeatable verification:** checks the actual diff, allowed paths, tests, hashes, repository state, and other acceptance evidence.
- **Independent review:** prepares only the relevant plan or result for a separate reviewer and records the response as external, untrusted evidence. The current command does not automatically call a model.
- **Persistent memory and recovery:** preserve observable events and compact checkpoints, support exact lookup, and prefer current repository reality over old summaries.
- **Safe integration and human release:** build a checked candidate away from the main branch, then require an explicit human-triggered fast-forward merge.

### Model choice

The main agent, worker agents, and reviewer are separate responsibilities. When the host configuration supports independent routing, each can use a different model or provider. Third-party models must be exposed through a compatible gateway supported by the local ZCode or Codex setup.

The project does not assume that “different model” automatically means “independent.” Review identity and available evidence are recorded when observable; unknown routing remains unknown.

> [!NOTE]
> ZCode LOOP Orchestra is an independent community project. It is a local Python control layer plus managed ZCode hooks; it does not distribute ZCode, patch its binary, or include provider credentials.

## Installation

### Requirements

- Windows 10 or 11, x64
- Python 3.11 or newer
- Git
- A working ZCode installation with local hook support
- An authenticated Codex-compatible backend when worker execution needs one

### Install

~~~powershell
git clone https://github.com/LEO001020/zcode-loop-orchestra.git
cd zcode-loop-orchestra

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m zloop.cli doctor
.\.venv\Scripts\python.exe -m zloop.cli project attach
.\.venv\Scripts\python.exe -m zloop.cli install
~~~

The installer checks the environment, backs up the hook configuration it manages, and provides an uninstall path. It does not inspect or configure API keys.

### Run a bounded wave

~~~powershell
zloop run start "Implement the next bounded change"
zloop stage begin --objective "Implement the next bounded change" --risk NORMAL
zloop wave propose packets.json
zloop wave start W1 --backend codex
zloop stage promote S01
~~~

These command names are implementation details:

| CLI term | Plain meaning |
|---|---|
| Run | One project objective |
| Stage | One bounded engineering phase |
| Packet | One worker task |
| Wave | A dependency-aware group of tasks |
| Materialize | Reapply and verify a worker’s changes in staging |
| Promote | Perform the final human-triggered merge |

For high-risk work, policy may require a recorded planning review before workers start and a result review before promotion. See [docs/](docs/) for recovery, research, and review commands.

### Uninstall

~~~powershell
.\.venv\Scripts\python.exe -m zloop.cli uninstall
~~~

## Repository layout

~~~text
src/zloop/        control, scheduling, workers, memory, evidence, and CLI
plugin/           optional ZCode plugin files and agent roles
docs/             installation, operation, and architecture guides
spec/             detailed engineering contracts and design decisions
tests/            unit, integration, concurrency, and failure tests
tools/prompt-lab/ prompt and context-budget experiments
~~~

## Verification

~~~powershell
$env:PYTHONPATH = (Resolve-Path "src").Path
python -m pytest tests -q
~~~

CI and local tests cover lifecycle state, task boundaries, stale-result rejection, staging rollback, evidence capture, recovery, installation, and promotion safety. Live provider capacity and model routing remain local environment checks.

## Security and limitations

- Hooks run as the current user and do not elevate privileges.
- Credentials stay in the user’s authenticated ZCode or gateway configuration.
- Workspace and path checks protect the integration boundary; they are not an operating-system security sandbox.
- Web research and external review are always treated as untrusted input.
- Agents and reviewers cannot authorize publication.
- The current implementation is a single-machine control layer, not a distributed scheduler.
- Sustainable worker concurrency depends on the backend, model provider, repository, and local hardware.

## License

MIT © 2026 [LEO001020](https://github.com/LEO001020). See [LICENSE](LICENSE) and [CONTRIBUTING.md](CONTRIBUTING.md).
