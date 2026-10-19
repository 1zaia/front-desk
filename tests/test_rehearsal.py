"""Rehearsal mode: the rule-based agent uses the same MCP tools, without Bedrock."""
import anyio
import pytest

from frontdesk import rehearsal


@pytest.fixture(autouse=True)
def environment(tmp_path, monkeypatch):
    from frontdesk import db
    monkeypatch.setenv("FRONTDESK_NOW", "2026-10-18T18:00")  # a Sunday evening
    monkeypatch.setenv("FRONTDESK_DEMO", "0")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "rehearsal.db")
    rehearsal._states.clear()


def _conversation(*utterances):
    from frontdesk.server import mcp

    async def run():
        sid, outputs = None, []
        for u in utterances:
            r = await rehearsal.respond(u, sid, server=mcp)
            sid = r["session"]
            outputs.append(r)
        return outputs
    return anyio.run(run)


def test_book_in_three_turns():
    r = _conversation("Any openings for a haircut Friday morning?", "9 a.m.", "John")
    assert r[0]["calls"][0]["tool"] == "check_availability"
    assert r[0]["calls"][0]["arguments"] == {"date": "friday", "service": "haircut", "period": "morning"}
    assert r[1]["calls"] == [] and "name" in r[1]["reply"]
    assert r[2]["calls"][0]["tool"] == "book_appointment" and r[2]["calls"][0]["ok"]
    assert r[2]["reply"].startswith("You're all set, John!")
    assert r[2]["model"] == rehearsal.MODE_NAME


def test_everything_in_one_sentence_at_noon():
    r = _conversation("I'd like a haircut on Monday at noon, my name is Ana Paula")
    c = r[0]["calls"][0]
    assert c["tool"] == "book_appointment"
    assert c["arguments"]["time"] == "noon" and c["arguments"]["date"] == "monday"
    assert c["arguments"]["customer_name"] == "Ana Paula" and c["ok"]


def test_cancel_and_prices():
    r = _conversation("haircut and beard tomorrow at 10 AM, this is Peter", "how much is a beard trim?", "I need to cancel")
    assert r[0]["calls"][0]["arguments"]["service"] == "haircut and beard" and r[0]["calls"][0]["ok"]
    assert r[1]["calls"][0]["tool"] == "list_services"
    assert r[2]["calls"][0]["tool"] == "cancel_appointment" and r[2]["calls"][0]["ok"]


def test_asks_for_what_is_missing():
    r = _conversation("I want to book an appointment", "beard", "tomorrow afternoon")
    assert "service" in r[0]["reply"]
    assert "day" in r[1]["reply"]
    assert r[2]["calls"][0]["arguments"] == {"date": "tomorrow", "service": "beard trim", "period": "afternoon"}


def test_greeting_and_hours():
    r = _conversation("Hi, good morning")
    assert r[0]["reply"].startswith("Hi there!") and r[0]["calls"] == []
    r = _conversation("Are you open Sunday?")
    assert "closed" in r[0]["reply"]
    r = _conversation("Are you open on Saturday?")
    assert r[0]["reply"].startswith("Yes, we're open")


def test_speech_recognition_style_input():
    """Chrome's en-US recognizer writes times as '3:30 p.m.' and numbers as digits."""
    r = _conversation("Can I get a fade on Tuesday at 3:30 p.m.", "it's Marcus")
    assert r[0]["calls"] == [] and "name" in r[0]["reply"]
    # "Marcus" is one of the barbers, so it is not taken as the customer's name
    assert r[1]["calls"] == [] and "name" in r[1]["reply"]
    r = _conversation("Can I get a fade on Tuesday at 3:30 p.m. with Diego", "My name's Sam")
    c = r[1]["calls"][0]
    assert c["arguments"] == {"customer_name": "Sam", "service": "haircut", "date": "tuesday", "time": "3:30 pm", "barber": "Diego"}
    assert c["ok"]


def test_reschedule():
    r = _conversation("haircut Wednesday at 9 AM, my name is Leo", "can I move it to 2 PM?")
    assert r[0]["calls"][0]["ok"]
    assert r[1]["calls"][0]["tool"] == "reschedule_appointment"
    assert r[1]["calls"][0]["ok"] and "2 PM" in r[1]["reply"]


def test_owner_summary():
    r = _conversation("haircut tomorrow at 10 AM, I'm Nina", "how's my day tomorrow?")
    assert r[1]["calls"][0]["tool"] == "daily_summary"
    assert "1 customer" in r[1]["reply"]
