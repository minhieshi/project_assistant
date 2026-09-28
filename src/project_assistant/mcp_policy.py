from __future__ import annotations

import re


# Project Assistant is intentionally a read-only intelligence layer.  A local
# allowlist is the primary authorisation boundary for MCP tools; this secondary
# name check prevents obviously mutating tools from being approved by mistake.
_MUTATING_WORDS = {
    "add",
    "approve",
    "archive",
    "assign",
    "cancel",
    "commit",
    "create",
    "delete",
    "deploy",
    "disable",
    "enable",
    "executeWrite".lower(),
    "invite",
    "merge",
    "modify",
    "move",
    "patch",
    "post",
    "publish",
    "put",
    "reject",
    "remove",
    "rename",
    "resolve",
    "restart",
    "run",
    "send",
    "set",
    "start",
    "stop",
    "submit",
    "transition",
    "trigger",
    "update",
    "upload",
    "write",
}

# Generic dispatch tools can hide writes behind arguments. They are not eligible
# for unattended retrieval unless the server exposes a read-specific wrapper.
_UNSAFE_EXACT = {"execute", "mutate", "request", "invoke", "calltool", "call_tool"}


def _name_words(name: str) -> list[str]:
    # Split camelCase/PascalCase as well as separators, while retaining compact
    # lowercase names for exact checks (e.g. executeWrite -> execute + write).
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [word.lower() for word in re.split(r"[^A-Za-z0-9]+|\s+", expanded) if word]


def mcp_tool_retrieval_block_reason(name: str) -> str | None:
    compact = re.sub(r"[^a-z0-9]+", "", name.lower())
    if compact in _UNSAFE_EXACT:
        return "generic execution/dispatch tool is not safe for unattended read-only retrieval"
    words = set(_name_words(name))
    blocked = sorted(words & _MUTATING_WORDS)
    if blocked:
        return f"tool name appears mutating ({', '.join(blocked)})"
    return None


def mcp_tool_retrieval_eligible(name: str) -> bool:
    return mcp_tool_retrieval_block_reason(name) is None
