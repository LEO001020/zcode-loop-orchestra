"""CLI tests (VOL-18 layer B): subprocess-driven against `python -m zloop.cli`.

Exit-code contract under test: 0 ok · 2 usage · 3 S_DEGRADED ·
4 bind-wait timeout (P2-13) · 5 blocked. Parallel modules
(zloop.history / zloop.checkpoint / zloop.install / zloop.hook) may be
absent while other agents write them — those assertions degrade via
importorskip / _have_module.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

from zloop import __version__          # noqa: E402
from zloop import db as zdb            # noqa: E402
from zloop import ids, paths           # noqa: E402


def _cli_env(data_root: Path) -> dict:
    env = dict(os.environ)
    pp = str(SRC)
    old = env.get("PYTHONPATH", "")
    if pp not in old.split(os.pathsep):
        env["PYTHONPATH"] = pp + ((os.pathsep + old) if old else "")
    env["ZLOOP_DATA"] = str(data_root)
    return env


def run_cli(*args, cwd: Path, data_root: Path, input=None, timeout=120):
    return subprocess.run(
        [sys.executable, "-m", "zloop.cli", *map(str, args)],
        input=input, capture_output=True, text=True, cwd=str(cwd),
        env=_cli_env(data_root), timeout=timeout,
        stdin=subprocess.DEVNULL if input is None else None)


def _have_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    d = tmp_path / "zloop-data"
    d.mkdir()
    monkeypatch.setenv("ZLOOP_DATA", str(d))  # in-process foundation calls agree
    return d


@pytest.fixture()
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def g(*a):
        return subprocess.run(["git", *a], cwd=str(repo), capture_output=True,
                              text=True, timeout=60)

    assert g("init").returncode == 0
    g("config", "user.email", "cli-test@example.com")
    g("config", "user.name", "cli-test")
    g("config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# cli test repo\n", encoding="utf-8")
    g("add", "-A")
    r = g("commit", "-m", "init")
    assert r.returncode == 0, r.stderr
    return repo


def _only_project_id() -> str:
    projs = paths.load_registry()["projects"]
    assert len(projs) == 1
    return next(iter(projs))


def _start_run(repo: Path, data_root: Path, objective="test objective"):
    r = run_cli("run", "start", objective, cwd=repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert lines[0].startswith("ZLOOP_BIND_TOKEN=")
    return lines


def _corrupt_control_db(pid: str) -> None:
    dbp = paths.control_db_path(pid)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(dbp) + suffix)
        if p.exists():
            p.unlink()
    dbp.write_bytes(b"this is definitely not a sqlite database at all")


# ---- basics ---------------------------------------------------------------


def test_version(tmp_path, data_root):
    r = run_cli("--version", cwd=tmp_path, data_root=data_root)
    assert r.returncode == 0
    assert __version__ in r.stdout


def test_usage_errors_exit_2(git_repo, data_root):
    r = run_cli("run", cwd=git_repo, data_root=data_root)  # missing subcommand
    assert r.returncode == 2
    r2 = run_cli("definitely-not-a-command", cwd=git_repo, data_root=data_root)
    assert r2.returncode == 2


def test_commands_require_registered_project(tmp_path, data_root):
    for args in (["run", "list"], ["binding", "status"], ["verify-run"],
                 ["run", "close", "R001"]):
        r = run_cli(*args, cwd=tmp_path, data_root=data_root)
        assert r.returncode == 5, (args, r.stderr)
    r = run_cli("history", "search", "x", cwd=tmp_path, data_root=data_root)
    if _have_module("zloop.history"):
        assert r.returncode == 5
    else:
        assert r.returncode == 0


def test_project_attach_list_idempotent(git_repo, data_root):
    a = run_cli("project", "attach", cwd=git_repo, data_root=data_root)
    assert a.returncode == 0, a.stderr
    rec = json.loads(a.stdout)
    assert rec["created"] is True
    assert rec["project_id"]
    a2 = run_cli("project", "attach", cwd=git_repo, data_root=data_root)
    assert json.loads(a2.stdout)["created"] is False
    l = run_cli("project", "list", cwd=git_repo, data_root=data_root)
    assert l.returncode == 0
    projs = json.loads(l.stdout)
    assert len(projs) == 1
    assert projs[0]["project_id"] == rec["project_id"]
    assert projs[0]["git_root"].lower() == str(git_repo.resolve()).lower()


# ---- run start: bind-token marker (VOL-05 §4) -----------------------------


def test_run_start_marker_first_and_pending_claim(git_repo, data_root):
    lines = _start_run(git_repo, data_root, "do the thing")
    nonce = lines[0].split("=", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{64}", nonce)
    obj = json.loads(lines[1])
    assert set(obj) >= {"run_id", "project_id", "claim_expires_at", "note"}
    assert obj["run_id"] == "R001"
    assert "FOREGROUND" in obj["note"]

    pid = _only_project_id()
    assert obj["project_id"] == pid
    pdir = paths.project_dir(pid)
    conn = zdb.connect(pdir)
    try:
        store = zdb.ControlStore(pdir, conn, project_id=pid)
        run = store.run(obj["run_id"])
        assert run is not None and run["state"] == "ACTIVE"
        assert run["objective"] == "do the thing"
        row = conn.execute(
            "SELECT * FROM pending_binding_claims WHERE nonce=?", (nonce,)).fetchone()
        assert row is not None
        assert row["purpose"] == "run_start"
        assert row["run_id"] == obj["run_id"]
        assert row["claimed_at"] is None
        exp = ids.parse_iso(row["expires_at"])
        assert exp is not None and exp > datetime.now(timezone.utc)  # TTL 120s
    finally:
        conn.close()


# ---- run close / status / verify-run ---------------------------------------


def test_run_close_status_verify_run(git_repo, data_root):
    lines = _start_run(git_repo, data_root, "objective A")
    rid = json.loads(lines[1])["run_id"]

    v = run_cli("verify-run", rid, cwd=git_repo, data_root=data_root)
    assert v.returncode == 5
    assert "ACTIVE" in v.stderr

    c = run_cli("run", "close", rid, cwd=git_repo, data_root=data_root)
    assert c.returncode == 0, c.stderr
    closed = json.loads(c.stdout)
    assert closed["state"] == "CLOSED" and closed["closed_at"]

    s = run_cli("run", "status", rid, cwd=git_repo, data_root=data_root)
    assert s.returncode == 0
    assert json.loads(s.stdout)["state"] == "CLOSED"

    v2 = run_cli("verify-run", rid, cwd=git_repo, data_root=data_root)
    assert v2.returncode == 0
    assert json.loads(v2.stdout)["verify"] == "ok"

    v3 = run_cli("verify-run", cwd=git_repo, data_root=data_root)  # latest run
    assert v3.returncode == 0

    v4 = run_cli("verify-run", "R999", cwd=git_repo, data_root=data_root)
    assert v4.returncode == 5

    lst = run_cli("run", "list", cwd=git_repo, data_root=data_root)
    assert lst.returncode == 0
    assert [r["run_id"] for r in json.loads(lst.stdout)] == [rid]


# ---- attach + binding status (I32 mechanical confirmation) ------------------


def test_attach_marker_and_binding_status(git_repo, data_root):
    lines = _start_run(git_repo, data_root)
    rid = json.loads(lines[1])["run_id"]

    a = run_cli("attach", rid, cwd=git_repo, data_root=data_root)
    assert a.returncode == 0, a.stderr
    alines = a.stdout.splitlines()
    assert alines[0].startswith("ZLOOP_BIND_TOKEN=")
    assert re.fullmatch(r"[0-9a-f]{64}", alines[0].split("=", 1)[1])
    aobj = json.loads(alines[1])
    assert aobj["run_id"] == rid

    # --resume-after-clear is carried on the attach output (I28 intent)
    a3 = run_cli("attach", rid, "--resume-after-clear",
                 cwd=git_repo, data_root=data_root)
    assert a3.returncode == 0
    assert json.loads(a3.stdout.splitlines()[1])["resume_after_clear"] is True

    b = run_cli("binding", "status", cwd=git_repo, data_root=data_root)
    assert b.returncode == 0, b.stderr
    bs = json.loads(b.stdout)
    claims = bs["pending_claims"]
    attach_claims = [c for c in claims if c["purpose"] == "attach"]
    assert attach_claims, claims
    assert attach_claims[0]["run_id"] == rid
    assert attach_claims[0]["seconds_to_expiry"] > 0
    assert any(c["purpose"] == "run_start" for c in claims)  # from run start

    # unknown run -> blocked
    a2 = run_cli("attach", "R999", cwd=git_repo, data_root=data_root)
    assert a2.returncode == 5


def test_detach_and_binding_status_session(git_repo, data_root):
    lines = _start_run(git_repo, data_root)
    rid = json.loads(lines[1])["run_id"]
    pid = _only_project_id()
    pdir = paths.project_dir(pid)
    # simulate a hook claim (the PostToolUse side of the protocol)
    conn = zdb.connect(pdir)
    try:
        store = zdb.ControlStore(pdir, conn, project_id=pid)
        nonce = store.create_claim(purpose="run_start", run_id=rid)
        assert store.claim_binding(nonce, "sess-cli-test") is not None
    finally:
        conn.close()

    b = run_cli("binding", "status", cwd=git_repo, data_root=data_root)
    assert b.returncode == 0
    bs = json.loads(b.stdout)
    assert any(x["zcode_session_id"] == "sess-cli-test" for x in bs["bindings"])

    b2 = run_cli("binding", "status", "--session", "sess-cli-test",
                 cwd=git_repo, data_root=data_root)
    assert b2.returncode == 0
    bs2 = json.loads(b2.stdout)
    assert bs2["bindings"] and bs2["bindings"][0]["zcode_session_id"] == "sess-cli-test"

    d = run_cli("detach", "--session", "sess-cli-test",
                cwd=git_repo, data_root=data_root)
    assert d.returncode == 0, d.stderr

    b3 = run_cli("binding", "status", cwd=git_repo, data_root=data_root)
    assert all(x["zcode_session_id"] != "sess-cli-test"
               for x in json.loads(b3.stdout)["bindings"])

    d2 = run_cli("detach", "--session", "sess-cli-test",
                 cwd=git_repo, data_root=data_root)
    assert d2.returncode == 5  # nothing left to detach


# ---- doctor ---------------------------------------------------------------


def test_doctor(git_repo, data_root):
    _start_run(git_repo, data_root)
    d = run_cli("doctor", cwd=git_repo, data_root=data_root)
    assert d.returncode == 0, d.stderr
    assert "data root" in d.stdout
    assert "journal profile" in d.stdout
    assert "1 project" in d.stdout
    assert "quick_check=ok" in d.stdout

    # a degraded project is reported, but the diagnostic still completes
    _corrupt_control_db(_only_project_id())
    d2 = run_cli("doctor", cwd=git_repo, data_root=data_root)
    assert d2.returncode == 0
    assert "DEGRADED" in d2.stdout


# ---- S degraded -> exit 3 (I4 fail-closed) ---------------------------------


def test_serror_run_list_exit_3(git_repo, data_root):
    _start_run(git_repo, data_root)
    _corrupt_control_db(_only_project_id())
    r = run_cli("run", "list", cwd=git_repo, data_root=data_root)
    assert r.returncode == 3
    assert "S_DEGRADED" in r.stderr


# ---- --wait-claim (P2-13 foreground constraint) -----------------------------


def test_wait_claim_timeout_exit_4(git_repo, data_root):
    r = run_cli("run", "start", "obj", "--wait-claim", "1",
                cwd=git_repo, data_root=data_root)
    assert r.returncode == 4
    assert "token not claimed" in r.stderr
    assert "foreground" in r.stderr.lower()
    # marker + JSON still on stdout in exact order
    lines = r.stdout.splitlines()
    assert lines[0].startswith("ZLOOP_BIND_TOKEN=")
    json.loads(lines[1])


def test_wait_claim_success(git_repo, data_root):
    a = run_cli("project", "attach", cwd=git_repo, data_root=data_root)
    assert a.returncode == 0
    pid = json.loads(a.stdout)["project_id"]
    pdir = paths.project_dir(pid)
    proc = subprocess.Popen(
        [sys.executable, "-m", "zloop.cli", "run", "start", "wait obj",
         "--wait-claim", "15"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=str(git_repo), env=_cli_env(data_root))
    try:
        nonce = None
        deadline = time.time() + 15
        while time.time() < deadline:
            dbp = pdir / "control.sqlite3"
            if dbp.exists():
                try:
                    conn = sqlite3.connect(str(dbp))
                    row = conn.execute(
                        "SELECT nonce FROM pending_binding_claims"
                        " WHERE claimed_at IS NULL").fetchone()
                    conn.close()
                    if row:
                        nonce = row[0]
                        break
                except sqlite3.Error:
                    pass
            time.sleep(0.1)
        assert nonce, "pending claim never appeared in S"
        conn = zdb.connect(pdir)
        try:
            store = zdb.ControlStore(pdir, conn, project_id=pid)
            assert store.claim_binding(nonce, "sess-hook-sim") is not None
        finally:
            conn.close()
    finally:
        out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err
    lines = out.splitlines()
    assert lines[0].startswith("ZLOOP_BIND_TOKEN=")
    bound = json.loads(lines[2])
    assert bound["bound"] is True
    assert bound["claimed_by_session"] == "sess-hook-sim"


# ---- lazy parallel modules -------------------------------------------------


def test_history_command_lazy_tolerance(git_repo, data_root):
    a = run_cli("project", "attach", cwd=git_repo, data_root=data_root)
    assert a.returncode == 0
    r = run_cli("history", "search", "anything", cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    if _have_module("zloop.history"):
        assert isinstance(json.loads(r.stdout), list)
    else:
        assert "module not available (parallel integration pending)" in r.stdout
    rv = run_cli("history", "verify", cwd=git_repo, data_root=data_root)
    assert rv.returncode == 0
    if not _have_module("zloop.history"):
        assert "module not available" in rv.stdout


def test_checkpoint_command_lazy_tolerance(git_repo, data_root):
    a = run_cli("project", "attach", cwd=git_repo, data_root=data_root)
    assert a.returncode == 0
    r = run_cli("checkpoint", "current", cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    if not _have_module("zloop.checkpoint"):
        assert "module not available (parallel integration pending)" in r.stdout
    else:
        assert json.loads(r.stdout)["checkpoint"] is None  # fresh project


def test_history_search_real_module(git_repo, data_root):
    pytest.importorskip("zloop.history")
    a = run_cli("project", "attach", cwd=git_repo, data_root=data_root)
    assert a.returncode == 0
    pid = _only_project_id()
    from zloop import evidence as zev
    j = zev.Journal(paths.history_session_file(pid, "sess-h1"),
                    paths.blobs_root(pid))
    assert j.append(kind="prompt", session_id="sess-h1",
                    payload={"q": "find the needle prompt"}) is not None
    r = run_cli("history", "search", "needle", cwd=git_repo, data_root=data_root)
    assert r.returncode == 0, r.stderr
    results = json.loads(r.stdout)
    assert isinstance(results, list) and len(results) >= 1
    r2 = run_cli("history", "verify", cwd=git_repo, data_root=data_root)
    assert r2.returncode == 0, r2.stderr
    assert isinstance(json.loads(r2.stdout), dict)


def test_checkpoint_write_show_real_module(git_repo, data_root):
    pytest.importorskip("zloop.checkpoint")
    lines = _start_run(git_repo, data_root)
    rid = json.loads(lines[1])["run_id"]
    capsule = {"run_id": rid, "note": "cli checkpoint capsule", "facts": []}
    w = run_cli("checkpoint", "write", input=json.dumps(capsule),
                cwd=git_repo, data_root=data_root)
    assert w.returncode == 0, w.stderr
    cp = json.loads(w.stdout)
    assert cp.get("checkpoint_id")
    s = run_cli("checkpoint", "show", cp["checkpoint_id"],
                cwd=git_repo, data_root=data_root)
    assert s.returncode == 0, s.stderr
    cur = run_cli("checkpoint", "current", cwd=git_repo, data_root=data_root)
    assert cur.returncode == 0
    assert json.loads(cur.stdout)["checkpoint"] is not None


def test_install_uninstall_real_module(git_repo, data_root, tmp_path):
    pytest.importorskip("zloop.install")
    pytest.importorskip("zloop.hook")
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg = cfg_dir / "config.json"
    i = run_cli("install", "--config-path", str(cfg),
                cwd=git_repo, data_root=data_root)
    assert i.returncode == 0, i.stderr
    assert isinstance(json.loads(i.stdout), dict)
    u = run_cli("uninstall", "--config-path", str(cfg),
                cwd=git_repo, data_root=data_root)
    assert u.returncode == 0, u.stderr
    assert isinstance(json.loads(u.stdout), dict)


def test_install_uninstall_lazy_tolerance(git_repo, data_root, tmp_path):
    if _have_module("zloop.hook") and _have_module("zloop.install"):
        pytest.skip("parallel modules present — covered by real-module test")
    # hook selfcheck or install module is missing -> notice + exit 0,
    # and the user-level config is never touched.
    r = run_cli("install", cwd=git_repo, data_root=data_root)
    assert r.returncode == 0
    assert "module not available (parallel integration pending)" in r.stdout
    if not _have_module("zloop.install"):
        r2 = run_cli("uninstall", cwd=git_repo, data_root=data_root)
        assert r2.returncode == 0
        assert "module not available (parallel integration pending)" in r2.stdout
