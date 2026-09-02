"""zloop.redact — recursive secret redaction (VOL-06 §1.2, VOL-17 §2).

Hard invariant I13: redaction happens BEFORE hash/serialize/journal.
Every string passed through the evidence plane must go through redact_obj /
redact_str first.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

REDACTED = "<redacted>"

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # PEM / ssh private key blocks
    ("pem", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.S)),
    # Bearer tokens
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}")),
    # key/token/secret/password assignment: name = value  or  name: value
    ("kv", re.compile(
        r"(?i)\b([A-Za-z0-9_.\-]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|"
        r"CREDENTIAL|AUTH[_-]?HEADER|PRIVATE[_-]?KEY|ACCESS[_-]?KEY)[A-Za-z0-9_.\-]*)"
        r"(\s*[:=]\s*)(\"[^\"]{4,}\"|'[^']{4,}'|[^\s,;}\]]{6,})")),
    # sk- style provider keys
    ("provider_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}")),
]

# Filenames whose whole content is treated as secret if they appear as a
# standalone token in tool input/output (best-effort, name-level only).
SECRET_FILE_RE = re.compile(
    r"(?i)(^|[\\/])(\.env(\.|$)|\.env|id_rsa|id_ed25519|.*\.pem|.*\.keystore|"
    r"credentials\.json|auth\.json|wallet\.json)(\s|$|\"|')")

# Dict keys whose NAME marks the value as secret ({"API_TOKEN": "x"}).
SECRET_KEYNAME_RE = re.compile(
    r"(?i)(^|[^A-Za-z0-9_])(API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|"
    r"CREDENTIAL|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|AUTH)([^A-Za-z0-9_]|$)"
    r"|(?i)^(API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)$")


def redact_str(s: str) -> str:
    if not isinstance(s, str):
        return s
    for kind, pat in _PATTERNS:
        if kind == "kv":
            s = pat.sub(lambda m: m.group(1) + m.group(2) + REDACTED, s)
        else:
            s = pat.sub(REDACTED, s)
    return s


def _walk(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_str(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            ks = str(k)
            if SECRET_FILE_RE.search(ks) or SECRET_KEYNAME_RE.search(ks):
                out[ks] = REDACTED
            else:
                out[ks] = _walk(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_walk(x) for x in obj]
    return obj


def redact_obj(obj: Any) -> Any:
    """Recursively redact a JSON-like payload (returns a plain structure)."""
    return _walk(obj)


def scan_secrets(text: str) -> list[str]:
    """Return kinds of secret patterns present (for artifact-export re-scan)."""
    hits: list[str] = []
    for kind, pat in _PATTERNS:
        if kind != "kv" and pat.search(text):
            hits.append(kind)
        elif kind == "kv":
            m = pat.search(text)
            if m and m.group(3).strip("\"'") != REDACTED:
                hits.append(kind)
    return hits


def iter_secret_kinds() -> Iterable[str]:
    return [k for k, _ in _PATTERNS]
