"""M4 Research Broker tests (VOL-15 §3/§4, VOL-04 §10).

No live kimi dependency by default: the broker is exercised through a
FakeLane, and the real KimiServerLane is exercised end-to-end against a
stdlib stub HTTP server that implements the P-KIM1-verified contract.
The one real quota-consuming test is gated behind ZLOOP_KIMI_LIVE.
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import threading
from hashlib import sha256
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zloop import redact                      # noqa: E402
from zloop.evidence import BlobStore          # noqa: E402
from zloop.research import broker, kimi_server  # noqa: E402


# ---------------------------------------------------------------------------
# FakeLane: canned answers, no network, no kimi
# ---------------------------------------------------------------------------

class FakeLane:
    def __init__(self, answers=None, errors=None, reason="completed"):
        self.answers = dict(answers or {})   # exact query -> answer
        self.errors = dict(errors or {})     # exact query -> exception
        self.reason = reason
        self.calls = []

    def openapi_digest(self):
        return "f" * 64

    def ask(self, question, *, cwd=None, timeout_s=180):
        cwd_path = Path(cwd) if cwd is not None else None
        self.calls.append({
            "question": question,
            "cwd": cwd_path,
            "cwd_existed": cwd_path is not None and cwd_path.is_dir(),
        })
        if question in self.errors:
            raise self.errors[question]
        ans = self.answers.get(question, "default answer")
        msgs = [
            {"role": "user",
             "content": [{"type": "text", "text": question}]},
            {"role": "assistant",
             "content": [{"type": "text", "text": ans}]},
        ]
        return {"answer": ans, "last_turn_reason": self.reason,
                "session_id": "session_fake",
                "raw_messages": msgs,
                "raw_messages_ref": "0" * 64}


def _proj(tmp_path):
    return tmp_path / "proj"


def _blob_text(proj, rec):
    store = BlobStore(proj / "blobs" / "sha256")
    digest = rec["raw_ref"].split(":", 2)[2]
    assert store.has(digest)
    return store.get(digest).decode("utf-8")


# ---------------------------------------------------------------------------
# broker: manifest shape + evidence records
# ---------------------------------------------------------------------------

def test_manifest_shape_and_claim_truncation(tmp_path):
    proj = _proj(tmp_path)
    lane = FakeLane(answers={"find x": "B" * 400 + " tail"})
    out = broker.run_research(proj, {
        "questions": [{"id": "Q1", "query": "find x"},
                      {"id": "Q2", "query": "other"}]}, lane=lane)

    assert out["research_id"] == "RS001"
    assert out["openapi_digest"] == "f" * 64
    assert [r["question_id"] for r in out["results"]] == ["Q1", "Q2"]

    manifest = json.loads((proj / "research" / "RS001" / "manifest.json")
                          .read_text(encoding="utf-8"))
    assert manifest["research_id"] == "RS001"
    assert manifest["lane"] == "kimi"
    assert manifest["openapi_digest"] == "f" * 64
    assert manifest["timeout_s"] == 180
    assert manifest["results"] == out["results"]

    rec = out["results"][0]
    for field in broker.VOL04_FIELDS:
        assert field in rec, field
    assert rec["ref"] == "web:kimi:Q1"
    assert rec["research_id"] == "RS001"
    assert rec["lane"] == "kimi"
    assert rec["query"] == "find x"
    assert rec["claim"] == "B" * 300          # first 300 chars only
    assert rec["content_hash"] == sha256(("B" * 400 + " tail")
                                         .encode()).hexdigest()
    assert rec["verification"] == "lane_reported"
    assert rec["observed_at"] and rec["retrieved_at"]
    # the full transcript lives in the blob CAS
    blob = _blob_text(proj, rec)
    assert "B" * 400 in blob


def test_failure_isolation_broker_never_raises(tmp_path):
    proj = _proj(tmp_path)
    lane = FakeLane(errors={"boom": RuntimeError("provider exploded")},
                    answers={"ok q": "fine"})
    out = broker.run_research(proj, {
        "questions": [{"id": "Q1", "query": "boom"},
                      {"id": "Q2", "query": "ok q"}]}, lane=lane)

    r1, r2 = out["results"]
    assert r1["verification"] == "source_unverified"
    assert r1["answer"] is None and r1["claim"] is None
    assert r1["raw_ref"] is None and r1["content_hash"] is None
    assert "provider exploded" in r1["error"]
    assert r2["verification"] == "lane_reported"
    assert r2["answer"] == "fine"
    # manifest still written with both records
    manifest = json.loads((proj / "research" / "RS001" / "manifest.json")
                         .read_text(encoding="utf-8"))
    assert len(manifest["results"]) == 2


def test_failed_turn_reason_marks_source_unverified(tmp_path):
    # a lane that RETURNS (no exception) but with a failed turn reason
    lane = FakeLane(answers={"q": ""}, reason="failed")
    out = broker.run_research(_proj(tmp_path), {
        "questions": [{"id": "Q1", "query": "q"}]}, lane=lane)
    rec = out["results"][0]
    assert rec["verification"] == "source_unverified"
    assert rec["answer"] is None and rec["claim"] is None
    assert "last_turn_reason=failed" in rec["error"]
    # raw messages are still archived for forensics
    assert rec["raw_ref"] and rec["raw_ref"].startswith("blob:sha256:")


def test_redaction_in_blob_and_claim(tmp_path):
    proj = _proj(tmp_path)
    secret_answer = ("prelude API_TOKEN=secret123456 and "
                     "Bearer abcdef123456789 postlude")
    lane = FakeLane(answers={"q": secret_answer})
    out = broker.run_research(proj, {
        "questions": [{"id": "Q1", "query": "q"}]}, lane=lane)
    rec = out["results"][0]

    for secret in ("secret123456", "abcdef123456789"):
        assert secret not in rec["claim"]
        assert secret not in rec["answer"]
        assert secret not in rec["content_hash"]
        blob = _blob_text(proj, rec)
        assert secret not in blob
    assert redact.REDACTED in rec["claim"]
    manifest_text = (proj / "research" / "RS001" / "manifest.json") \
        .read_text(encoding="utf-8")
    assert "secret123456" not in manifest_text
    assert "abcdef123456789" not in manifest_text


def test_research_id_allocation_sequence(tmp_path):
    proj = _proj(tmp_path)
    lane = FakeLane()
    a = broker.run_research(proj, {"questions": []}, lane=lane)
    b = broker.run_research(proj, {"questions": []}, lane=lane)
    assert (a["research_id"], b["research_id"]) == ("RS001", "RS002")

    c = broker.run_research(proj, {"research_id": "RS042", "questions": []},
                           lane=lane)
    assert c["research_id"] == "RS042"
    assert (proj / "research" / "RS042" / "manifest.json").exists()

    d = broker.run_research(proj, {"questions": []}, lane=lane)
    assert d["research_id"] == "RS043"        # continues after explicit id

    with pytest.raises(ValueError, match="research_id"):
        broker.run_research(proj, {"research_id": "../evil", "questions": []},
                            lane=lane)


def test_trust_always_external_untrusted(tmp_path):
    lane = FakeLane(errors={"bad": RuntimeError("x")})
    out = broker.run_research(_proj(tmp_path), {
        "questions": [{"id": "Q1", "query": "bad"},
                      {"id": "Q2", "query": "good"}]}, lane=lane)
    assert all(r["trust"] == "external_untrusted" for r in out["results"])
    assert all(r["verification"] in
               ("lane_reported", "source_unverified", "verified_fetch",
                "conflicted") for r in out["results"])


def test_lane_runs_in_isolated_temp_cwd(tmp_path):
    proj = _proj(tmp_path)
    lane = FakeLane(answers={"q1": "a", "q2": "b"})
    broker.run_research(proj, {
        "questions": [{"id": "Q1", "query": "q1"},
                      {"id": "Q2", "query": "q2"}]}, lane=lane)
    assert len(lane.calls) == 2
    cwds = []
    for call in lane.calls:
        cwd = call["cwd"]
        assert cwd is not None
        assert call["cwd_existed"] is True       # existed during the ask
        assert proj not in cwd.parents            # I42: never project dir
        assert not cwd.exists()                   # cleaned up afterwards
        cwds.append(cwd)
    assert cwds[0] != cwds[1]                      # independent per question


def test_missing_query_captured_not_raised(tmp_path):
    out = broker.run_research(_proj(tmp_path), {
        "questions": [{"id": "Q1"}]}, lane=FakeLane())
    rec = out["results"][0]
    assert rec["question_id"] == "Q1"
    assert rec["verification"] == "source_unverified"
    assert rec["error"] == "missing query"


# ---------------------------------------------------------------------------
# terminal-answer extraction (P-KIM1: skip meta / non-assistant / tool-only)
# ---------------------------------------------------------------------------

def test_extract_answer_last_real_assistant():
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "Q"}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "tool_call_id": "t1",
             "tool_name": "WebSearch", "input": {}}]},        # tool-only
        {"role": "tool", "content": [
            {"type": "tool_result", "tool_call_id": "t1", "output": {}}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "final answer"}]},
        {"role": "assistant", "type": "meta:session.resume_hint",
         "content": [{"type": "text", "text": "hint"}]},      # trailing meta
    ]
    assert kimi_server.extract_answer(msgs) == "final answer"


def test_extract_answer_no_assistant():
    assert kimi_server.extract_answer(
        [{"role": "user", "content": [{"type": "text", "text": "Q"}]}]) == ""
    assert kimi_server.extract_answer([]) == ""
    assert kimi_server.extract_answer(None) == ""


# ---------------------------------------------------------------------------
# the real lane against a stdlib stub server (no kimi dependency)
# ---------------------------------------------------------------------------

class _KimiStub(http.server.ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), _KimiStubHandler)
        self.token = "stub-token-12345678"
        self.poll_count = 0
        self.prompt_bodies = []
        self.profile_bodies = []
        self.create_bodies = []
        self.aborted = False


class _KimiStubHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _send(self, payload, status=200):
        body = payload if isinstance(payload, bytes) \
            else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        return self.headers.get("Authorization", "") == \
            "Bearer " + self.server.token

    def do_GET(self):
        if self.path == "/api/v1/healthz":          # no auth (P-KIM1)
            return self._send({"code": 0, "msg": "success",
                               "data": {"ok": True}})
        if not self._authed():                     # 401 without Bearer
            return self._send({"code": 40101, "msg": "unauthorized"},
                              status=401)
        path = self.path.split("?")[0]
        if path == "/openapi.json":                # requires auth (live)
            return self._send(b'{"openapi":"3.1.0","paths":{}}')
        if path == "/api/v1/sessions/session_test1/messages":
            items = [
                {"role": "user",
                 "content": [{"type": "text", "text": "the question"}]},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "tool_call_id": "t1",
                     "tool_name": "WebSearch", "input": {}}]},
                {"role": "assistant", "content": [
                    {"type": "text",
                     "text": "FINAL API_TOKEN=secret123456"}]},
                {"role": "assistant", "type": "meta:session.resume_hint",
                 "content": [{"type": "text", "text": "hint"}]},
            ]
            return self._send({"code": 0, "msg": "success",
                               "data": {"items": items, "has_more": False}})
        if path == "/api/v1/sessions/session_test1":
            self.server.poll_count += 1
            ltr = "completed" if self.server.poll_count >= 2 else None
            return self._send({"code": 0, "msg": "success",
                               "data": {"id": "session_test1",
                                        "last_turn_reason": ltr}})
        return self._send({"code": 40400, "msg": "not found"}, status=404)

    def do_POST(self):
        if not self._authed():
            return self._send({"code": 40101, "msg": "unauthorized"},
                              status=401)
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/v1/sessions":
            self.server.create_bodies.append(body)
            return self._send({"code": 0, "msg": "success",
                               "data": {"id": "session_test1"}})
        if self.path == "/api/v1/sessions/session_test1/profile":
            self.server.profile_bodies.append(body)
            return self._send({"code": 0, "msg": "success",
                               "data": {"id": "session_test1"}})
        if self.path == "/api/v1/sessions/session_test1/prompts":
            self.server.prompt_bodies.append(body)
            return self._send({"code": 0, "msg": "success",
                               "data": {"prompt_id": "msg_1",
                                        "status": "running"}})
        if self.path == "/api/v1/sessions/session_test1:abort":
            self.server.aborted = True
            return self._send({"code": 0, "msg": "success",
                               "data": {"aborted": True}})
        return self._send({"code": 40400, "msg": "not found"}, status=404)


@pytest.fixture()
def stub_server():
    srv = _KimiStub()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_ask_full_flow_against_stub(stub_server, tmp_path):
    token_file = tmp_path / "server.token"
    token_file.write_text("stub-token-12345678\n", encoding="utf-8")
    lane = kimi_server.KimiServerLane(
        base_url=f"http://127.0.0.1:{stub_server.server_port}",
        token_path=token_file)

    # already running -> healthy, NOT owned, nothing spawned
    assert lane.ensure_server(timeout_s=5) is True
    assert lane.owned is False
    assert lane.token_fingerprint() == sha256(
        b"stub-token-12345678").hexdigest()[:8]

    digest = lane.openapi_digest()
    assert digest == sha256(b'{"openapi":"3.1.0","paths":{}}').hexdigest()

    ask_cwd = tmp_path / "research-cwd"
    ask_cwd.mkdir()
    res = lane.ask("the question", cwd=ask_cwd, timeout_s=30)

    assert res["session_id"] == "session_test1"
    assert res["last_turn_reason"] == "completed"
    assert res["prompt_endpoint"] == "POST /api/v1/sessions/session_test1/prompts"
    assert res["prompt_body_shape"] == "content:array_of_typed_parts"
    # terminal answer skips tool-only and trailing meta records
    assert res["answer"] == "FINAL API_TOKEN=secret123456"
    # raw messages are redacted and digest = canonical bytes of them
    assert "secret123456" not in json.dumps(res["raw_messages"])
    assert res["raw_messages_ref"] == sha256(
        kimi_server.messages_blob_bytes(res["raw_messages"])).hexdigest()
    # abort was called as cleanup
    assert stub_server.aborted is True

    # create body: metadata.cwd only, no agent_config (P-KIM1: dropped)
    create = stub_server.create_bodies[0]
    assert create["metadata"]["cwd"] == str(ask_cwd)
    assert "agent_config" not in create
    # the model went through the profile endpoint instead
    assert stub_server.profile_bodies[0]["agent_config"]["model"] == \
        kimi_server.DEFAULT_MODEL
    # prompt body: content is an array of typed parts
    assert stub_server.prompt_bodies[0] == {
        "content": [{"type": "text", "text": "the question"}]}

    # not owned -> shutdown must not touch the (still alive) server
    lane.shutdown()
    assert lane.owned is False
    assert stub_server.aborted is True


def test_missing_token_clear_error(tmp_path):
    lane = kimi_server.KimiServerLane(token_path=tmp_path / "nope.token")
    with pytest.raises(kimi_server.KimiError, match="token"):
        lane.token()
    assert lane.token_fingerprint() is None


def test_ensure_server_without_kimi_exe(tmp_path, monkeypatch):
    lane = kimi_server.KimiServerLane(
        base_url="http://127.0.0.1:9", token_path=tmp_path / "t")
    monkeypatch.setattr(kimi_server.KimiServerLane, "_locate_kimi_exe",
                        lambda self: None)
    with pytest.raises(kimi_server.KimiError, match="kimi executable"):
        lane.ensure_server(timeout_s=2)


# ---------------------------------------------------------------------------
# the ONE live test (opt-in: consumes real kimi quota)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not os.environ.get("ZLOOP_KIMI_LIVE"),
                    reason="live kimi quota")
def test_live_kimi_ask():
    lane = kimi_server.KimiServerLane()
    try:
        res = lane.ask("Reply with exactly: OK", timeout_s=120)
        assert res["session_id"].startswith("session_")
        assert res["last_turn_reason"] in (
            "completed", "cancelled", "failed", "timeout")
        assert res["prompt_body_shape"] == "content:array_of_typed_parts"
        if res["last_turn_reason"] == "completed":
            assert res["answer"].strip() == "OK"
        # raw messages always redacted + content-addressed
        assert res["raw_messages_ref"] == sha256(
            kimi_server.messages_blob_bytes(res["raw_messages"])).hexdigest()
    finally:
        lane.shutdown()  # kills the tree iff WE spawned it
