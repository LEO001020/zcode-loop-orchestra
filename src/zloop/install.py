"""zloop.install — hook config management (VOL-05 §1).

Writes/reads the user-level `~/.zcode/cli/config.json` hooks block: all
`type: "process"`, single entrypoint `zloop-hook handle`, no matcher
(capture everything), seven events. Conservative v1: if an existing
non-zloop hooks config is present we refuse and ask for a manual merge —
never clobber a config we did not write. All writes are atomic
(tmp + os.replace) and every pre-existing config is backed up to
~/.zloop/hygiene-backup/ before being replaced.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from . import paths

SEVEN_EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
]

HOOKS_TIMEOUT_MS = 8000
HOOKS_MAX_OUTPUT_BYTES = 32768

PathLike = Union[str, os.PathLike]


def _config_path(config_path: Optional[PathLike] = None) -> Path:
    if config_path is not None:
        return Path(config_path)
    return Path.home() / ".zcode" / "cli" / "config.json"


def _load(path: Path) -> Optional[dict]:
    """Read config JSON; None if the file does not exist. Raises
    ValueError on invalid JSON / non-object (callers decide policy)."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"config is not readable JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("config top level is not a JSON object")
    return data


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, path)  # atomic replace (paths.py lesson)


def _hooks_block(hook_command: str, args: list, timeout_ms: int) -> dict:
    return {
        "enabled": True,
        "timeoutMs": timeout_ms,
        "maxOutputBytes": HOOKS_MAX_OUTPUT_BYTES,
        "events": {
            ev: [{"hooks": [{"type": "process",
                            "command": hook_command,
                            "args": list(args)}]}]
            for ev in SEVEN_EVENTS
        },
    }


def _zloop_managed(hooks: Any) -> bool:
    """Marker check: does this hooks block reference zloop?"""
    if not hooks:
        return False
    try:
        return "zloop" in json.dumps(hooks)
    except (TypeError, ValueError):
        return False


def install_hooks(hook_command: str, args: list, *, timeout_ms: int = 8000,
                  config_path: Optional[PathLike] = None) -> dict:
    """Install the VOL-05 §1 hooks block, preserving every other top-level
    key. Refuses (ok False) if a non-zloop hooks config is already present."""
    path = _config_path(config_path)
    try:
        existing = _load(path)
    except ValueError as e:
        return {"ok": False,
                "reason": f"existing config unreadable; manual merge required ({e})"}

    hooks = existing.get("hooks") if existing else None
    if hooks and not _zloop_managed(hooks):
        return {"ok": False,
                "reason": "existing non-zloop hooks config present; manual merge required"}

    # Backup the previous file (only if one existed) before replacing.
    backup_path: Optional[str] = None
    if existing is not None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%f")
        bdir = paths.hygiene_backup_dir()
        bdir.mkdir(parents=True, exist_ok=True)
        backup = bdir / f"config-{ts}.json"
        try:
            shutil.copy2(path, backup)
            backup_path = str(backup)
        except OSError:
            backup_path = None  # non-fatal: install proceeds

    merged = dict(existing) if existing else {}
    merged["hooks"] = _hooks_block(hook_command, args, timeout_ms)
    _atomic_write(path, merged)
    return {"ok": True, "config_path": str(path),
            "events": len(SEVEN_EVENTS), "backup": backup_path}


def uninstall_hooks(config_path: Optional[PathLike] = None) -> dict:
    """Remove ONLY the zloop-managed `hooks` key; keep everything else."""
    path = _config_path(config_path)
    if not path.exists():
        return {"ok": True, "removed": False, "reason": "no config"}
    try:
        data = _load(path)
    except ValueError as e:
        return {"ok": False, "reason": str(e)}
    assert data is not None  # file exists
    hooks = data.get("hooks")
    if not hooks:
        return {"ok": True, "removed": False, "reason": "no hooks present"}
    if not _zloop_managed(hooks):
        return {"ok": False, "reason": "hooks not managed by zloop"}
    data.pop("hooks")
    _atomic_write(path, data)
    return {"ok": True, "removed": True, "config_path": str(path)}


def hook_status(config_path: Optional[PathLike] = None) -> dict:
    """Inspect the config without modifying it."""
    path = _config_path(config_path)
    status: dict = {
        "config_exists": path.exists(),
        "config_path": str(path),
        "hooks_enabled": False,
        "event_count": 0,
        "zloop_managed": False,
        "command": None,
    }
    if not path.exists():
        return status
    try:
        data = _load(path)
    except ValueError:
        return status
    if data is None:
        return status
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return status
    status["hooks_enabled"] = hooks.get("enabled") is True
    status["zloop_managed"] = _zloop_managed(hooks)
    events = hooks.get("events")
    if isinstance(events, dict):
        status["event_count"] = len(events)
        for matchers in events.values():
            if not (isinstance(matchers, list) and matchers
                    and isinstance(matchers[0], dict)):
                continue
            entry_hooks = matchers[0].get("hooks")
            if isinstance(entry_hooks, list) and entry_hooks \
                    and isinstance(entry_hooks[0], dict):
                status["command"] = entry_hooks[0].get("command")
                break
    return status
