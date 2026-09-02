"""zloop.supervisor — the wave supervisor (M6): one cold long process that
owns a wave end-to-end (VOL-03 §5, VOL-09 §5/§7/§8).

``run_wave`` claims the D-8 controller token first — a CAS on
``runs.controller_nonce`` (nonce + pid + pid_start), never an OS lock, and
never a wrestle: a failed claim returns ``controller_busy`` immediately
(I5). It then:

1. validates the proposal (``wave.validate_wave`` against the stage's
   ``risk_effective`` floor; the stage must be EXECUTING and its base
   provably clean [I37]) — errors are returned, nothing is written
   (fail-closed, I4);
2. allocates one fresh launch_id + per-launch workspace directory
   ``workspaces_root/<stage>/<packet>/<launch>/`` [I34] per packet and
   writes the durable PENDING rows (packets / attempts / wave_started in
   one mutation);
3. loops — the supervisor's ONLY loop; it never busy-polls providers
   (a tick that made no progress sleeps ``poll_s``):
   a. observes ``runs.cancel_requested`` — D-8: external cancel is command
      input, not a lifecycle transition — and settles the wave: stage
      CANCELLED (VOL-08 §4), remaining non-terminal packets CANCELLED,
      dangling launches quarantined;
   b. starts every PENDING packet whose dependencies are all MATERIALIZED
      (deps are on private stage snapshot materializations [I8]; a dep in
      a dead state FAILs the dependent to BLOCKED);
   c. collects terminal worker reports through the I6 hard fence
      (``wave.accept_result``): accepted -> REPORTED; rejected -> a
      rejection event only, the packet stays RUNNING (stale results are
      evidence, [I7]); an accepted non-``completed`` status -> FAILED;
   d. materializes every REPORTED packet via ``zloop.materialize``
      (imported lazily — parallel M7 module): ok -> MATERIALIZED (its
      dependents become eligible on the next tick); not ok -> BLOCKED
      with the failure reason;
   e. exits when every packet is terminal ({MATERIALIZED, FAILED, BLOCKED,
      CANCELLED}; SUPERSEDED counts too — a mid-wave stage replan,
      VOL-09 §2, is respected), or with ``reason="stalled"`` when no
      forward work remains (e.g. a fenced-out result leaves a dangling
      RUNNING packet for the next controller epoch to reconcile,
      VOL-03 §5).

The controller token is released on every exit path (``finally``), even
on exceptions.

``pid_start`` caveat (D-8): the value recorded at claim time is the claim
timestamp — a best-effort hint only. Death-proof takeover always requires
mechanically proving the previous owner's process identity from external
evidence BEFORE any CAS; this module never attempts a takeover
(``expected_old`` is never used here).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from . import ids
from .stage import check_stage_base, get_stage, transition_stage
from .wave import accept_result, validate_wave

# A packet the supervisor no longer needs to drive. SUPERSEDED is included
# beyond the VOL-09 terminal four because a mid-wave stage replan
# (wave.supersede_stage_revision) may settle one of our packets while we
# hold the controller token — the supervisor respects it instead of
# hanging on a packet that will never report again.
TERMINAL_PACKET_STATES = frozenset(
    {"MATERIALIZED", "FAILED", "BLOCKED", "CANCELLED", "SUPERSEDED"})

# A dependency in any of these states can never become MATERIALIZED, so a
# dependent packet is BLOCKED instead of waiting forever.
DEAD_DEP_STATES = frozenset({"FAILED", "BLOCKED", "CANCELLED", "SUPERSEDED"})


# ---------------------------------------------------------------- helpers


def _wave_name(wave_no: Any) -> str:
    if isinstance(wave_no, int) and not isinstance(wave_no, bool):
        return ids.fmt_wave(wave_no)
    return str(wave_no)


def _loads(value: Any, default: Any) -> Any:
    try:
        out = json.loads(value) if value else default
    except (TypeError, ValueError):
        return default
    return out if isinstance(out, type(default)) else default


def _json_or_none(value: Any) -> Optional[str]:
    return json.dumps(value, ensure_ascii=False) if value is not None else None


def _stage_packets(store, run_id: str, stage_id: str) -> dict[str, dict]:
    """All packets of the stage keyed by packet_id — dependencies may live
    in earlier waves of the same stage, not only in this wave."""
    return {row["packet_id"]: dict(row) for row in store.conn.execute(
        "SELECT * FROM packets WHERE run_id=? AND stage_id=?",
        (run_id, stage_id))}


def _deps_of(row: dict) -> list[str]:
    return [d for d in _loads(row.get("deps_json"), []) if isinstance(d, str)]


def _result_ready(backend, handle) -> bool:
    """Is a terminal result available for this handle? A backend may expose
    ``poll(handle)``; immediate backends (MockBackend) are always ready —
    for them ``collect`` is the wait."""
    poll = getattr(backend, "poll", None)
    if callable(poll):
        try:
            return bool(poll(handle))
        except Exception:
            return False
    return True


def _summary(wave_name: str, ok: bool, rows: Optional[dict[str, dict]],
             packet_ids: list[str], *, reason: Optional[str] = None,
             errors: Optional[list[str]] = None,
             cancelled: bool = False) -> dict:
    rows = rows or {}
    grouped: dict[str, list[str]] = {}
    for pid in packet_ids:
        grouped.setdefault(rows.get(pid, {}).get("state", "UNKNOWN"),
                           []).append(pid)
    out: dict[str, Any] = {
        "wave": wave_name,
        "ok": ok,
        "cancelled": cancelled,
        "materialized": sorted(grouped.get("MATERIALIZED", [])),
        "blocked": sorted(grouped.get("BLOCKED", [])),
        "failed": sorted(grouped.get("FAILED", [])),
        "cancelled_packets": sorted(grouped.get("CANCELLED", [])),
        "running": sorted(grouped.get("RUNNING", [])),
        "pending": sorted(grouped.get("PENDING", [])),
    }
    if reason is not None:
        out["reason"] = reason
    if errors is not None:
        out["errors"] = list(errors)
    return out


# ------------------------------------------------------------- public API


def run_wave(store, run_id: str, stage_id: str, wave_no, packets: list[dict],
             backend, *, git_root: Path, staging_ws: Path,
             workspaces_root: Path, poll_s: float = 0.2) -> dict:
    """Supervise one wave to completion (VOL-09 §5/§7/§8, D-8).

    Claims the run's controller token first: the CAS claim fails with
    ``{"ok": False, "reason": "controller_busy"}`` when another controller
    owns the run — the supervisor never wrestles (I5). The ``pid_start``
    recorded with the claim is the claim timestamp, a best-effort hint:
    death-proof takeover requires external mechanical proof of the old
    owner's process identity (D-8); this module never takes over.

    ``git_root`` / ``staging_ws`` / ``workspaces_root`` locate the canonical
    repo, the private staging worktree that materialization commits into
    (VOL-10), and the per-launch workspace root
    (``<stage>/<packet>/<launch>/`` [I34]). ``poll_s`` is the sleep between
    no-progress ticks — the loop never busy-polls providers.

    Returns ``{"wave", "ok", "cancelled", "materialized", "blocked",
    "failed", "cancelled_packets", "running", "pending"}`` plus ``reason``
    on non-ok exits ("unknown_run" | "controller_busy" | "unknown_stage" |
    "invalid_wave" (+ ``errors``) | "cancelled" | "stalled"). The
    controller token is released on every exit path, including exceptions.
    """
    wave_name = _wave_name(wave_no)
    if store.run(run_id) is None:
        return _summary(wave_name, False, None, [], reason="unknown_run")
    nonce = ids.new_nonce()
    # D-8: S-internal CAS claim; pid_start is a claim-time hint only (see
    # module docstring — real takeover proof is external process identity).
    if not store.claim_controller(run_id, nonce=nonce, pid=os.getpid(),
                                  pid_start=ids.now_iso()):
        return _summary(wave_name, False, None, [],
                        reason="controller_busy")
    try:
        return _supervise(store, run_id, stage_id, wave_name, packets,
                          backend, Path(git_root), Path(staging_ws),
                          Path(workspaces_root), float(poll_s))
    finally:
        store.release_controller(run_id, nonce)


# ---------------------------------------------------------- wave internals


def _supervise(store, run_id: str, stage_id: str, wave_name: str,
               packets: list[dict], backend, git_root: Path,
               staging_ws: Path, workspaces_root: Path,
               poll_s: float) -> dict:
    # ---- validation (fail-closed: on errors nothing is written, I4) -----
    stage = get_stage(store, run_id, stage_id)
    if stage is None:
        return _summary(wave_name, False, {}, [],
                        reason="unknown_stage",
                        errors=[f"unknown stage {run_id}/{stage_id}"])
    errors: list[str] = []
    if stage["state"] != "EXECUTING":
        errors.append(
            f"stage {stage_id} is {stage['state']}, not EXECUTING (VOL-09 §1)")
    clean, why = check_stage_base(stage["canonical_dirty_digest"])
    if not clean:
        errors.append(
            f"wave blocked for {stage_id}: {why} (dirty canonical base, I37)")
    existing = {
        row["packet_id"]: {"packet_id": row["packet_id"],
                           "depends_on": _deps_of(row),
                           "write_scope": _loads(row["write_scope_json"], [])}
        for row in _stage_packets(store, run_id, stage_id).values()}
    verdict = validate_wave(packets, existing,
                            stage_floor=stage["risk_effective"])
    errors.extend(verdict["errors"])
    if errors:
        return _summary(wave_name, False, {}, [], reason="invalid_wave",
                        errors=errors)

    # ---- setup: durable PENDING rows + per-launch workspaces [I34] -------
    stage_revision = stage["stage_revision"]
    infos: list[dict] = []
    now = ids.now_iso()
    with store.mutation():
        for p in packets:
            pid = p["packet_id"]
            launch_id = ids.new_launch_id()
            workspace = workspaces_root / stage_id / pid / launch_id
            workspace.mkdir(parents=True, exist_ok=True)
            store.conn.execute(
                "INSERT INTO packets(run_id, stage_id, stage_revision,"
                " packet_id, packet_revision, goal, write_scope_json,"
                " acceptance_json, constraints_json, deps_json,"
                " resource_scope_json, evidence_refs_json, risk_class,"
                " network_policy, max_turns, state, active_launch_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, stage_id, stage_revision, pid, 1,
                 p.get("goal") or "",
                 json.dumps(p.get("write_scope") or [], ensure_ascii=False),
                 json.dumps(p.get("acceptance") or [], ensure_ascii=False),
                 _json_or_none(p.get("constraints")),
                 json.dumps(p.get("depends_on") or [], ensure_ascii=False),
                 _json_or_none(p.get("resource_scope")),
                 _json_or_none(p.get("evidence_refs")),
                 p.get("risk_class") or "NORMAL",
                 p.get("network_policy") or "none",
                 p.get("max_turns"),
                 "PENDING", None))
            store._event("packet_created",
                         {"packet_id": pid, "state": "PENDING"},
                         run_id=run_id, stage_id=stage_id)
            store.conn.execute(
                "INSERT INTO attempts(run_id, stage_id, packet_id,"
                " packet_revision, attempt, created_at, note)"
                " VALUES (?,?,?,?,?,?,?)",
                (run_id, stage_id, pid, 1, 1, now,
                 f"wave {wave_name} attempt 1"))
            store._event("attempt_created",
                         {"packet_id": pid, "attempt": 1},
                         run_id=run_id, stage_id=stage_id)
            infos.append({"packet": p, "packet_id": pid,
                          "launch_id": launch_id, "workspace": workspace,
                          "handle": None, "collected": False})
        store._event("wave_started",
                     {"wave": wave_name, "stage_revision": stage_revision,
                      "packets": [i["packet_id"] for i in infos]},
                     run_id=run_id, stage_id=stage_id)

    packet_ids = [i["packet_id"] for i in infos]

    # ---- the supervisor's only loop --------------------------------------
    while True:
        # (a) external cancel is command input observed every tick (D-8)
        ctrl = store.controller(run_id)
        if ctrl is not None and ctrl.get("cancel_requested"):
            return _cancel_wave(store, run_id, stage_id, wave_name, infos,
                                backend)

        progress = False
        rows = _stage_packets(store, run_id, stage_id)

        # (b) start PENDING packets whose deps are all MATERIALIZED [I8]
        for info in infos:
            row = rows.get(info["packet_id"])
            if row is None or row["state"] != "PENDING":
                continue
            dep_states = [(dep, rows.get(dep, {}).get("state"))
                          for dep in _deps_of(row)]
            if all(state == "MATERIALIZED" for _, state in dep_states):
                _start_packet(store, run_id, stage_id, stage_revision,
                              info, backend)
                progress = True
                continue
            dead = sorted({dep for dep, state in dep_states
                           if state in DEAD_DEP_STATES})
            if dead:
                dead_state = next(state for _, state in dep_states
                                  if state in DEAD_DEP_STATES)
                with store.mutation():
                    store.conn.execute(
                        "UPDATE packets SET state='BLOCKED'"
                        " WHERE run_id=? AND stage_id=? AND packet_id=?"
                        " AND state='PENDING'",
                        (run_id, stage_id, info["packet_id"]))
                    store._event(
                        "packet_blocked",
                        {"packet_id": info["packet_id"],
                         "reason": f"dependency_{dead_state.lower()}",
                         "dependencies": dead},
                        run_id=run_id, stage_id=stage_id)
                progress = True

        # (c) collect terminal reports through the I6 fence
        rows = _stage_packets(store, run_id, stage_id)
        for info in infos:
            if info["handle"] is None or info["collected"]:
                continue
            row = rows.get(info["packet_id"])
            if row is None or row["state"] != "RUNNING":
                continue
            if not _result_ready(backend, info["handle"]):
                continue
            report = backend.collect(info["handle"])
            info["collected"] = True  # the terminal result is consumed
            if _collect_report(store, run_id, stage_id, info, row, report):
                progress = True

        # (d) materialize every REPORTED packet (lazy M7 import)
        rows = _stage_packets(store, run_id, stage_id)
        for info in infos:
            if rows.get(info["packet_id"], {}).get("state") != "REPORTED":
                continue
            _materialize_packet(store, run_id, stage_id, info,
                                git_root, staging_ws)
            progress = True

        # (e) exit when every packet is terminal
        rows = _stage_packets(store, run_id, stage_id)
        if all(rows.get(pid, {}).get("state") in TERMINAL_PACKET_STATES
               for pid in packet_ids):
            summary = _summary(wave_name, True, rows, packet_ids)
            with store.mutation():
                store._event(
                    "wave_completed",
                    {"wave": wave_name,
                     "materialized": summary["materialized"],
                     "blocked": summary["blocked"],
                     "failed": summary["failed"]},
                    run_id=run_id, stage_id=stage_id)
            return summary
        if not progress:
            # await, don't busy-poll: sleep only while live work pends
            if any(i["handle"] is not None and not i["collected"]
                   for i in infos):
                time.sleep(poll_s)
                continue
            summary = _summary(wave_name, False, rows, packet_ids,
                               reason="stalled")
            with store.mutation():
                store._event(
                    "wave_stalled",
                    {"wave": wave_name, "running": summary["running"],
                     "pending": summary["pending"]},
                    run_id=run_id, stage_id=stage_id)
            return summary


def _start_packet(store, run_id: str, stage_id: str, stage_revision: int,
                  info: dict, backend) -> None:
    """Durable launch intent -> spawn -> BOUND -> RUNNING (VOL-09 §5)."""
    pid = info["packet_id"]
    launch_id = info["launch_id"]
    p = info["packet"]
    workspace_id = f"{stage_id}/{pid}/{launch_id}"
    spec = {
        # identity — fenced again at collect time (VOL-09 §2, I6)
        "run_id": run_id, "stage_id": stage_id,
        "stage_revision": stage_revision,
        "packet_id": pid, "packet_revision": 1, "attempt": 1,
        "launch_id": launch_id,
        # WorkerSpec-like launch envelope (VOL-09 §4)
        "workspace": str(info["workspace"]),
        "prompt": p.get("goal") or "",
        "network": p.get("network_policy") or "none",
        "max_turns": p.get("max_turns"),
        "model": os.environ.get("ZLOOP_MODEL"),
        # full packet payload for backends that want it
        "goal": p.get("goal") or "",
        "write_scope": p.get("write_scope") or [],
        "acceptance": p.get("acceptance") or [],
        "constraints": p.get("constraints") or [],
        "network_policy": p.get("network_policy") or "none",
    }
    # (1) durable intent BEFORE any spawn (VOL-09 §5)
    with store.mutation():
        store.conn.execute(
            "INSERT INTO launches(launch_id, run_id, stage_id, stage_revision,"
            " packet_id, packet_revision, attempt, workspace_id, backend,"
            " intent_state, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (launch_id, run_id, stage_id, stage_revision, pid, 1, 1,
             workspace_id, getattr(backend, "name", type(backend).__name__),
             "INTENDED", ids.now_iso()))
        store._event("launch_intended",
                     {"packet_id": pid, "launch_id": launch_id,
                      "workspace_id": workspace_id},
                     run_id=run_id, stage_id=stage_id)
    # (2) spawn the worker
    started = backend.start(spec)
    handle = started.get("handle") if isinstance(started, dict) else None
    info["handle"] = handle
    # (3) BOUND -> RUNNING + packet RUNNING
    with store.mutation():
        store.conn.execute(
            "UPDATE launches SET intent_state='BOUND', backend_handle=?"
            " WHERE launch_id=?", (handle, launch_id))
        store._event("launch_bound",
                     {"packet_id": pid, "launch_id": launch_id,
                      "backend_handle": handle},
                     run_id=run_id, stage_id=stage_id)
        store.conn.execute(
            "UPDATE launches SET intent_state='RUNNING' WHERE launch_id=?",
            (launch_id,))
        store._event("launch_running",
                     {"packet_id": pid, "launch_id": launch_id},
                     run_id=run_id, stage_id=stage_id)
        cur = store.conn.execute(
            "UPDATE packets SET state='RUNNING', active_launch_id=?"
            " WHERE run_id=? AND stage_id=? AND packet_id=? AND state='PENDING'",
            (launch_id, run_id, stage_id, pid))
        if cur.rowcount == 1:
            store._event("packet_running",
                         {"packet_id": pid, "launch_id": launch_id,
                          "state": "RUNNING"},
                         run_id=run_id, stage_id=stage_id)


def _collect_report(store, run_id: str, stage_id: str, info: dict,
                    row: dict, report: dict) -> bool:
    """Apply one terminal report through the I6 hard fence. Returns True
    when the packet state moved (progress)."""
    pid = info["packet_id"]
    launch_id = info["launch_id"]
    ok, reason = accept_result(row, report)
    if not ok:
        # fenced out: evidence only, the packet stays RUNNING [I7]
        with store.mutation():
            store._event("result_rejected",
                         {"packet_id": pid,
                          "launch_id": report.get("launch_id"),
                          "reason": reason},
                         run_id=run_id, stage_id=stage_id)
        return False
    status = report.get("status")
    with store.mutation():
        store.conn.execute(
            "UPDATE launches SET intent_state='TERMINAL', terminal_state=?,"
            " terminal_at=? WHERE launch_id=?",
            (str(status), ids.now_iso(), launch_id))
        if status == "completed":
            store.conn.execute(
                "UPDATE packets SET state='REPORTED'"
                " WHERE run_id=? AND stage_id=? AND packet_id=?"
                " AND state='RUNNING' AND active_launch_id=?",
                (run_id, stage_id, pid, launch_id))
            store._event("packet_reported",
                         {"packet_id": pid, "launch_id": launch_id,
                          "status": status},
                         run_id=run_id, stage_id=stage_id)
        else:
            # terminal worker failure (VOL-09 §5)
            store.conn.execute(
                "UPDATE packets SET state='FAILED', active_launch_id=NULL"
                " WHERE run_id=? AND stage_id=? AND packet_id=?"
                " AND state='RUNNING' AND active_launch_id=?",
                (run_id, stage_id, pid, launch_id))
            store._event("packet_failed",
                         {"packet_id": pid, "launch_id": launch_id,
                          "status": status},
                         run_id=run_id, stage_id=stage_id)
    return True


def _materialize_packet(store, run_id: str, stage_id: str, info: dict,
                        git_root: Path, staging_ws: Path) -> None:
    """REPORTED -> MATERIALIZED (ok) or BLOCKED (not ok), via the M7
    materialization module (imported lazily — parallel component).

    ``materialize_packet`` performs the S-side MATERIALIZED transition +
    ``packet_materialized`` event itself when it succeeds (and performs NO
    transition on failure — VOL-10 §1 leaves the candidate as evidence).
    The supervisor therefore only settles the packet itself when the module
    left it REPORTED (interface tolerance), and owns the not-ok policy:
    scope_violation / acceptance_failed / apply_failed -> BLOCKED.
    """
    from . import materialize  # lazy: zloop.materialize (M7, VOL-10)

    pid = info["packet_id"]
    p = info["packet"]
    result = materialize.materialize_packet(
        store, run_id, stage_id, pid,
        git_root=git_root, staging_ws=staging_ws,
        workspace=info["workspace"],
        write_scope=p.get("write_scope") or [],
        acceptance=p.get("acceptance") or [])
    result = result if isinstance(result, dict) else {}
    if result.get("ok"):
        row = store.conn.execute(
            "SELECT state FROM packets WHERE run_id=? AND stage_id=?"
            " AND packet_id=?", (run_id, stage_id, pid)).fetchone()
        if row is not None and row["state"] == "REPORTED":
            # the module left the transition to the supervisor — do it
            with store.mutation():
                store.conn.execute(
                    "UPDATE packets SET state='MATERIALIZED'"
                    " WHERE run_id=? AND stage_id=? AND packet_id=?"
                    " AND state='REPORTED'",
                    (run_id, stage_id, pid))
                store._event("packet_materialized",
                             {"packet_id": pid,
                              **{k: result[k] for k in ("commit", "sha")
                                 if k in result}},
                             run_id=run_id, stage_id=stage_id)
        return
    # not ok: the wave owner settles the packet (BLOCKED) with the reason
    detail = {"packet_id": pid,
              "reason": result.get("reason") or "materialize_failed",
              **{k: result[k] for k in ("commit", "violations")
                 if k in result}}
    with store.mutation():
        store.conn.execute(
            "UPDATE packets SET state='BLOCKED'"
            " WHERE run_id=? AND stage_id=? AND packet_id=?"
            " AND state='REPORTED'",
            (run_id, stage_id, pid))
        store._event("packet_blocked", detail,
                     run_id=run_id, stage_id=stage_id)


def _cancel_wave(store, run_id: str, stage_id: str, wave_name: str,
                 infos: list[dict], backend) -> dict:
    """Settle the wave after observing ``runs.cancel_requested`` (VOL-09 §8):
    interrupt live launches best-effort, stage -> CANCELLED (VOL-08 §4),
    every remaining non-terminal packet -> CANCELLED, launches quarantined."""
    rows = _stage_packets(store, run_id, stage_id)
    packet_ids = [i["packet_id"] for i in infos]
    interrupt = getattr(backend, "interrupt", None)
    if callable(interrupt):
        for info in infos:
            if info["handle"] is not None and not info["collected"]:
                try:
                    interrupt(info["handle"])
                except Exception:
                    pass  # best-effort; quarantine below is the record
    try:
        transition_stage(store, run_id, stage_id, "CANCELLED")
    except ValueError:
        pass  # already settled by another path; packets still settle below
    cancelled_ids: list[str] = []
    with store.mutation():
        for info in infos:
            row = rows.get(info["packet_id"])
            if row is None or row["state"] in TERMINAL_PACKET_STATES:
                continue
            store.conn.execute(
                "UPDATE packets SET state='CANCELLED', active_launch_id=NULL"
                " WHERE run_id=? AND stage_id=? AND packet_id=?",
                (run_id, stage_id, info["packet_id"]))
            store._event("packet_cancelled",
                         {"packet_id": info["packet_id"],
                          "from_state": row["state"]},
                         run_id=run_id, stage_id=stage_id)
            store.conn.execute(
                "UPDATE launches SET intent_state='QUARANTINED',"
                " terminal_state='CANCELLED', terminal_at=?"
                " WHERE run_id=? AND stage_id=? AND packet_id=?"
                " AND intent_state IN ('INTENDED','BOUND','RUNNING')",
                (ids.now_iso(), run_id, stage_id, info["packet_id"]))
            cancelled_ids.append(info["packet_id"])
    rows = _stage_packets(store, run_id, stage_id)
    summary = _summary(wave_name, False, rows, packet_ids,
                       reason="cancelled", cancelled=True)
    with store.mutation():
        store._event("wave_cancelled",
                     {"wave": wave_name,
                      "cancelled_packets": sorted(cancelled_ids)},
                     run_id=run_id, stage_id=stage_id)
    return summary
