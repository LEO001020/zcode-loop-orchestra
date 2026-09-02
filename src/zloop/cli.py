"""zloop.cli — root-side command surface (VOL-22 §9 root loop, VOL-05 §4 bind-token).

Exit codes (contract):
  0 ok · 2 usage · 3 S_DEGRADED (S degraded, fail-closed I4) ·
  4 bind-token wait timeout (P2-13 foreground constraint) ·
  5 blocked (precondition not met).

Parallel modules (zloop.history / zloop.checkpoint / zloop.install /
zloop.hook) are imported lazily inside their command handlers; when one is
absent the command prints "module not available (parallel integration
pending)" and exits 0 — never a crash, and never a fake success claim about
a plane that is not there.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import __version__, db, ids, paths

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_S_DEGRADED = 3
EXIT_BIND_TIMEOUT = 4
EXIT_BLOCKED = 5

MODULE_UNAVAILABLE = "module not available (parallel integration pending)"
FOREGROUND_NOTE = "run this in FOREGROUND so the PostToolUse hook can claim the token"
LEGACY_CODEX_REQUIREMENTS = Path("C:/ProgramData/OpenAI/Codex/requirements.toml")
CLAIM_TTL_S = 120  # I32: one-time bind token, TTL 120s


class CliError(Exception):
    """Command failed with a specific exit code (message goes to stderr)."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


# ---- small helpers --------------------------------------------------------


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def _lazy_import(module_name: str):
    """Import a parallel module tolerantly; None when absent (integration pending)."""
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


def _git_root(cwd: Optional[Path] = None) -> Path:
    cwd = Path(cwd or Path.cwd())
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           cwd=str(cwd), capture_output=True, text=True, timeout=15)
        if p.returncode == 0 and p.stdout.strip():
            return Path(p.stdout.strip()).resolve()
    except Exception:
        pass
    return cwd.resolve()


def _require_project() -> tuple[str, Path]:
    """Resolve the project for the cwd git root. Never guesses by cwd for
    binding purposes — the project must already be registered (I32)."""
    root = _git_root()
    pid = paths.find_project_by_git_root(str(root))
    if pid is None:
        raise CliError(EXIT_BLOCKED,
                       f"no zloop project registered for {root} — "
                       f"run 'zloop project attach' or 'zloop run start' first")
    return pid, paths.project_dir(pid)


def _open_store(pdir: Path, project_id: str) -> tuple["db.ControlStore", Any]:
    conn = db.connect(pdir, create=True)
    return db.ControlStore(pdir, conn, project_id=project_id), conn


def _seconds_to_expiry(expires_at: Optional[str]) -> Optional[int]:
    if not expires_at:
        return None
    dt = ids.parse_iso(expires_at)
    if dt is None:
        return None
    return int((dt - datetime.now(timezone.utc)).total_seconds())


# ---- bind-token flow (VOL-05 §4) ------------------------------------------


def _emit_bind_token(store: "db.ControlStore", conn: Any, *, purpose: str,
                     run_id: Optional[str], project_id: str,
                     extra: Optional[dict] = None) -> str:
    """Create the one-time claim and print the bind-token block.

    Output order is exact: marker line first (truncation-safe, ≤80 chars),
    then one JSON line. Nothing may be printed before the marker."""
    nonce = store.create_claim(purpose=purpose, run_id=run_id, ttl_s=CLAIM_TTL_S)
    row = conn.execute(
        "SELECT expires_at FROM pending_binding_claims WHERE nonce=?",
        (nonce,)).fetchone()
    expires_at = row["expires_at"] if row is not None else ids.iso_plus(CLAIM_TTL_S)
    print(f"ZLOOP_BIND_TOKEN={nonce}", flush=True)
    payload: dict[str, Any] = {
        "run_id": run_id,
        "project_id": project_id,
        "claim_expires_at": expires_at,
        "note": FOREGROUND_NOTE,
    }
    if extra:
        payload.update(extra)
    _print_json(payload)
    return nonce


