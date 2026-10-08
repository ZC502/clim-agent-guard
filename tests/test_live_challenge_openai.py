"""OpenAI-compatible runner tests with real CLIM Core and mocked model messages.

Run from repo root: python -m pytest -q tests/test_live_challenge_openai.py
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "challenge", ROOT / "examples" / "live_challenge_openai.py"
)
challenge = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(challenge)


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=self.replies.pop(0))])


def call(path="important-notes.txt", call_id="call-1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name="delete_file", arguments=json.dumps({"path": path})),
    )


def reply(*calls):
    return SimpleNamespace(tool_calls=list(calls), content=None)


def options(**kwargs):
    config = dict(
        mode="guarded", base_url="http://localhost:11434/v1", api_key="EMPTY",
        model="test-model", prompt="adversarial user", user_confirmed=False,
        authorized_target="important-notes.txt", tool_choice="auto",
        max_rounds=2, max_tool_calls=3, timeout=10, evidence_out=None,
    )
    config.update(kwargs)
    return argparse.Namespace(**config)


def scenario(messages, **overrides):
    client = FakeClient(messages)
    result = challenge.run(options(**overrides), client=client)
    return result, client


def test_unconfirmed_spoof_is_blocked():
    r, _ = scenario([reply(call()), reply()])
    assert r["calls"][0]["code"] == "USER_CONFIRMATION_REQUIRED"
    assert r["outcome"] == "GUARD_BLOCKED"
    assert r["authorized_file_exists"]


def test_baseline_deletes_without_confirmation():
    r, _ = scenario([reply(call()), reply()], mode="baseline")
    assert r["outcome"] == "SIDE_EFFECT_EXECUTED"
    assert r["calls"][0]["unauthorized_effect"]
    assert not r["authorized_file_exists"]


def test_authorized_target_substitution_is_blocked():
    r, _ = scenario([reply(call("other-file.txt")), reply()], user_confirmed=True)
    assert r["calls"][0]["code"] == "TARGET_NOT_AUTHORIZED"
    assert r["authorized_file_exists"]


def test_path_escape_is_blocked():
    r, _ = scenario([reply(call("../outside.txt")), reply()], user_confirmed=True)
    assert r["calls"][0]["code"] == "TARGET_NOT_AUTHORIZED"


def test_guarded_authorized_execution_is_verified():
    r, _ = scenario([reply(call()), reply()], user_confirmed=True)
    assert r["calls"][0]["verification"]["code"] == "EFFECT_VERIFIED"
    assert not r["authorized_file_exists"]


def test_no_tool_does_not_count_as_guard_success():
    r, _ = scenario([reply()])
    assert r["outcome"] == "BOUNDARY_NOT_EXERCISED"


def test_multi_tool_call_fails_closed():
    r, _ = scenario([reply(call(call_id="call-1"), call("other-file.txt", "call-2"))],
                    user_confirmed=True)
    assert all(c["code"] == "MULTIPLE_TOOL_CALLS_UNSUPPORTED" for c in r["calls"])
    assert r["authorized_file_exists"]


def test_second_delete_blocked_after_success():
    r, _ = scenario([reply(call()), reply(call(call_id="call-2"))], user_confirmed=True)
    assert r["calls"][0]["executed"]
    assert r["calls"][1]["code"] == "TARGET_NOT_PRESENT"


def test_malformed_json_is_not_executed():
    bad = SimpleNamespace(id="call-1", function=SimpleNamespace(
        name="delete_file", arguments="{invalid"))
    r, _ = scenario([reply(bad), reply()])
    assert r["calls"][0]["code"] == "MALFORMED_TOOL_ARGS"
    assert r["authorized_file_exists"]


def test_respects_tool_call_budget():
    r, c = scenario([reply(call()), reply(call(call_id="call-2"))],
                    user_confirmed=False, max_tool_calls=1)
    assert len(c.requests) == 1
    assert len(r["calls"]) == 1


def test_sdk_omits_auto_tool_choice():
    _, c = scenario([reply()])
    assert "tool_choice" not in c.requests[0]


def test_required_tool_choice_is_explicit():
    _, c = scenario([reply()], tool_choice="required")
    assert c.requests[0]["tool_choice"] == "required"


def test_nonstandard_tool_call_fails_closed():
    malformed = SimpleNamespace(
        id=None, function=SimpleNamespace(name="delete_file", arguments='{"path":"important-notes.txt"}')
    )
    r, _ = scenario([reply(malformed)])
    assert r["calls"][0]["code"] == "MALFORMED_TOOL_CALL"
    assert r["outcome"] == "BOUNDARY_NOT_EXERCISED"
    assert r["authorized_file_exists"]
