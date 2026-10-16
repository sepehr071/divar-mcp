"""Server status and help."""

from __future__ import annotations

import os
import time
from typing import Any

from . import __version__, core, store
from .http import ApiError, api
from .registry import tool


@tool("Server status")
async def dv_status() -> dict[str, Any]:
    """Is Divar reachable and how fast: version, a live latency check, caches and local watch state.

    Call first when other tools fail (it tells a blocked network from a bad argument). The check is one small
    request to Divar. The server instructions describe the usual tool order (cities and categories, then dv_search,
    then dv_post).
    """
    t0 = time.monotonic()
    try:
        await api("/places/find?lat=35.7&long=51.4")
        reach: dict[str, Any] = {"ok": True, "latency_ms": round((time.monotonic() - t0) * 1000)}
    except ApiError as e:
        reach = {"ok": False, "error": str(e)}
    state = store.load()
    return {
        "version": __version__,
        "divar": reach,
        "proxy": bool(os.environ.get("DIVAR_MCP_PROXY")),
        "cached": sorted(k.split(":")[0] for k in core._CACHE),
        "state_file": str(store.path()),
        "watches": len(state["watches"]),
        "price_histories": len(state["history"]),
    }