def _wait_for_claim(conn: Any, nonce: str, wait_s: int, *, run_id: Optional[str],
                    project_id: str) -> int:
    """Poll S until the PostToolUse hook claims the nonce (VOL-05 §4 step 4).

    Success -> {"bound": true, ...} + exit 0. Timeout/expiry -> WARNING on
    stderr + exit 4 (P2-13: a background Bash tool_response never contains
    the marker, so the token cannot be claimed)."""
    deadline = time.monotonic() + max(0, wait_s)
    while True:
        row = conn.execute(
            "SELECT claimed_at, claimed_by_session, expires_at"
            " FROM pending_binding_claims WHERE nonce=?", (nonce,)).fetchone()
        if row is not None and row["claimed_at"]:
            _print_json({"bound": True, "nonce": nonce,
                         "claimed_by_session": row["claimed_by_session"],
                         "run_id": run_id, "project_id": project_id,
                         "claim_expires_at": row["expires_at"]})
            return EXIT_OK
        if row is not None:
            left = _seconds_to_expiry(row["expires_at"])
            if left is not None and left <= 0:
                break  # expired unclaimed — waiting longer is pointless
        if time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    print("WARNING: token not claimed — likely ran in background; re-run in foreground",
          file=sys.stderr)
    return EXIT_BIND_TIMEOUT


# ---- commands -------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"data root: {paths.zloop_data_root()}")
    prof = db.journal_profile()
    print("journal profile: " + json.dumps(prof, ensure_ascii=False))
    projects = paths.load_registry().get("projects", {})
    print(f"registry: {len(projects)} project(s)")
    for pid in sorted(projects):
        rec = projects[pid]
        name = rec.get("display_name") or ""
        try:
            conn = db.connect(paths.project_dir(pid), create=False)
            try:
                row = conn.execute("PRAGMA quick_check").fetchone()
                qc = str(row[0]).lower() if row is not None else "unknown"
            finally:
                conn.close()
            print(f"project {pid} ({name}): quick_check={qc} "
                  f"journal={prof['journal_mode']}")
        except db.SError as e:
            if "control DB missing" in str(e):
                print(f"project {pid} ({name}): no control DB yet (no runs)")
            else:
                print(f"project {pid} ({name}): S_DEGRADED ({e})")
    # hook deployment status (parallel module, lazy)
    zinstall = _lazy_import("zloop.install")
    if zinstall is None:
        print(f"hooks: {MODULE_UNAVAILABLE}")
    else:
        try:
            print("hooks: " + json.dumps(zinstall.hook_status(), ensure_ascii=False))
        except Exception as e:  # diagnostics must not die on the parallel module
            print(f"hooks: ERROR ({e})")
    if LEGACY_CODEX_REQUIREMENTS.exists():
        print("WARN: old LOOP machine-wide Codex hooks present (P-HYG1)")
    return EXIT_OK


def cmd_project(args: argparse.Namespace) -> int:
    if args.cmd == "attach":
        rec = db.register_project_from_git(args.git_root)
        _print_json(rec)
        return EXIT_OK
    projects = paths.load_registry().get("projects", {})
    _print_json([{"project_id": pid, **rec} for pid, rec in sorted(projects.items())])
    return EXIT_OK


def cmd_run_start(args: argparse.Namespace) -> int:
    rec = db.register_project_from_git()  # ensure registered for cwd git root
    pid = rec["project_id"]
    pdir = paths.ensure_project_layout(pid)
    conn = None
    try:
        with db.RunLock(pdir):  # single controller (I43)
            conn = db.connect(pdir, create=True)
            store = db.ControlStore(pdir, conn, project_id=pid)
            run_id = store.create_run(args.objective)
            nonce = _emit_bind_token(store, conn, purpose="run_start",
                                     run_id=run_id, project_id=pid)
        # run lock released; the read-only claim polling continues without it
        if args.wait_claim is not None:
            return _wait_for_claim(conn, nonce, args.wait_claim,
                                   run_id=run_id, project_id=pid)
        return EXIT_OK
    finally:
        if conn is not None:
            conn.close()


def cmd_run_close(args: argparse.Namespace) -> int:
    pid, pdir = _require_project()
    with db.RunLock(pdir):
        conn = db.connect(pdir, create=True)
        try:
            store = db.ControlStore(pdir, conn, project_id=pid)
            run = store.run(args.run_id)
            if run is None:
                raise CliError(EXIT_BLOCKED,
                               f"unknown run {args.run_id} in project {pid}")
            if run["state"] == "CLOSED":
                _print_json({"run_id": args.run_id, "state": "CLOSED",
                             "note": "already closed"})
                return EXIT_OK
            if run["state"] != "ACTIVE":
                raise CliError(EXIT_BLOCKED,
                               f"run {args.run_id} is {run['state']}; "
                               f"only ACTIVE runs can be closed")
            store.close_run(args.run_id)
            closed = store.run(args.run_id)
            _print_json({"run_id": args.run_id, "state": closed["state"],
                         "closed_at": closed["closed_at"]})
            return EXIT_OK
        finally:
            conn.close()


