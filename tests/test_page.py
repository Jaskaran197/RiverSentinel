"""Escalation logic of page.page() with every network call replaced: nothing here can dial a phone."""
import pytest

import page

ALERT = {"severity": "Danger to life", "station": "Bow River at Calgary", "summary": "s", "evidence": "e"}
CONTACTS = ["+14035550001", "+14035550002"]


@pytest.fixture
def fake_calls(monkeypatch):
    """Scripted call outcomes: set fake_calls.outcomes to a list of (place_error, answered) per contact, in order."""
    for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"):
        monkeypatch.setenv(k, "test")
    state = SimpleState()
    monkeypatch.setattr(page, "resolve_phone_number_id", lambda: "pn_test")
    def place(to, alert, pid):
        state.dialled.append(to)
        err, _ = state.outcomes[len(state.dialled) - 1]
        return (None, None, err) if err else (f"CA{len(state.dialled)}", f"conv{len(state.dialled)}", None)
    def wait(call_sid, ring_timeout_s):
        _, answered = state.outcomes[len(state.dialled) - 1]
        return ("completed", True) if answered else ("no-answer", False)
    monkeypatch.setattr(page, "place_agent_call", place)
    monkeypatch.setattr(page, "twilio_wait_final", wait)
    monkeypatch.setattr(page, "el_conversation_summary", lambda cid: {})
    monkeypatch.setattr(page, "http", lambda *a, **k: pytest.fail("page.http must not be called"))
    return state


class SimpleState:
    def __init__(self):
        self.outcomes, self.dialled = [], []


def statuses(attempts):
    return [a["status"] for a in attempts]


def test_first_contact_answers(fake_calls):
    fake_calls.outcomes = [(None, True), (None, True)]
    res = page.page(CONTACTS, ALERT, escalate=True, ring_timeout_s=1)
    assert statuses(res) == ["ANSWERED"] and fake_calls.dialled == CONTACTS[:1]


def test_escalates_to_second_contact(fake_calls):
    fake_calls.outcomes = [(None, False), (None, True)]
    assert statuses(page.page(CONTACTS, ALERT, escalate=True, ring_timeout_s=1)) == ["ESCALATED", "ANSWERED"]


def test_nobody_answers(fake_calls):
    fake_calls.outcomes = [(None, False), (None, False)]
    assert statuses(page.page(CONTACTS, ALERT, escalate=True, ring_timeout_s=1)) == ["ESCALATED", "NO_ANSWER"]


def test_no_escalation_stops_after_first(fake_calls):
    fake_calls.outcomes = [(None, False), (None, True)]
    assert statuses(page.page(CONTACTS, ALERT, escalate=False, ring_timeout_s=1)) == ["NO_ANSWER"]
    assert fake_calls.dialled == CONTACTS[:1]


def test_failed_placement_moves_on_when_escalating(fake_calls):
    fake_calls.outcomes = [("500: boom", None), (None, True)]
    res = page.page(CONTACTS, ALERT, escalate=True, ring_timeout_s=1)
    assert statuses(res) == ["FAILED", "ANSWERED"] and res[0]["error"] == "500: boom"


def test_attempts_store_masked_numbers(fake_calls):
    fake_calls.outcomes = [(None, True)]
    assert page.page(CONTACTS[:1], ALERT, ring_timeout_s=1)[0]["to"] == "+14***0001"


def test_missing_credentials_exit_before_dialling(fake_calls, monkeypatch):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN")
    with pytest.raises(SystemExit, match="TWILIO_AUTH_TOKEN"):
        page.page(CONTACTS, ALERT)
    assert fake_calls.dialled == []


def test_mask():
    assert page.mask("+14035551234") == "+14***1234" and page.mask("") == ""
