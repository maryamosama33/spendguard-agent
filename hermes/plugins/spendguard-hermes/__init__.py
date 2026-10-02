"""Hermes plugin: SpendGuard guardrails that don't depend on the model obeying the prompt.

1. The owner decides. approve_expense / reject_expense are blocked in any
   turn that also received an expense (extract_expense or save_expense), so
   an approval or rejection can only come from a later message: the owner's.
2. Exact replies. SpendGuard tools put the message for the user in
   "reply_to_sender", "reply_to_owner" or "reply". Chat models tend to wrap it
   in their own (formal, sometimes English) report, so the last such text seen
   in a turn replaces the turn's final reply.
"""

import json
import threading
from typing import Any

TOOL_PREFIX = "mcp__spendguard__"
INTAKE_TOOLS = {TOOL_PREFIX + "extract_expense", TOOL_PREFIX + "save_expense"}
DECISION_TOOLS = {TOOL_PREFIX + "approve_expense", TOOL_PREFIX + "reject_expense"}
REPLY_KEYS = ("reply_to_sender", "reply_to_owner", "reply")

BLOCK_MESSAGE = (
    "Blocked: only the owner can approve or reject, in their own later message. "
    "A duplicate or price warning is not a reason to decide yourself. Send the "
    "save_expense reply_to_owner as your reply and end your turn."
)

_intake_turns: set[tuple[str, str]] = set()
_pending_replies: dict[tuple[str, str], str] = {}
_lock = threading.Lock()


def _as_data(value: Any) -> Any:
    """Tool results arrive as JSON text, possibly nested; decode what we can."""
    if isinstance(value, str):
        try:
            return _as_data(json.loads(value))
        except (ValueError, TypeError):
            return value
    return value


def find_reply(result: Any) -> str | None:
    """First reply text anywhere in a (possibly wrapped) tool result."""
    data = _as_data(result)
    if isinstance(data, dict):
        for key in REPLY_KEYS:
            if isinstance(data.get(key), str) and data[key].strip():
                return data[key]
        children = data.values()
    elif isinstance(data, list):
        children = data
    else:
        return None
    for child in children:
        if (found := find_reply(child)) is not None:
            return found
    return None


def on_pre_tool_call(tool_name: str = "", session_id: str = "", turn_id: str = "",
                     **_: Any) -> dict | None:
    key = (session_id, turn_id)
    with _lock:
        if tool_name in INTAKE_TOOLS:
            _intake_turns.add(key)
        elif tool_name in DECISION_TOOLS and key in _intake_turns:
            return {"action": "block", "message": BLOCK_MESSAGE}
    return None


def on_post_tool_call(tool_name: str = "", result: Any = None, session_id: str = "",
                      turn_id: str = "", **_: Any) -> None:
    if not tool_name.startswith(TOOL_PREFIX):
        return
    reply = find_reply(result)
    if reply is not None:
        with _lock:
            _pending_replies[(session_id, turn_id)] = reply


def on_transform_llm_output(response_text: str = "", session_id: str = "",
                            turn_id: str = "", **_: Any) -> str | None:
    key = (session_id, turn_id)
    with _lock:
        _intake_turns.discard(key)
        return _pending_replies.pop(key, None)


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("transform_llm_output", on_transform_llm_output)