def _runs_report(pid: str, pdir: Path, run_id: Optional[str]) -> int:
    conn = db.connect(pdir, create=True)
    try:
        store = db.ControlStore(pdir, conn, project_id=pid)
        if run_id:
            run = store.run(run_id)
            if run is None:
                raise CliError(EXIT_BLOCKED, f"unknown run {run_id} in project {pid}")
            _print_json(run)
        else:
            _print_json(store.runs())
        return EXIT_OK
    finally:
        conn.close()


def cmd_run_status(args: argparse.Namespace) -> int:
    pid, pdir = _require_project()
    return _runs_report(pid, pdir, args.run_id)


def cmd_run_list(args: argparse.Namespace) -> int:
    pid, pdir = _require_project()
    return _runs_report(pid, pdir, None)


def cmd_attach(args: argparse.Namespace) -> int:
    rec = db.register_project_from_git()  # like run start: ensure registered
    pid = rec["project_id"]
    pdir = paths.ensure_project_layout(pid)
    conn = None
    try:
        with db.RunLock(pdir):
            conn = db.connect(pdir, create=True)
            store = db.ControlStore(pdir, conn, project_id=pid)
            if store.run(args.run_id) is None:
                raise CliError(EXIT_BLOCKED,
                               f"unknown run {args.run_id} in project {pid}")
            if args.resume_after_clear:
                # I28 intent: recovery after /clear once the token is claimed.
                # The claim row itself cannot carry the flag (fixed S DDL),
                # so it is recorded as a logical audit event.
                with store.mutation():
                    store._event("attach_resume_after_clear_requested",
                                 {"run_id": args.run_id, "resume_after_clear": True},
                                 run_id=args.run_id)
            nonce = _emit_bind_token(
                store, conn, purpose="attach", run_id=args.run_id, project_id=pid,
                extra={"purpose": "attach",
                       "resume_after_clear": bool(args.resume_after_clear)})
        if args.wait_claim is not None:
            return _wait_for_claim(conn, nonce, args.wait_claim,
                                   run_id=args.run_id, project_id=pid)
        return EXIT_OK
    finally:
        if conn is not None:
            conn.close()


def cmd_detach(args: argparse.Namespace) -> int:
    pid, pdir = _require_project()
    with db.RunLock(pdir):
        conn = db.connect(pdir, create=True)
        try:
            store = db.ControlStore(pdir, conn, project_id=pid)
            if store.binding(args.session) is None:
                raise CliError(EXIT_BLOCKED, f"no binding for session {args.session}")
            store.detach_session(args.session)
            _print_json({"detached": True, "session_id": args.session})
            return EXIT_OK
        finally:
            conn.close()


def cmd_binding_status(args: argparse.Namespace) -> int:
    pid, pdir = _require_project()
    conn = db.connect(pdir, create=True)
    try:
        store = db.ControlStore(pdir, conn, project_id=pid)
        if args.session:
            b = store.binding(args.session)
            bindings = [b] if b else []
        else:
            bindings = [dict(r) for r in conn.execute(
                "SELECT * FROM session_bindings WHERE project_id=?"
                " ORDER BY updated_at", (pid,))]
        pending = []
        for c in store.pending_claims():
            c = dict(c)
            c["seconds_to_expiry"] = _seconds_to_expiry(c["expires_at"])
            pending.append(c)
        out: dict[str, Any] = {"project_id": pid, "bindings": bindings,
                               "pending_claims": pending}
        if args.session and not bindings:
            out["note"] = f"no binding for session {args.session}"
        _print_json(out)
        return EXIT_OK
    finally:
        conn.close()


def cmd_verify_run(args: argparse.Namespace) -> int:
    pid, pdir = _require_project()
    conn = db.connect(pdir, create=True)
    try:
        store = db.ControlStore(pdir, conn, project_id=pid)
        rid = args.run_id
        if rid is None:
            runs = store.runs()
            if not runs:
                raise CliError(EXIT_BLOCKED,
                               f"no runs in project {pid} — nothing to verify")
            rid = runs[-1]["run_id"]
        run = store.run(rid)
        if run is None:
            raise CliError(EXIT_BLOCKED, f"unknown run {rid} in project {pid}")
        ok = run["state"] == "CLOSED"
        reason = ("run is CLOSED" if ok
                  else f"run state is {run['state']}, expected CLOSED")
        _print_json({"run_id": rid, "state": run["state"],
                     "objective": run["objective"], "closed_at": run["closed_at"],
                     "verify": "ok" if ok else "blocked", "reason": reason})
        if ok:
            return EXIT_OK
        raise CliError(EXIT_BLOCKED, f"verify-run: {reason}")
    finally:
        conn.close()


