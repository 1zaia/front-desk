"""Round-trip tests: start the real server and call the tools through an MCP client."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import anyio
import pytest

from mcp import Client

TOOLS = {"list_services", "check_availability", "book_appointment", "cancel_appointment",
         "reschedule_appointment", "daily_summary"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(tmp_path, **extra):
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "FRONTDESK_DB": str(tmp_path / "test.db"),
           "FRONTDESK_NOW": "2026-10-19T08:00", "FRONTDESK_DEMO": "0", **extra}
    proc = subprocess.Popen([sys.executable, "-m", "frontdesk.server"], env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with urllib.request.urlopen(base + "/health") as r:
                if r.status == 200:
                    break
        except OSError:
            time.sleep(0.1)
    return proc, base


@pytest.fixture
def server(tmp_path):
    proc, base = _start(tmp_path)
    yield base
    proc.terminate()
    proc.wait(timeout=5)


def _initialize(base: str, host: str | None = None) -> tuple[int, dict]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if host:
        headers["Host"] = host
    req = urllib.request.Request(base + "/mcp", data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}


def test_initialize_2025_11_25(server):
    """The server accepts the handshake at the protocol version the hackathon requires."""
    status, answer = _initialize(server)
    assert status == 200
    assert answer["result"]["protocolVersion"] == "2025-11-25"
    assert answer["result"]["serverInfo"]["name"] == "front-desk"


def _data(result) -> dict:
    text = "".join(c.text for c in result.content if getattr(c, "type", "") == "text")
    return json.loads(text)


def test_tools_listed(server):
    async def run():
        async with Client(server + "/mcp", mode="legacy") as client:
            return await client.list_tools()

    assert {t.name for t in anyio.run(run).tools} == TOOLS


def test_full_conversation(server):
    """The demo script: check, book, read the summary, reschedule and cancel."""
    async def run():
        r = {}
        async with Client(server + "/mcp", mode="legacy") as c:
            r["services"] = _data(await c.call_tool("list_services", {}))
            r["open"] = _data(await c.call_tool("check_availability", {"date": "Friday", "service": "haircut", "period": "morning"}))
            r["book"] = _data(await c.call_tool("book_appointment", {"customer_name": "john", "service": "haircut", "date": "Friday", "time": "9 AM"}))
            r["taken"] = _data(await c.call_tool("book_appointment", {"customer_name": "Ana", "service": "haircut", "date": "Friday", "time": "9 AM", "barber": r["book"]["appointment"]["staff"]}))
            r["summary"] = _data(await c.call_tool("daily_summary", {"date": "Friday"}))
            r["reschedule"] = _data(await c.call_tool("reschedule_appointment", {"customer_name": "john", "new_date": "Friday", "new_time": "4 PM"}))
            r["cancel"] = _data(await c.call_tool("cancel_appointment", {"customer_name": "John"}))
            r["sunday"] = _data(await c.call_tool("book_appointment", {"customer_name": "Ana", "service": "beard", "date": "Sunday", "time": "10 AM"}))
        return r

    r = anyio.run(run)
    assert "haircut for $35" in r["services"]["speech"]
    assert r["open"]["ok"] and "Friday, October 23" in r["open"]["speech"] and "9 AM" in r["open"]["speech"]
    assert r["book"]["ok"] and r["book"]["speech"].startswith("You're all set, John! A haircut on Friday")
    assert not r["taken"]["ok"] and "already taken" in r["taken"]["speech"]
    assert r["summary"]["total_customers"] == 1
    assert r["reschedule"]["ok"] and r["reschedule"]["appointment"]["start"] == "2026-10-23T16:00"
    assert r["cancel"]["ok"] and r["cancel"]["appointment"]["status"] == "cancelled"
    assert not r["sunday"]["ok"] and "closed" in r["sunday"]["speech"]
    for step, data in r.items():
        print(f"{step:>10}: {data['speech']}")


def test_api_schedule(server):
    with urllib.request.urlopen(server + "/api/schedule?date=2026-10-23") as resp:
        data = json.loads(resp.read())
    assert data["business"] == "Main Street Barbers"
    assert data["date"] == "2026-10-23"


def test_api_week(server):
    with urllib.request.urlopen(server + "/api/week?days=7") as resp:
        data = json.loads(resp.read())
    assert data["staff"] == ["Marcus", "Diego"]
    assert len(data["days"]) == 7
    monday, sunday = data["days"][0], data["days"][6]
    assert monday["date"] == "2026-10-19" and monday["open"] and monday["opens"] == "09:00"
    assert sunday["weekday"] == "Sunday" and not sunday["open"]


def test_pages(server):
    for path in ("/simulator", "/dashboard", "/demo", "/simulador", "/painel"):
        with urllib.request.urlopen(server + path) as resp:
            assert resp.status == 200 and "text/html" in resp.headers["Content-Type"]
            assert 'lang="en"' in resp.read().decode()


def test_conversation_without_text(server):
    req = urllib.request.Request(server + "/api/conversation", data=b'{"text": ""}', method="POST",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(req)
    assert error.value.code == 400


def test_annotations_and_structured_output(server):
    """Read and write tools are clearly marked, and results come back as structuredContent."""
    async def run():
        async with Client(server + "/mcp", mode="legacy") as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            r = await c.call_tool("list_services", {})
            return tools, r

    tools, r = anyio.run(run)
    for name in ("list_services", "check_availability", "daily_summary"):
        assert tools[name].annotations.read_only_hint is True
    assert tools["cancel_appointment"].annotations.destructive_hint is True
    assert tools["book_appointment"].annotations.read_only_hint is False
    assert all(t.title for t in tools.values())
    assert all(t.output_schema for t in tools.values())
    assert r.structured_content["ok"] is True and "Haircut" in str(r.structured_content["services"])


def test_tool_latency(server):
    """Alexa+ asks for round trips under 500 ms; each call here has to stay well below that."""
    async def run():
        async with Client(server + "/mcp", mode="legacy") as c:
            times = []
            for _ in range(5):
                t0 = time.perf_counter()
                await c.call_tool("check_availability", {"date": "Friday", "service": "haircut"})
                times.append(time.perf_counter() - t0)
            return max(times)

    assert anyio.run(run) < 0.5


def test_public_host_allowed(tmp_path):
    """Behind a proxy, the public domain in FRONTDESK_HOSTS must pass the Host header protection."""
    proc, base = _start(tmp_path, FRONTDESK_HOSTS="front-desk.example.com")
    try:
        assert _initialize(base, "front-desk.example.com")[0] == 200
        assert _initialize(base, "other.example.com")[0] == 421
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_rate_limit_per_ip(monkeypatch):
    from types import SimpleNamespace
    from frontdesk import server
    monkeypatch.setattr(server, "RATE_PER_IP", 3)
    server._utterances_by_ip.clear()
    request = SimpleNamespace(headers={"x-forwarded-for": "203.0.113.9"}, client=None)
    assert [server._within_limits(request) for _ in range(4)] == [True, True, True, False]
    other = SimpleNamespace(headers={"x-forwarded-for": "198.51.100.7"}, client=None)
    assert server._within_limits(other)
    server._utterances_by_ip.clear()
