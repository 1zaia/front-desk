"""Agent tests without calling Bedrock: a fake model asks for tools and the real MCP server answers."""
import copy

import anyio
import pytest

from frontdesk import agent


@pytest.fixture(autouse=True)
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("FRONTDESK_DB", str(tmp_path / "agent.db"))
    monkeypatch.setenv("FRONTDESK_NOW", "2026-10-19T08:00")
    monkeypatch.setenv("FRONTDESK_DEMO", "0")
    from frontdesk import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "agent.db")
    agent._sessions.clear()


def _server():
    from frontdesk.server import mcp
    return mcp


class FakeModel:
    """Mimics the Converse API: follows a script of tool requests, then answers with text."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        step = self.script.pop(0)
        if isinstance(step, str):
            return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"text": step}]}}}
        content = [{"toolUse": {"toolUseId": f"t{i}", "name": name, "input": args}} for i, (name, args) in enumerate(step)]
        return {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": content}}}


def _text(answer: str) -> dict:
    return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"text": answer}]}}}


def test_schema_without_null_anyof():
    schema = {
        "type": "object", "title": "X",
        "properties": {
            "date": {"type": "string", "title": "Date"},
            "period": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "description": "Optional"},
        },
        "required": ["date"],
    }
    s = agent._simplify_schema(schema)
    assert s["properties"]["period"] == {"type": "string", "description": "Optional"}
    assert "title" not in s and "title" not in s["properties"]["date"]
    assert s["required"] == ["date"]


def test_booking_through_the_conversation():
    model = FakeModel([
        [("check_availability", {"date": "Friday", "service": "haircut", "period": "morning"})],
        "For a haircut on Friday morning I have 9 AM, 9:30 AM, or 10 AM. Which works best?",
        [("book_appointment", {"customer_name": "John", "service": "haircut", "date": "Friday", "time": "9 AM"})],
        "You're all set, John! A haircut on Friday at 9 AM.",
    ])

    async def run():
        r1 = await agent.respond("any openings for a haircut Friday morning?", server=_server(), converse=model)
        r2 = await agent.respond("9 AM, my name is John", session_id=r1["session"], server=_server(), converse=model)
        return r1, r2

    r1, r2 = anyio.run(run)
    assert r1["calls"][0]["tool"] == "check_availability"
    assert r1["calls"][0]["ok"] and "9 AM" in r1["calls"][0]["speech"]
    assert r2["session"] == r1["session"]
    assert r2["calls"][0]["tool"] == "book_appointment" and r2["calls"][0]["ok"]
    assert r2["reply"].startswith("You're all set, John")

    # The tools reached the model in the Converse API format
    specs = model.calls[0]["toolConfig"]["tools"]
    assert {"book_appointment", "check_availability", "daily_summary"} <= {s["toolSpec"]["name"] for s in specs}
    # The tool result went back to the model as a JSON toolResult
    returned = model.calls[1]["messages"][-1]["content"][0]["toolResult"]
    assert returned["toolUseId"] == "t0" and returned["content"][0]["json"]["ok"] is True
    # The second utterance carried the history of the first one
    assert len(model.calls[2]["messages"]) == 5
    # The system prompt is in English and has today's date
    assert "Monday, October 19, 2026" in model.calls[0]["system"][0]["text"]


def test_tool_error_goes_back_to_the_model():
    model = FakeModel([
        [("book_appointment", {"customer_name": "Ana", "service": "beard", "date": "Sunday", "time": "10 AM"})],
        "Sorry, we're closed on Sunday. Would another day work?",
    ])
    r = anyio.run(lambda: agent.respond("beard Sunday 10 AM, Ana", server=_server(), converse=model))
    assert not r["calls"][0]["ok"]
    assert "closed" in r["calls"][0]["speech"]
    assert "closed" in r["reply"]


def test_empty_utterance():
    with pytest.raises(ValueError):
        anyio.run(lambda: agent.respond("   ", server=_server(), converse=FakeModel([])))


def test_bedrock_error_becomes_a_clear_message():
    from botocore.exceptions import ClientError

    def refuse(**_):
        raise ClientError({"Error": {"Code": "UnrecognizedClientException", "Message": "The security token included in the request is invalid."}}, "Converse")

    with pytest.raises(agent.AgentError) as error:
        anyio.run(lambda: agent.respond("hi", server=_server(), converse=refuse))
    assert "aws login" in str(error.value) and "UnrecognizedClientException" in str(error.value)
    assert agent._sessions[next(iter(agent._sessions))].messages == []  # the failed utterance is not kept


def test_no_quota_moves_to_the_next_model(monkeypatch):
    from botocore.exceptions import ClientError
    monkeypatch.setenv("FRONTDESK_MODEL", "us.amazon.nova-lite-v1:0,us.amazon.nova-micro-v1:0")
    agent._paused.clear()
    tried = []

    def converse(**kw):
        tried.append(kw["modelId"])
        if kw["modelId"] == "us.amazon.nova-lite-v1:0":
            raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "Too many tokens per day, please wait before trying again."}}, "Converse")
        return _text("Hi!")

    r = anyio.run(lambda: agent.respond("hi", server=_server(), converse=converse))
    assert r["reply"] == "Hi!" and r["model"] == "us.amazon.nova-micro-v1:0"
    assert tried == ["us.amazon.nova-lite-v1:0", "us.amazon.nova-micro-v1:0"]
    # the model without quota stays paused: the next utterance goes straight to the second one
    tried.clear()
    anyio.run(lambda: agent.respond("hi again", server=_server(), converse=converse))
    assert tried == ["us.amazon.nova-micro-v1:0"]
    agent._paused.clear()


def test_all_without_quota_explains(monkeypatch):
    from botocore.exceptions import ClientError
    monkeypatch.setenv("FRONTDESK_MODEL", "a,b")
    agent._paused.clear()

    def converse(**kw):
        raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "Too many tokens per day, please wait before trying again."}}, "Converse")

    with pytest.raises(agent.AgentError) as error:
        anyio.run(lambda: agent.respond("hi", server=_server(), converse=converse))
    assert "daily Bedrock quota" in str(error.value) and "FRONTDESK_AGENT=rehearsal" in str(error.value)
    agent._paused.clear()


def test_auto_mode_falls_back_to_rehearsal(monkeypatch):
    """In the deployment (FRONTDESK_AGENT=auto), if Bedrock fails the conversation continues in rehearsal mode."""
    from starlette.testclient import TestClient
    from frontdesk import server

    async def fail(*a, **k):
        raise agent.AgentError("The AWS account has no daily Bedrock quota")

    monkeypatch.setenv("FRONTDESK_AGENT", "auto")
    monkeypatch.setenv("FRONTDESK_MCP_URL", "local")
    monkeypatch.setattr(agent, "respond", fail)
    monkeypatch.setattr(server, "_utterances_by_ip", {})
    monkeypatch.setattr(server, "_bedrock_down", {"until": 0.0, "notice": ""})
    with TestClient(server.app()) as c:
        r = c.post("/api/conversation", json={"text": "how much is a haircut?"})
    data = r.json()
    assert r.status_code == 200
    assert data["model"].startswith("rehearsal mode") and "no daily Bedrock quota" in data["notice"]
    assert data["calls"][0]["tool"] == "list_services"
    # for the next few minutes the server does not even try Bedrock: it goes straight to rehearsal
    calls = []

    async def count(*a, **k):
        calls.append(1)
        raise agent.AgentError("should not be called")

    monkeypatch.setattr(agent, "respond", count)
    with TestClient(server.app()) as c:
        r = c.post("/api/conversation", json={"text": "hi"})
    assert r.status_code == 200 and calls == [] and r.json()["notice"]
