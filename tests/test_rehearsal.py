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


def _conversation(*utterances, caller=None):
    """caller: the caller ID phone number, as the simulator sends it."""
    from frontdesk.server import mcp

    async def run():
        sid, outputs = None, []
        for u in utterances:
            r = await rehearsal.respond(u, sid, server=mcp, context={"caller_phone": caller} if caller else None)
            sid = r["session"]
            outputs.append(r)
        return outputs
    return anyio.run(run)


def test_book_in_three_turns():
    r = _conversation("Any openings for a haircut Friday morning?", "9 a.m.", "John, 310 555 0100")
    assert r[0]["calls"][0]["tool"] == "check_availability"
    assert r[0]["calls"][0]["arguments"] == {"date": "friday", "service": "Haircut", "period": "morning"}
    # name and phone are always required, asked for together
    assert r[1]["calls"] == [] and "name" in r[1]["reply"] and "phone" in r[1]["reply"]
    assert r[2]["calls"][0]["tool"] == "book_appointment" and r[2]["calls"][0]["ok"]
    assert r[2]["calls"][0]["arguments"]["customer_phone"] == "310 555 0100"
    assert r[2]["reply"].startswith("You're all set, John!")
    assert r[2]["model"] == rehearsal.MODE_NAME


def test_everything_in_one_sentence_at_noon():
    r = _conversation("I'd like a haircut on Monday at noon, my name is Ana Paula", caller="+13105550101")
    c = r[0]["calls"][0]
    assert c["tool"] == "book_appointment"
    assert c["arguments"]["time"] == "noon" and c["arguments"]["date"] == "monday"
    assert c["arguments"]["customer_name"] == "Ana Paula" and c["ok"]


def test_cancel_and_prices():
    r = _conversation("haircut and beard tomorrow at 10 AM, this is Peter", "how much is a beard trim?", "I need to cancel",
                      caller="+13105550102")
    assert r[0]["calls"][0]["arguments"]["service"] == "Haircut and beard" and r[0]["calls"][0]["ok"]
    assert r[1]["calls"][0]["tool"] == "list_services"
    assert r[2]["calls"][0]["tool"] == "cancel_appointment" and r[2]["calls"][0]["ok"]


def test_asks_for_what_is_missing():
    r = _conversation("I want to book an appointment", "beard", "tomorrow afternoon")
    assert "service" in r[0]["reply"]
    assert "day" in r[1]["reply"]
    assert r[2]["calls"][0]["arguments"] == {"date": "tomorrow", "service": "Beard trim", "period": "afternoon"}


def test_greeting_and_hours():
    r = _conversation("Hi, good morning")
    assert r[0]["reply"].startswith("Hi there!") and r[0]["calls"] == []
    r = _conversation("Are you open Sunday?")
    assert "closed" in r[0]["reply"]
    r = _conversation("Are you open on Saturday?")
    assert r[0]["reply"].startswith("Yes, we're open")


def test_speech_recognition_style_input():
    """Chrome's en-US recognizer writes times as '3:30 p.m.' and numbers as digits."""
    r = _conversation("Can I get a fade on Tuesday at 3:30 p.m.", "it's Marcus", caller="+13105550103")
    assert r[0]["calls"] == [] and "name" in r[0]["reply"]
    # "Marcus" is one of the barbers, so it is not taken as the customer's name
    assert r[1]["calls"] == [] and "name" in r[1]["reply"]
    r = _conversation("Can I get a fade on Tuesday at 3:30 p.m. with Diego", "My name's Sam", caller="+13105550104")
    c = r[1]["calls"][0]
    assert c["arguments"] == {"customer_name": "Sam", "customer_phone": "+13105550104", "service": "haircut",
                              "date": "tuesday", "time": "3:30 pm", "staff": "Diego"}
    assert c["ok"]


def test_reschedule():
    r = _conversation("haircut Wednesday at 9 AM, my name is Leo", "can I move it to 2 PM?", caller="+13105550105")
    assert r[0]["calls"][0]["ok"]
    assert r[1]["calls"][0]["tool"] == "reschedule_appointment"
    assert r[1]["calls"][0]["ok"] and "2 PM" in r[1]["reply"]


