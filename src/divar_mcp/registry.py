"""Collects tool functions from the tool modules; server.py registers them."""

from collections.abc import Callable
from typing import Any

TOOLS: list[tuple[Callable[..., Any], str]] = []
LOCAL: set[Callable[..., Any]] = set()  # tools that write a local state file (never anything on Divar)


def tool(title: str, *, local: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark an async function as an MCP tool with a human-readable title."""

    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        TOOLS.append((fn, title))
        if local:
            LOCAL.add(fn)
        return fn

    return register
