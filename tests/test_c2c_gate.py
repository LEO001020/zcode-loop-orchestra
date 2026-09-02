"""D-25 triple-audit wiring: c2c CLI roundtrip, wave-start plan gate,
role-aware promote gate (VOL-16 §1/§6)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from zloop import paths
from zloop import db as zdb

from tests.test_cli import (run_cli, data_root, git_repo,  # noqa: F401
                            _start_run, _stage_begin, _write_packets, _packet,
                            _final_stage, _staging_workspace, _only_project_id)


def _begin_high_stage(git_repo, data_root, tmp_path, *, risk="HIGH"):
    """run start -> stage begin (HIGH) -> wave propose; NOT started."""
    lines = _start_run(git_repo, data_root, "triple audit probe")
    rid = json.loads(lines[1])["run_id"]
    assert _stage_begin(git_repo, data_root, "triple audit slice",
                        risk=risk).returncode == 0
    f = _write_packets(tmp_path, [_packet("P01", ["src/a/**"], risk_class=risk,
                                          acceptance=['python -c "exit(0)"'])])
    r = run_cli("wave", "propose", str(f), cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    return rid


def _record_c2c(git_repo, data_root, *, role, content, response):
    p = run_cli("c2c", "prepare", "--role", role, cwd=git_repo,
                data_root=data_root, input=content)
    assert p.returncode == 0, p.stderr
    c2c_id = json.loads(p.stdout)["c2c_id"]
    rec = run_cli("c2c", "record", "--c2c", c2c_id,
                  "--identity", "surface=chatgpt_web", cwd=git_repo,
                  data_root=data_root, input=response)
    assert rec.returncode == 0, rec.stderr
    return json.loads(p.stdout), json.loads(rec.stdout)


def test_c2c_cli_roundtrip(git_repo, data_root, tmp_path):
    """zloop c2c prepare --role plan -> bounded packet + c2c_prepared event;
    zloop c2c record -> sha-verified, redacted, identity-filtered result."""
    _begin_high_stage(git_repo, data_root, tmp_path)
    pout, rout = _record_c2c(git_repo, data_root, role="plan",
                             content="objective slice + constraints + unknowns",
                             response="counterplan: add X; blind spot: Y")
    assert pout["role"] == "plan" and pout["c2c_id"].startswith("C2C")
    assert pout["risk_effective"] == "HIGH"
    assert pout["fresh_thread_required"] is True      # D-11 anchoring guard
    assert Path(pout["packet_path"]).is_file()
    pid = _only_project_id()
    conn = zdb.connect(paths.project_dir(pid))
    try:
        kinds = [r["kind"] for r in conn.execute(
            "SELECT kind FROM events ORDER BY seq")]
        assert "c2c_prepared" in kinds and "c2c_recorded" in kinds
    finally:
        conn.close()
    assert rout["audit_coverage"] == "text_packet_only"
    assert rout["trust"] == "external_untrusted"
    assert rout["role"] == "plan"
    assert rout["observed_identity"]["surface"] == "chatgpt_web"
    assert rout["observed_identity"]["ui_model_label"] == "unknown"  # I41b
    assert "counterplan" in rout["response_digest"]


def test_wave_start_plan_gate_blocks_then_unblocks(git_repo, data_root, tmp_path):
    """HIGH risk + no plan-role C2C -> wave start blocked (exit 5), stage
    stays PLANNING; after recording a plan-role C2C -> dispatch proceeds."""
    _begin_high_stage(git_repo, data_root, tmp_path)
    r = run_cli("wave", "start", "W1", "--backend", "mock",
                cwd=git_repo, data_root=data_root)
    assert r.returncode == 5
    assert "c2c_plan_gate_required" in (r.stdout + r.stderr)
    pid = _only_project_id()
    conn = zdb.connect(paths.project_dir(pid))
    try:
        st = conn.execute("SELECT state FROM stages WHERE stage_id='S01'").fetchone()
        assert st["state"] == "PLANNING"            # nothing dispatched
    finally:
        conn.close()
    _record_c2c(git_repo, data_root, role="plan",
                content="plan packet", response="plan verdict: proceed")
    r2 = run_cli("wave", "start", "W1", "--backend", "mock",
                 cwd=git_repo, data_root=data_root)
    assert r2.returncode == 0, r2.stderr
    assert json.loads(r2.stdout)["materialized"] == ["P01"]


def test_promote_requires_result_role_not_plan(git_repo, data_root, tmp_path):
    """A plan-role record satisfies the wave gate but NOT the promote gate;
    only a result-role record unblocks promotion (D-25 role-awareness)."""
    rid = _begin_high_stage(git_repo, data_root, tmp_path)
    r = run_cli("wave", "start", "W1", "--backend", "mock", "--skip-c2c",
                cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    pid = _only_project_id()
    pdir = paths.project_dir(pid)
    staged = _final_stage(_staging_workspace(pdir, "S01"), "src/a/hard.txt",
                          "hard\n", rid, "S01")
    _record_c2c(git_repo, data_root, role="plan",
                content="plan packet", response="plan ok")
    r2 = run_cli("stage", "promote", "S01", cwd=git_repo, data_root=data_root)
    assert r2.returncode == 5
    assert "c2c_gate_required" in (r2.stdout + r2.stderr)
    _record_c2c(git_repo, data_root, role="result",
                content="staged diff review packet", response="verdict: PASS")
    r3 = run_cli("stage", "promote", "S01", cwd=git_repo, data_root=data_root)
    assert r3.returncode == 0, r3.stderr
    assert json.loads(r3.stdout)["promoted"] is True
    assert json.loads(r3.stdout)["new_head"] == staged


def test_wave_start_plan_gate_waiver_audited(git_repo, data_root, tmp_path):
    """--skip-c2c on wave start records a c2c_waiver event (gate=wave_start_plan)."""
    _begin_high_stage(git_repo, data_root, tmp_path)
    r = run_cli("wave", "start", "W1", "--backend", "mock", "--skip-c2c",
                cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    pid = _only_project_id()
    conn = zdb.connect(paths.project_dir(pid))
    try:
        w = conn.execute(
            "SELECT detail_json FROM events WHERE kind='c2c_waiver'").fetchone()
        assert w is not None and "wave_start_plan" in w["detail_json"]
    finally:
        conn.close()
