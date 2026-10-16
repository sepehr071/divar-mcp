"""Local state for saved watches and price history: one small JSON file. Not a tool module; nothing here talks to Divar."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .http import ApiError


def path() -> Path:
    return Path(os.environ.get("DIVAR_MCP_STATE") or Path.home() / ".divar-mcp" / "state.json")


def load() -> dict[str, Any]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"watches": data.get("watches") or {}, "history": data.get("history") or {}}


def save(state: dict[str, Any]) -> None:
    p = path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, p)
    except OSError as e:
        raise ApiError(f"Could not write the local state file {p}: {e}. Set DIVAR_MCP_STATE to a writable path.") from e