def test_owner_summary():
    r = _conversation("haircut tomorrow at 10 AM, I'm Nina", "how's my day tomorrow?", caller="+13105550106")
    assert r[1]["calls"][0]["tool"] == "daily_summary"
    assert "1 customer" in r[1]["reply"]


def test_waitlist_after_a_full_slot():
    _conversation("haircut Friday at 10 AM, I'm Ana", caller="+13105550107")
    _conversation("haircut Friday at 10 AM, I'm Bea", caller="+13105550108")
    r = _conversation("Can I get a haircut Friday at 10 AM? My name is Sam", "yes please", caller="+13105550109")
    assert not r[0]["calls"][-1]["ok"] and "waitlist" in r[0]["reply"]
    c = r[1]["calls"][0]
    assert c["tool"] == "join_waitlist" and c["ok"]
    assert c["arguments"] == {"customer_name": "Sam", "customer_phone": "+13105550109", "service": "Haircut",
                              "date": "friday", "time": "10 am"}
    # Ana cancels (found by her caller ID): Sam gets the 10 AM slot automatically
    r = _conversation("I need to cancel, this is Ana", caller="+13105550107")
    assert r[0]["calls"][0]["tool"] == "cancel_appointment" and r[0]["calls"][0]["ok"]


def test_returning_customer_gets_the_usual(monkeypatch):
    monkeypatch.setenv("FRONTDESK_DEMO", "1")
    r = _conversation("Hi, it's Sam", "yes, Friday at 11 AM", caller="+13105550142")
    assert r[0]["calls"][0]["tool"] == "customer_history"
    assert r[0]["calls"][0]["arguments"]["customer_phone"] == "+13105550142"
    assert r[0]["reply"].startswith("Welcome back, Sam!") and r[0]["reply"].endswith("Want the usual?")
    c = r[1]["calls"][0]
    assert c["tool"] == "book_appointment" and c["ok"]
    assert c["arguments"]["service"] == "the usual" and c["arguments"]["customer_name"] == "Sam Torres"
    assert r[1]["reply"].startswith("You're all set, Sam! Your usual beard trim")


def test_owner_blocks_time():
    r = _conversation("Block Diego's Thursday afternoon")
    c = r[0]["calls"][0]
    assert c["tool"] == "block_time" and c["ok"]
    assert c["arguments"] == {"staff": "Diego", "date": "thursday", "period": "afternoon"}
    r = _conversation("Marcus is off Friday from 1 to 3 pm")
    assert r[0]["calls"][0]["arguments"] == {"staff": "Marcus", "date": "friday", "start_time": "1 pm", "end_time": "3 pm"}
    assert "from 1 PM to 3 PM" in r[0]["reply"]


def test_caller_id_recognizes_the_regular_without_asking(monkeypatch):
    monkeypatch.setenv("FRONTDESK_DEMO", "1")
    r = _conversation("Hey there", "yes, Friday at 11 AM", caller="+13105550142")
    assert r[0]["reply"].startswith("Welcome back, Sam!")
    assert r[1]["calls"][0]["ok"] and r[1]["calls"][0]["arguments"]["customer_name"] == "Sam Torres"


def test_new_caller_only_gives_the_name(monkeypatch):
    r = _conversation("Haircut tomorrow at 10 AM", "Jordan", caller="+13105550199")
    assert r[0]["calls"] == [] and r[0]["reply"] == "Perfect. Can I get your name?"
    assert r[1]["calls"][0]["tool"] == "book_appointment" and r[1]["calls"][0]["ok"]
    assert r[1]["calls"][0]["arguments"]["customer_phone"] == "+13105550199"


def test_other_business_types(tmp_path, monkeypatch):
    """A salon: services and stylists come from the business, not from barbershop words."""
    from frontdesk import tenancy
    owner = tenancy.create_account("ava@example.com", "Ava", "password123")
    salon = tenancy.create_business(owner, "Glow Studio", "hair_salon", "America/Los_Angeles")
    with tenancy.serving(salon):
        r = _conversation("I'd like color on Wednesday at 11 with Mia, I'm Kate", caller="+13105550120")
    c = r[0]["calls"][0]
    assert c["tool"] == "book_appointment" and c["ok"], r[0]["reply"]
    assert c["arguments"]["service"] == "Color" and c["arguments"]["staff"] == "Mia"
    assert "with Mia" in r[0]["reply"]