def cmd_history(args: argparse.Namespace) -> int:
    hist = _lazy_import("zloop.history")
    if hist is None:
        print(MODULE_UNAVAILABLE)
        return EXIT_OK
    pid, pdir = _require_project()
    if args.cmd == "search":
        results = hist.history_search(pdir, args.query, session=args.session,
                                      run_id=args.run, limit=args.limit)
        _print_json(results)
    else:
        _print_json(hist.history_verify(pdir))
    return EXIT_OK


def cmd_checkpoint(args: argparse.Namespace) -> int:
    cp = _lazy_import("zloop.checkpoint")
    if cp is None:
        print(MODULE_UNAVAILABLE)
        return EXIT_OK
    pid, pdir = _require_project()
    if args.cmd == "write":
        try:
            if args.file is not None:
                capsule = json.loads(Path(args.file).read_text(encoding="utf-8"))
            else:
                capsule = json.loads(sys.stdin.read())
        except (OSError, json.JSONDecodeError) as e:
            raise CliError(EXIT_USAGE, f"capsule is not valid JSON: {e}")
        cp_id = cp.checkpoint_write(pdir, capsule)
        if cp_id is None:
            raise CliError(EXIT_BLOCKED, "checkpoint write failed")
        _print_json({"checkpoint_id": cp_id, "project_id": pid})
        return EXIT_OK
    if args.cmd == "current":
        _print_json({"checkpoint": cp.checkpoint_current(pdir)})
        return EXIT_OK
    capsule = cp.checkpoint_show(pdir, args.checkpoint_id)
    if capsule is None:
        raise CliError(EXIT_BLOCKED, f"unknown checkpoint {args.checkpoint_id}")
    _print_json(capsule)
    return EXIT_OK


def _hook_selfcheck() -> tuple[bool, str]:
    """Verify `import zloop.hook` in a fresh interpreter (zloop.hook is a
    parallel module — never imported in-process here)."""
    env = dict(os.environ)
    pkg_parent = str(Path(__file__).resolve().parents[1])
    parts = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    if pkg_parent not in parts:
        parts.insert(0, pkg_parent)
        env["PYTHONPATH"] = os.pathsep.join(parts)
    try:
        proc = subprocess.run([sys.executable, "-c", "import zloop.hook"],
                              capture_output=True, text=True, env=env, timeout=60)
    except subprocess.TimeoutExpired:
        return False, "selfcheck timed out"
    except OSError as e:
        return False, f"could not run interpreter: {e}"
    if proc.returncode == 0:
        return True, ""
    err = (proc.stderr or "").strip()
    if "ModuleNotFoundError" in err:
        return False, "MODULE_MISSING"
    return False, err or f"exit code {proc.returncode}"


def cmd_install(args: argparse.Namespace) -> int:
    ok, info = _hook_selfcheck()
    if not ok:
        if info == "MODULE_MISSING":
            print(MODULE_UNAVAILABLE)
            return EXIT_OK
        raise CliError(EXIT_BLOCKED, f"zloop.hook import selfcheck failed: {info}")
    zinstall = _lazy_import("zloop.install")
    if zinstall is None:
        print(MODULE_UNAVAILABLE)
        return EXIT_OK
    result = zinstall.install_hooks(sys.executable, ["-m", "zloop.hook"],
                                    timeout_ms=args.timeout_ms,
                                    config_path=args.config_path)
    _print_json(result)
    return EXIT_OK


def cmd_uninstall(args: argparse.Namespace) -> int:
    zinstall = _lazy_import("zloop.install")
    if zinstall is None:
        print(MODULE_UNAVAILABLE)
        return EXIT_OK
    _print_json(zinstall.uninstall_hooks(config_path=args.config_path))
    return EXIT_OK


