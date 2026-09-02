"""zloop.materialize — host re-application + host acceptance (VOL-10 §1-§4).

I38: a worker's green run on its OWN snapshot proves nothing — the host
re-applies the worker's final filesystem delta onto the CURRENT staging
snapshot (the stage's ONE private staging branch, D-12; snapshot identity =
commit SHA) and re-runs the packet-required mechanical acceptance on the
resulting candidate BEFORE the packet may become MATERIALIZED. A worker
commit is never cherry-picked/merged as-is: only the reconstructed delta of
its final filesystem is trusted, and every changed path must fall inside the
packet's write_scope (VOL-10 §2 — the delta is re-derived from
``git status --porcelain=v2 -z`` machine output, never from worker
self-reports).

VOL-10 §5 (batching/bisect/oracle cache) is DEFERRED per D-12: v1
materializes packets strictly one at a time.

Failure semantics: acceptance failure LEAVES the candidate commit on the
staging branch (evidence for replan/bisect-later) but performs NO packet
state transition — the packet stays REPORTED.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path, PurePosixPath

from . import ids
from .workspace import enumerate_delta, paths_within_scope

_DEFAULT_TIMEOUT_S = 600

# Git status codes are relative to the repo root; joining a worker-relative
# path onto a worktree root must never escape it (defense in depth on top of
# paths_within_scope, which already rejects absolute/traversal paths).
def _safe_join(root: Path, rel: str) -> Path:
    parts = PurePosixPath(rel.replace("\\", "/")).parts
    if not parts or PurePosixPath(rel).is_absolute() or ".." in parts:
        raise ValueError(f"unsafe delta path: {rel!r}")
    return root.joinpath(*parts)


def _git(args: list[str], cwd: Path,
         timeout: float = 120.0) -> subprocess.CompletedProcess:
    """Run git (real exe) with bytes output (paths may contain any bytes)."""
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          timeout=timeout)


def _err(proc: subprocess.CompletedProcess, limit: int = 300) -> str:
    return proc.stderr.decode("utf-8", errors="replace").strip()[:limit]


# --------------------------------------------------------------- acceptance


def run_host_acceptance(cwd: Path, commands: list[str],
                        timeout_s: int = _DEFAULT_TIMEOUT_S) -> dict:
    """Run each acceptance command in ``cwd`` (shell=True: .cmd shims on
    Windows need it) and capture rc + first 500 chars of stdout/stderr.

    Returns ``{"ok": all rc==0, "results": [...]}``; a timeout or a spawn
    failure counts as a failed command (rc != 0), never as a crash.
    """
    results: list[dict] = []
    for cmd in commands:
        rec: dict = {"command": cmd, "rc": None, "stdout": "", "stderr": "",
                     "timeout": False}
        try:
            proc = subprocess.run(
                cmd, cwd=str(cwd), shell=True, capture_output=True,
                encoding="utf-8", errors="replace", timeout=timeout_s)
            rec["rc"] = proc.returncode
            rec["stdout"] = (proc.stdout or "")[:500]
            rec["stderr"] = (proc.stderr or "")[:500]
        except subprocess.TimeoutExpired as e:
            rec["timeout"] = True
            for key in ("stdout", "stderr"):
                v = getattr(e, key, None)
                if isinstance(v, bytes):
                    v = v.decode("utf-8", errors="replace")
                rec[key] = (v or "")[:500]
        except OSError as e:
            rec["stderr"] = repr(e)[:500]
        results.append(rec)
    return {"ok": all(r["rc"] == 0 for r in results), "results": results}


# ------------------------------------------------------------- application


def _apply_delta(delta: list[dict], workspace: Path, staging_ws: Path) -> None:
    """Apply the worker's final-FS delta onto the staging worktree.

    - record "1" (changed): copied from the worker workspace; a ``D`` in the
      xy status means the file is gone in the worker's final state -> remove.
    - record "2" (rename): remove orig_path, copy path.
    - record "?": copy (untracked = new file).
    - record "u": unmerged worker state -> never applied (caller rejects).
    - record "!": ignored file, not part of the delta.
    """
    for entry in delta:
        kind = entry.get("record_type")
        if kind == "!":
            continue
        if kind == "u":
            raise ValueError(
                f"unmerged paths in worker workspace: {entry.get('path')!r}")
        src = _safe_join(workspace, entry["path"])
        dst = _safe_join(staging_ws, entry["path"])
        if kind == "1" and "D" in (entry.get("xy") or ""):
            dst.unlink(missing_ok=True)
            continue
        if kind == "2":
            _safe_join(staging_ws, entry["orig_path"]).unlink(missing_ok=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)  # content + mode, never worker metadata


def _trailers(run_id: str, stage_id: str, packet_id: str,
              packet_revision: int) -> str:
    return "\n".join([
        f"materialize {packet_id} for {run_id}/{stage_id}",
        "",
        f"ZLoop-Run: {run_id}",
        f"ZLoop-Stage: {stage_id}",
        f"ZLoop-Packet: {packet_id}",
        f"ZLoop-Packet-Revision: {packet_revision}",
    ])


def staging_commit_sha(staging_ws: Path) -> str:
    """Current HEAD of the staging worktree (snapshot identity, D-12)."""
    proc = _git(["rev-parse", "HEAD"], Path(staging_ws))
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse failed in {staging_ws}: {_err(proc)}")
    return proc.stdout.decode("utf-8", errors="replace").strip()


# ------------------------------------------------------------- materialize


def materialize_packet(store, run_id: str, stage_id: str, packet_id: str, *,
                       git_root: Path, staging_ws: Path, workspace: Path,
                       write_scope: list[str],
                       acceptance: list[str]) -> dict:
    """Re-apply a REPORTED packet's delta and re-run acceptance (VOL-10 §1).

    1. packet must be REPORTED (the I6 fence already ran at report time);
    2. delta = enumerate_delta(worker workspace) — worker final FS vs its
       base snapshot — must satisfy ``paths ⊆ write_scope``;
    3. the delta is applied onto ``staging_ws`` (the stage's private staging
       worktree, checked out at the CURRENT stage snapshot);
    4. ``git add -A`` + a host commit (author=zloop, provenance trailers);
    5. host acceptance runs ON that candidate commit — worker green is not
       evidence (I38); failure leaves the commit but transitions nothing;
    6. pass -> one S mutation: packet MATERIALIZED, stage current_snapshot
       updated to the candidate SHA, event carrying the candidate hash.
    """
    staging_ws, workspace = Path(staging_ws), Path(workspace)
    row = store.conn.execute(
        "SELECT * FROM packets WHERE run_id=? AND stage_id=? AND packet_id=?",
        (run_id, stage_id, packet_id)).fetchone()
    if row is None:
        return {"ok": False, "reason": "not_reported",
                "detail": f"unknown packet {run_id}/{stage_id}/{packet_id}"}
    packet = dict(row)
    if packet["state"] != "REPORTED":
        return {"ok": False, "reason": "not_reported",
                "detail": f"packet state is {packet['state']}, not REPORTED"}

    # (2) reconstruct the delta from the worker's FINAL filesystem (VOL-10 §2)
    delta = enumerate_delta(workspace)
    ok_scope, violations = paths_within_scope(delta, write_scope)
    if not ok_scope:
        with store.mutation():
            store._event("materialization_scope_violation",
                         {"packet_id": packet_id, "violations": violations},
                         run_id=run_id, stage_id=stage_id)
        return {"ok": False, "reason": "scope_violation", "violations": violations}

    # (3) apply the delta onto the CURRENT staging snapshot
    try:
        _apply_delta(delta, workspace, staging_ws)
    except (OSError, ValueError) as e:
        return {"ok": False, "reason": "apply_failed", "detail": str(e)[:300]}

    # (4) host materialization commit (VOL-10 §4) on the staging branch
    add = _git(["add", "-A"], staging_ws)
    if add.returncode != 0:
        return {"ok": False, "reason": "apply_failed",
                "detail": f"git add -A failed: {_err(add)}"}
    message = _trailers(run_id, stage_id, packet_id, packet["packet_revision"])
    # rc 1 = staged changes exist; rc 0 = empty delta (retry after a failed
    # acceptance re-applies the same delta) — the snapshot stays where it is
    # and acceptance still runs on it.
    diff = _git(["diff", "--cached", "--quiet"], staging_ws)
    if diff.returncode == 1:
        commit = _git(
            ["-c", "user.name=zloop", "-c", "user.email=zloop@localhost",
             "commit", "-q", "-m", message], staging_ws)
        if commit.returncode != 0:
            return {"ok": False, "reason": "apply_failed",
                    "detail": f"git commit failed: {_err(commit)}"}
    elif diff.returncode != 0:
        return {"ok": False, "reason": "apply_failed",
                "detail": f"git diff --cached failed: {_err(diff)}"}
    # an empty delta leaves the snapshot where it is — acceptance still runs
    sha = staging_commit_sha(staging_ws)

    # (5) host acceptance on the candidate (worker green does not count, I38)
    verdict = run_host_acceptance(staging_ws, acceptance)
    if not verdict["ok"]:
        with store.mutation():
            store._event("materialization_failed",
                         {"packet_id": packet_id, "candidate": sha,
                          "acceptance": verdict["results"]},
                         run_id=run_id, stage_id=stage_id)
        return {"ok": False, "reason": "acceptance_failed",
                "commit": sha, "acceptance": verdict}

    # (6) single S transaction: packet MATERIALIZED + snapshot pointer (VOL-10 §4)
    with store.mutation():
        store.conn.execute(
            "UPDATE packets SET state='MATERIALIZED'"
            " WHERE run_id=? AND stage_id=? AND packet_id=? AND state='REPORTED'",
            (run_id, stage_id, packet_id))
        store.conn.execute(
            "UPDATE stages SET current_snapshot=?, updated_at=?"
            " WHERE run_id=? AND stage_id=?",
            (sha, ids.now_iso(), run_id, stage_id))
        store._event("packet_materialized",
                     {"packet_id": packet_id, "candidate": sha,
                      "run_id": run_id, "stage_id": stage_id},
                     run_id=run_id, stage_id=stage_id)
    return {"ok": True, "commit": sha, "acceptance": verdict}
