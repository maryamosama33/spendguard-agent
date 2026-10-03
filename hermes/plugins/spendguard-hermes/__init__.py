"""Hermes plugin: SpendGuard guardrails that don't depend on the model obeying the prompt.

1. The owner decides. approve_expense / reject_expense are blocked in any
   turn that also received an expense (extract_expense[_from_text] or
   save_expense), so an approval or rejection can only come from a later
   message; and, when SPENDGUARD_OWNER_IDS is set, only if that message's
   sender is an owner (others get NOT_OWNER_REPLY). Turns without a sender ID
   (the terminal demo) are not restricted by sender.
2. Verified sender. save_expense / approve_expense / reject_expense get the
   real Telegram sender ID as "telegram_sender" (empty elsewhere), whatever
   the model passed, so SpendGuard can forward an engineer's request to the
   owner's chat and send the decision back.
3. Exact replies. SpendGuard tools put the message for the user in
   "reply_to_sender", "reply_to_owner" or "reply". Chat models tend to wrap it
   in their own (formal, sometimes English) report, so the last such text seen
   in a turn replaces the turn's final reply.
"""

import json
import os
import threading
from typing import Any

TOOL_PREFIX = "mcp__spendguard__"
INTAKE_TOOLS = {TOOL_PREFIX + name for name in
                ("extract_expense", "extract_expense_from_text", "save_expense")}
DECISION_TOOLS = {TOOL_PREFIX + "approve_expense", TOOL_PREFIX + "reject_expense"}
SENDER_STAMPED_TOOLS = DECISION_TOOLS | {TOOL_PREFIX + "save_expense"}
REPLY_KEYS = ("reply_to_sender", "reply_to_owner", "reply")

BLOCK_MESSAGE = (
    "Blocked: only the owner can approve or reject, in their own later message. "
    "A duplicate or price warning is not a reason to decide yourself. Send the "
    "save_expense reply_to_owner as your reply and end your turn."
)

NOT_OWNER_MESSAGE = (
    "Blocked: this sender is not the owner, so they cannot approve or reject. "
    "End your turn; the user is told only the owner decides."
)
NOT_OWNER_REPLY = "الموافقة والرفض لصاحب الشركة بس. الطلب متسجل ومستني قراره."

_intake_turns: set[tuple[str, str]] = set()
_turn_senders: dict[tuple[str, str], str] = {}
_turn_platforms: dict[tuple[str, str], str] = {}
_pending_replies: dict[tuple[str, str], str] = {}
_lock = threading.Lock()


def owner_ids() -> set[str]:
    raw = os.environ.get("SPENDGUARD_OWNER_IDS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _is_non_owner(sender: str) -> bool:
    owners = owner_ids()
    return bool(owners) and bool(sender) and sender not in owners


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


def on_pre_llm_call(session_id: str = "", turn_id: str = "", sender_id: Any = "",
                    platform: str = "", **_: Any) -> None:
    """Remember who sent this turn's message (empty in the terminal)."""
    with _lock:
        _turn_senders[(session_id, turn_id)] = str(sender_id or "")
        _turn_platforms[(session_id, turn_id)] = platform or ""


def _telegram_sender(key: tuple[str, str]) -> str:
    return _turn_senders.get(key, "") if _turn_platforms.get(key) == "telegram" else ""


def on_pre_tool_call(tool_name: str = "", session_id: str = "", turn_id: str = "",
                     **_: Any) -> dict | None:
    key = (session_id, turn_id)
    with _lock:
        if tool_name in DECISION_TOOLS and key in _intake_turns:
            return {"action": "block", "message": BLOCK_MESSAGE}
        if tool_name in DECISION_TOOLS and _is_non_owner(_turn_senders.get(key, "")):
            _pending_replies[key] = NOT_OWNER_REPLY
            return {"action": "block", "message": NOT_OWNER_MESSAGE}
        if tool_name in INTAKE_TOOLS:
            _intake_turns.add(key)
        if tool_name in SENDER_STAMPED_TOOLS:
            return {"action": "modify", "args": {"telegram_sender": _telegram_sender(key)}}
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
        _turn_senders.pop(key, None)
        _turn_platforms.pop(key, None)
        return _pending_replies.pop(key, None)


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("transform_llm_output", on_transform_llm_output)