# ---- parser ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zloop",
        description="ZLoop root-side control surface "
                    "(run start/attach MUST run in foreground to bind)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("doctor", help="environment & S health report")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("project", help="project registry")
    psub = sp.add_subparsers(dest="cmd", required=True)
    sp = psub.add_parser("attach", help="register the project for a git root (default: cwd)")
    sp.add_argument("--git-root", type=Path, default=None, metavar="PATH")
    sp.set_defaults(func=cmd_project)
    sp = psub.add_parser("list", help="list registered projects")
    sp.set_defaults(func=cmd_project)

    sp = sub.add_parser("run", help="runs")
    rsub = sp.add_subparsers(dest="cmd", required=True)
    sp = rsub.add_parser("start", help="create a run and emit a bind token (FOREGROUND)")
    sp.add_argument("objective")
    sp.add_argument("--wait-claim", type=int, default=None, metavar="N",
                    help="poll up to N seconds for the hook to claim the token")
    sp.set_defaults(func=cmd_run_start)
    sp = rsub.add_parser("close", help="close a run")
    sp.add_argument("run_id", metavar="RID")
    sp.set_defaults(func=cmd_run_close)
    sp = rsub.add_parser("status", help="show one run (default: all runs)")
    sp.add_argument("run_id", nargs="?", default=None, metavar="RID")
    sp.set_defaults(func=cmd_run_status)
    sp = rsub.add_parser("list", help="list runs")
    sp.set_defaults(func=cmd_run_list)

    sp = sub.add_parser("attach", help="emit a bind token for an existing run (FOREGROUND)")
    sp.add_argument("run_id", metavar="RID")
    sp.add_argument("--resume-after-clear", action="store_true",
                    help="request recovery injection after /clear (I28) once bound")
    sp.add_argument("--wait-claim", type=int, default=None, metavar="N")
    sp.set_defaults(func=cmd_attach)

    sp = sub.add_parser("detach", help="remove a session binding")
    sp.add_argument("--session", required=True, metavar="ID")
    sp.set_defaults(func=cmd_detach)

    sp = sub.add_parser("binding", help="session bindings & pending claims")
    bsub = sp.add_subparsers(dest="cmd", required=True)
    sp = bsub.add_parser("status", help="bindings + pending unexpired claims")
    sp.add_argument("--session", default=None, metavar="ID")
    sp.set_defaults(func=cmd_binding_status)

    sp = sub.add_parser("history", help="H0 history search/verify")
    hsub = sp.add_subparsers(dest="cmd", required=True)
    sp = hsub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--session", default=None, metavar="ID")
    sp.add_argument("--run", default=None, metavar="RID")
    sp.add_argument("--limit", type=int, default=50, metavar="N")
    sp.set_defaults(func=cmd_history)
    sp = hsub.add_parser("verify")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("checkpoint", help="H1 checkpoints")
    csub = sp.add_subparsers(dest="cmd", required=True)
    sp = csub.add_parser("write", help="write a checkpoint capsule (file or stdin)")
    sp.add_argument("--file", type=Path, default=None, metavar="PATH")
    sp.set_defaults(func=cmd_checkpoint)
    sp = csub.add_parser("current", help="show the current checkpoint")
    sp.set_defaults(func=cmd_checkpoint)
    sp = csub.add_parser("show", help="show one checkpoint")
    sp.add_argument("checkpoint_id", metavar="ID")
    sp.set_defaults(func=cmd_checkpoint)

    sp = sub.add_parser("install", help="install user-level ZCode hooks")
    sp.add_argument("--timeout-ms", type=int, default=8000, metavar="N")
    sp.add_argument("--config-path", type=Path, default=None, metavar="PATH",
                    help="hook config path (default: user-level ZCode config)")
    sp.set_defaults(func=cmd_install)
    sp = sub.add_parser("uninstall", help="remove ZCode hooks")
    sp.add_argument("--config-path", type=Path, default=None, metavar="PATH")
    sp.set_defaults(func=cmd_uninstall)

    sp = sub.add_parser("verify-run", help="goal-completion check: run exists and is CLOSED")
    sp.add_argument("run_id", nargs="?", default=None, metavar="RID")
    sp.set_defaults(func=cmd_verify_run)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    # Exact-spec messages contain non-ASCII (em-dash); never die on a legacy
    # console codepage while printing them.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)  # usage errors exit 2 (argparse)
    try:
        return args.func(args)
    except CliError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return e.code
    except db.SError as e:
        print(f"S_DEGRADED: {e}", file=sys.stderr)
        return EXIT_S_DEGRADED
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # python -m zloop.cli
    sys.exit(main())
