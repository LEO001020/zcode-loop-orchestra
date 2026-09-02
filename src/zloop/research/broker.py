"""zloop.research.broker — M4 Research Broker (VOL-15 §1/§4, VOL-04 §10).

Single Kimi K1 lane (decision D-10). The broker NEVER raises on per-question
lane failures: every question is captured into its own evidence record with
verification="source_unverified" and answer=None. The lane runs in an
independent temp cwd — never the project dir or any canonical writable root
(VOL-15 §7 / I42).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any, Optional

from .. import ids, redact
from ..evidence import BlobStore
from .kimi_server import KimiServerLane, messages_blob_bytes

LANE = "kimi"
TRUST = "external_untrusted"
CLAIM_MAX = 300
_RESEARCH_ID_RE = re.compile(r"RS\d{3,}")

# VOL-04 §10 evidence-record fields (single source of truth for the shape)
VOL04_FIELDS = (
    "ref", "research_id", "question_id", "lane", "query", "claim", "url",
    "title", "source_class", "observed_at", "published_at", "retrieved_at",
    "raw_ref", "content_hash", "verification", "trust",
)


def _allocate_research_id(project_dir: Path) -> str:
    """Next free RS### under project_dir/research/ (VOL-04 §1 via ids)."""
    root = Path(project_dir) / "research"
    best = 0
    if root.is_dir():
        for entry in root.iterdir():
            m = _RESEARCH_ID_RE.fullmatch(entry.name)
            if entry.is_dir() and m:
                best = max(best, int(m.group(0)[2:]))
    return ids.fmt_research(best + 1)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, path)


def _token_prefix(lane: Any) -> Optional[str]:
    """sha256 prefix of the lane token — only form allowed in artifacts."""
    try:
        fp = getattr(lane, "token_fingerprint", None)
        return fp() if callable(fp) else None
    except Exception:
        return None


def _shutdown_default_lane(lane: Any) -> None:
    """Kill the server tree iff the default lane spawned it (owned)."""
    try:
        shutdown = getattr(lane, "shutdown", None)
        if callable(shutdown):
            shutdown()
    except Exception:
        pass


def _run_question(lane: Any, store: BlobStore, research_id: str,
                  qid: str, query: Any, timeout_s: int) -> dict:
    now = ids.now_iso()
    rec = {
        "ref": f"web:{LANE}:{qid}",
        "research_id": research_id,
        "question_id": qid,
        "lane": LANE,
        "query": redact.redact_str(str(query)) if query else None,
        "claim": None,
        "url": None,            # K1 v1 surface has no URL; lane-synthesized
        "title": None,
        "source_class": "secondary",
        "observed_at": now,
        "published_at": None,   # model output has no publication time
        "retrieved_at": now,
        "raw_ref": None,
        "content_hash": None,
        "verification": "source_unverified",
        "trust": TRUST,
        "answer": None,
        "last_turn_reason": None,
        "session_id": None,
        "error": None,
    }
    if not query:
        rec["error"] = "missing query"
        return rec

    # I42: the lane NEVER runs in the project dir — independent temp cwd
    temp_cwd = Path(tempfile.mkdtemp(prefix="zloop-research-"))
    try:
        res = lane.ask(str(query), cwd=temp_cwd, timeout_s=timeout_s)
        answer = res.get("answer")
        reason = res.get("last_turn_reason")
        rec["last_turn_reason"] = reason
        rec["session_id"] = res.get("session_id")
        raw = res.get("raw_messages")
        if raw is not None:
            safe = redact.redact_obj(raw)
            digest = store.put(messages_blob_bytes(safe))
            rec["raw_ref"] = "blob:sha256:" + digest
        if reason == "completed" and answer:
            rec["answer"] = redact.redact_str(str(answer))
            rec["claim"] = rec["answer"][:CLAIM_MAX]
            rec["content_hash"] = sha256(
                rec["answer"].encode("utf-8")).hexdigest()
            rec["verification"] = "lane_reported"
        else:
            rec["error"] = f"no answer (last_turn_reason={reason})"
    except Exception as e:  # per-question isolation: never raise (M4)
        rec["error"] = redact.redact_str(f"{type(e).__name__}: {e}")[:300]
    finally:
        shutil.rmtree(temp_cwd, ignore_errors=True)
    return rec


def run_research(project_dir: Path, spec: dict, *, lane: Any = None) -> dict:
    """Run one research spec through the Kimi lane; write a bounded manifest.

    spec = {"research_id": optional "RS###", "questions": [{"id","query"}],
            "timeout_s": optional}. Returns {"research_id", "results"
            [evidence records], "openapi_digest"}. Per-question failures are
    captured in the records; only a malformed research_id raises.
    """
    project_dir = Path(project_dir)
    spec = spec or {}
    questions = list(spec.get("questions") or [])
    timeout_s = int(spec.get("timeout_s") or 180)
    research_id = str(spec.get("research_id") or "").strip() \
        or _allocate_research_id(project_dir)
    if not _RESEARCH_ID_RE.fullmatch(research_id):
        raise ValueError(
            f"invalid research_id {research_id!r} (expected RS###)")
    research_dir = project_dir / "research" / research_id
    research_dir.mkdir(parents=True, exist_ok=True)

    # default lane looked up at call time so tests can monkeypatch the
    # module attribute; an injected lane (parameter) is caller-owned
    default_lane = lane is None
    if lane is None:
        lane = KimiServerLane()

    openapi_digest = None
    try:
        openapi_digest = lane.openapi_digest()
    except Exception:
        openapi_digest = None

    store = BlobStore(project_dir / "blobs" / "sha256")
    results = []
    for i, q in enumerate(questions):
        q = q or {}
        qid = str(q.get("id") or f"Q{i + 1}")
        results.append(_run_question(lane, store, research_id, qid,
                                     q.get("query"), timeout_s))

    manifest = {
        "research_id": research_id,
        "created_at": ids.now_iso(),
        "lane": LANE,
        "openapi_digest": openapi_digest,
        "token_sha256_prefix": _token_prefix(lane),
        "timeout_s": timeout_s,
        "results": results,
    }
    out = {"research_id": research_id, "results": results,
           "openapi_digest": openapi_digest}
    try:
        _write_json(research_dir / "manifest.json", manifest)
    except Exception as e:  # manifest write failure must not lose results
        out["manifest_error"] = str(e)[:200]

    if default_lane:
        _shutdown_default_lane(lane)
    return out
