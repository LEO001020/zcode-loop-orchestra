<!-- size-justified: project landing page; raw logs and generated state are excluded. -->
<div align="center">

# ZCode LOOP Orchestra

**A local control layer that keeps ZCode agent work moving, verifiable, and human-released.**

[![CI](https://github.com/LEO001020/zcode-loop-orchestra/actions/workflows/ci.yml/badge.svg)](https://github.com/LEO001020/zcode-loop-orchestra/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-0078D4.svg)](docs/INSTALL.zh-CN.md)

[The control loop](#the-control-loop-is-the-product) · [What you get](#what-you-get) · [Quickstart](#quickstart) · [How it works](#how-it-works) · [中文](README.zh-CN.md)

</div>

ZCode can launch agents. LOOP adds the missing control layer around that launch: it records a run, turns work into bounded packets, starts ready work without blocking on one worker, checks returned changes in staging, and leaves the final promotion command to a person.

In practical terms, you give LOOP a project goal and a packet manifest. The system keeps the submitted work moving inside a supervised wave, rejects unsafe or stale results, and leaves a durable record of what happened. It is a local Python CLI plus managed ZCode hooks; it does not replace ZCode or ship the ZCode binary.

> **Agents do the work. Code owns the state. Mechanical checks decide what is admissible. A human decides what ships.**

## The control loop is the product

A native harness is good at starting an agent. A real engineering run also needs to answer what happens when a worker finishes, fails, writes the wrong file, or returns after the plan has changed. LOOP makes those transitions explicit:

1. The root conversation produces a bounded decision skeleton instead of doing the batch work itself.
2. Every packet states its goal, authorized paths, acceptance commands, and constraints.
3. A DAG gate rejects cyclic dependencies and overlapping write scopes before dispatch.
4. A cold wave supervisor starts only ready packets and uses non-blocking <code>poll()</code> to collect reports.
5. Materialization reapplies a worker delta to private staging, re-runs host acceptance, and restores the previous staging SHA on failure.
6. An audit record can block, request rework, or escalate; it cannot publish.
7. Only a person runs the final promotion command. The canonical branch stays outside the worker lifecycle.
8. Waiting, polling, counting, routine retries, and state transitions live in code and durable state, not in extra coordinator turns.

This is the LOOP promise in one line: **keep useful work moving, keep bad changes contained, and keep release authority human.**

## What you get

The table below explains the design in the order a user experiences it: a limitation in a native harness, the concrete change LOOP makes, and the benefit of that change.

| Native harness limitation | What LOOP changes | What you gain |
|---|---|---|
| A submitted batch thins out as workers finish; the conversation has to be prompted to continue. | The supervisor tracks pending and running packets and starts the next ready packet within the wave. | **Useful work keeps moving** while bounded work remains. |
| Several agents share mutable state or the canonical checkout. | Each launch gets its own working directory; integration happens in a separate staging worktree. | **One worker cannot directly overwrite another worker’s integration state.** |
| A worker’s “done” message is treated as the result. | LOOP enumerates the returned delta, checks its path scope, reapplies it to staging, and runs host acceptance commands again. | **The candidate is checked where it will be integrated.** |
| A synchronous driver waits on the first task and makes the rest look parallel. | A cold supervisor uses non-blocking polling and advances independent packets as reports arrive. | **Real overlap without blocking the coordinator on one task.** |
| One failed candidate leaves a poisoned staging state for later work. | Failed materialization or acceptance restores the pre-materialization SHA and blocks that packet. | **Failure stays local instead of cascading through the wave.** |
| A plan or result review is detached from the exact run that produced it. | C2C packets are bounded, redacted, hash-recorded, and tied to the run and stage; the response is recorded as external and untrusted. | **Review has an identity, an audit trail, and a clear trust boundary.** |
| Restarting after a failure loses the sequence of events. | SQLite state, H0 journal entries, content-addressed blobs, bindings, and recovery commands are kept together. | **You can inspect what happened and recover from a degraded history.** |

## Quickstart

### Agent-guided installation

Give the repository to ZCode or another coding agent and ask it to read the repository instructions first:

~~~text
Install ZCode LOOP Orchestra from
https://github.com/LEO001020/zcode-loop-orchestra.
Inspect my Windows environment, show the dry run and backup plan, wait for my
approval, then activate the managed hooks and verify the installation.
Do not read, print, or change my API credentials.
~~~

### Direct installation

~~~powershell
git clone https://github.com/LEO001020/zcode-loop-orchestra.git
cd zcode-loop-orchestra

python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m zloop.cli doctor
.venv\Scripts\python -m zloop.cli project attach
.venv\Scripts\python -m zloop.cli install
~~~

The installer is deliberately agent-friendly: it checks the environment, backs up managed hook configuration, changes only its managed files, and provides an uninstall path. It does not contain the ZCode executable or any provider credential.

## See the product first

This is the full product view: one user objective becomes a supervised wave, a checked candidate, an audit record, and a human-triggered promotion. The right-hand spine shows the state, identity, and evidence that make the flow recoverable.

![ZCode LOOP full product architecture: objective, run and stage, packet wave, supervisor, isolated launch workspace, materialization and acceptance, audit record, controlled promotion, durable state, evidence, and human release](docs/assets/architecture-overview.en.svg)

## The five-step version

If you only want the idea, follow the five large steps below. The detailed names are intentionally kept out of this view.

![ZCode LOOP simplified product flow: enter a goal, make bounded packets, keep ready work moving, verify candidates, and let a human promote the result](docs/assets/architecture-simplified.en.svg)

## How it works

### The user-visible path

~~~text
zloop run start "Implement the next bounded change"
zloop stage begin --objective "Implement the next bounded change" --risk NORMAL
zloop wave propose packets.json
zloop wave start W1 --backend codex
zloop stage promote S01
~~~

<code>stage promote</code> is intentionally the last human-triggered step. For HIGH or CRITICAL work, the configured policy may require a recorded result-review packet before that command is allowed.

### What the main components actually do

- **Run and Stage:** record the objective, base reference, risk floor, and stage revision in the control database.
- **Wave proposal:** validate packet shape, dependency references, risk policy, and write-scope conflicts before any worker is started.
- **Wave Supervisor:** persist launch intent, start ready packets, poll without blocking on one handle, collect reports, and settle terminal states.
- **Launch workspace:** give each launch a separate working directory and return a bounded delta for integration. The canonical branch is not a worker write target.
- **Materialization:** reapply that delta to staging, check authorized paths, run host acceptance commands, create a provenance-bearing candidate commit, or restore staging on failure.
- **Control database:** persist Run, Stage, Packet, Attempt, Launch, revision, and lifecycle state in <code>control.sqlite3</code>.
- **Evidence path:** <code>zloop.hook</code> captures scoped lifecycle events into an H0 journal; large or sensitive payloads are redacted and stored through content-addressed blobs.
- **Promotion:** verify the expected state and Git identity, then use a CAS-protected fast-forward-only promotion. The final command is still issued by a person.

### Model and audit boundaries

Root planning, worker execution, and result review are separate responsibilities. When the host setup exposes independent routing, choose the model for each responsibility separately; a third-party provider must be exposed through the Codex-compatible gateway supported by that setup.

The C2C layer is deliberately narrow. <code>zloop c2c prepare</code> creates a bounded, redacted packet, and <code>zloop c2c record</code> stores the response with its packet hash and identity fields. The current C2C module does not make an automatic HTTP or model call; it records an external response as <code>external_untrusted</code>.

### Failure and recovery in plain language

1. A worker reports a change.
2. LOOP checks which files changed and whether they are in scope.
3. LOOP reapplies the change to staging and runs the acceptance commands there.
4. If a check fails, staging returns to the known pre-materialization SHA and the packet is blocked.
5. If the candidate is admissible, the audit and promotion gates can continue.
6. A person decides whether to run the final promotion command.

## Install and operate

### Requirements

- Windows 10 or 11 x64.
- Python 3.11 or newer.
- Git and a compatible ZCode installation with local hook support.
- A working ZCode login or a Codex-compatible model gateway when the selected backend needs one.

### Typical commands

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

Use <code>zloop --help</code> and the guides under [docs/](docs/) for the exact options available in your installation. Run the test suite locally with:

~~~powershell
.venv\Scripts\python -m pytest tests -q
~~~

The package is designed for a single Windows machine and a controlled local workspace. Provider availability, model limits, and the number of workers a machine can sustain remain deployment-specific.

## Repository layout

~~~text
src/zloop/        Control plane, worker backend, lifecycle, evidence, and CLI
spec/             Architecture contracts, decisions, invariants, and progress records
tests/            Unit, integration, concurrency, and chaos coverage
plugin/           ZCode plugin distribution files and hooks
docs/             Operational documentation and architecture material
tools/prompt-lab/ Prompt experiments and context-budget checks
pyproject.toml    Build metadata and dependencies
~~~

## Security and limitations

- Hooks run as the current user and do not elevate privileges.
- Provider credentials stay in the user’s authenticated ZCode or gateway setup; they are not part of this repository.
- Path checks protect the integration boundary. They do not turn a broad worker sandbox into an operating-system security boundary.
- Mechanical and audit gates can block or escalate, but they do not grant publication authority.
- The project is a local lifecycle controller, not a distributed scheduler.

## License

ZCode LOOP Orchestra is released under the [MIT License](LICENSE).
