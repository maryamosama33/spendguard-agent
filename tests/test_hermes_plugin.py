import importlib.util
import json
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "hermes" / "plugins" / "spendguard-hermes" / "__init__.py"
SAVE = "mcp__spendguard__save_expense"
EXTRACT = "mcp__spendguard__extract_expense"
APPROVE = "mcp__spendguard__approve_expense"
REJECT = "mcp__spendguard__reject_expense"


@pytest.fixture
def plugin():
    spec = importlib.util.spec_from_file_location("spendguard_hermes", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call(plugin, tool, turn, result=None, session="s1"):
    """Simulate Hermes running one tool: pre hook, then (if not blocked) post hook."""
    directive = plugin.on_pre_tool_call(tool_name=tool, session_id=session, turn_id=turn)
    blocked = directive if directive and directive["action"] == "block" else None
    if blocked is None:
        plugin.on_post_tool_call(tool_name=tool, result=json.dumps(result or {}),
                                 session_id=session, turn_id=turn)
    return blocked


# Owner-decides guard

def test_reject_blocked_in_same_turn_as_new_expense(plugin):
    _call(plugin, EXTRACT, "t1")
    _call(plugin, SAVE, "t1", {"reply_to_owner": "موافق ولا مرفوض؟"})

    blocked = _call(plugin, REJECT, "t1")

    assert blocked["action"] == "block"
    # the owner still gets the approval request, not the model's text
    assert plugin.on_transform_llm_output("اترفض", session_id="s1", turn_id="t1") == "موافق ولا مرفوض؟"


def test_reject_blocked_after_text_request(plugin):
    _call(plugin, "mcp__spendguard__extract_expense_from_text", "t1")

    assert _call(plugin, REJECT, "t1")["action"] == "block"


def test_approve_blocked_in_same_turn_as_new_expense(plugin):
    _call(plugin, SAVE, "t1")

    assert _call(plugin, APPROVE, "t1")["action"] == "block"


def test_owner_decision_in_later_turn_allowed(plugin):
    _call(plugin, SAVE, "t1")
    plugin.on_transform_llm_output("x", session_id="s1", turn_id="t1")

    assert _call(plugin, APPROVE, "t2", {"reply": "اتعتمد"}) is None
    assert plugin.on_transform_llm_output("x", session_id="s1", turn_id="t2") == "اتعتمد"


def _turn(plugin, turn, sender, session="s1"):
    plugin.on_pre_llm_call(session_id=session, turn_id=turn, sender_id=sender)


def test_non_owner_cannot_approve(plugin, monkeypatch):
    monkeypatch.setenv("SPENDGUARD_OWNER_IDS", "1386120774")
    _turn(plugin, "t1", "555")

    blocked = _call(plugin, APPROVE, "t1")

    assert blocked["action"] == "block"
    assert plugin.on_transform_llm_output("ok", session_id="s1", turn_id="t1") == plugin.NOT_OWNER_REPLY


def test_owner_can_approve(plugin, monkeypatch):
    monkeypatch.setenv("SPENDGUARD_OWNER_IDS", "1386120774, 42")
    _turn(plugin, "t1", 42)  # Hermes may pass the ID as an int

    assert _call(plugin, APPROVE, "t1", {"reply": "اتعتمد"}) is None


def test_terminal_turn_without_sender_not_restricted(plugin, monkeypatch):
    monkeypatch.setenv("SPENDGUARD_OWNER_IDS", "1386120774")
    _turn(plugin, "t1", "")

    assert _call(plugin, REJECT, "t1") is None


def test_no_owner_list_means_no_sender_restriction(plugin, monkeypatch):
    monkeypatch.delenv("SPENDGUARD_OWNER_IDS", raising=False)
    _turn(plugin, "t1", "555")

    assert _call(plugin, APPROVE, "t1") is None


def test_other_session_not_blocked(plugin):
    _call(plugin, SAVE, "t1", session="engineer-chat")

    assert _call(plugin, APPROVE, "t1", session="owner-chat") is None


# Verified sender

def test_telegram_sender_stamped_on_save_whatever_the_model_passed(plugin):
    plugin.on_pre_llm_call(session_id="s1", turn_id="t1", sender_id=555, platform="telegram")

    directive = plugin.on_pre_tool_call(tool_name=SAVE, session_id="s1", turn_id="t1")

    assert directive == {"action": "modify", "args": {"telegram_sender": "555"}}


def test_terminal_turn_stamps_empty_sender(plugin):
    plugin.on_pre_llm_call(session_id="s1", turn_id="t1", sender_id="", platform="cli")

    directive = plugin.on_pre_tool_call(tool_name=APPROVE, session_id="s1", turn_id="t1")

    assert directive == {"action": "modify", "args": {"telegram_sender": ""}}


def test_extract_is_not_stamped(plugin):
    assert plugin.on_pre_tool_call(tool_name=EXTRACT, session_id="s1", turn_id="t1") is None


# Greetings

@pytest.mark.parametrize("message", ["اهلا ازيك", "أهلاً، إزيك؟", "السلام عليكم", "hi", "صباح الخير يا باشا"])
def test_plain_greeting_gets_fixed_egyptian_reply(plugin, message):
    plugin.on_pre_llm_call(session_id="s1", turn_id="t1", user_message=message)

    assert plugin.on_transform_llm_output("مرحبا، كيف حالك؟", session_id="s1", turn_id="t1") == plugin.GREETING_REPLY


@pytest.mark.parametrize("message", ["اهلا، دفعت 3000 جنيه نقل رمل", "تمام", "موافق", "ازيك، صرفنا كام الشهر ده؟"])
def test_greeting_with_a_request_or_decision_is_left_to_the_agent(plugin, message):
    plugin.on_pre_llm_call(session_id="s1", turn_id="t1", user_message=message)

    assert plugin.on_transform_llm_output("x", session_id="s1", turn_id="t1") is None


def test_tool_reply_in_a_greeting_turn_still_wins(plugin):
    plugin.on_pre_llm_call(session_id="s1", turn_id="t1", user_message="اهلا")
    _call(plugin, SAVE, "t1", {"reply_to_owner": "طلب صرف جديد"})

    assert plugin.on_transform_llm_output("x", session_id="s1", turn_id="t1") == "طلب صرف جديد"


# Exact replies

def test_finds_reply_in_wrapped_json_result(plugin):
    result = json.dumps({"result": json.dumps({"id": 5, "reply_to_owner": "طلب صرف جديد رقم 5"})})

    assert plugin.find_reply(result) == "طلب صرف جديد رقم 5"


def test_no_reply_in_plain_result(plugin):
    assert plugin.find_reply(json.dumps({"is_duplicate": False})) is None


def test_final_reply_replaced_once(plugin):
    _call(plugin, SAVE, "t1", {"reply_to_owner": "موافق ولا مرفوض؟"})

    assert plugin.on_transform_llm_output("report", session_id="s1", turn_id="t1") == "موافق ولا مرفوض؟"
    assert plugin.on_transform_llm_output("report", session_id="s1", turn_id="t1") is None


def test_non_spendguard_tools_ignored(plugin):
    plugin.on_post_tool_call(tool_name="mcp__other__tool", result=json.dumps({"reply": "x"}),
                             session_id="s1", turn_id="t1")

    assert plugin.on_transform_llm_output("text", session_id="s1", turn_id="t1") is None
