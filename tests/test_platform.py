"""Many businesses on one server: sign-up, sign-in, the owner's management API and one MCP endpoint per business."""
import json

import pytest
from starlette.testclient import TestClient

from frontdesk import db, tenancy

JSON = {"Content-Type": "application/json"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "demo.db")
    monkeypatch.setenv("FRONTDESK_NOW", "2026-10-19T08:00")
    monkeypatch.setenv("FRONTDESK_AGENT", "rehearsal")
    from frontdesk import server, web_api
    monkeypatch.setattr(web_api, "_utterances_by_ip", {})
    tenancy._failed_logins.clear()
    with TestClient(server.app(), base_url="https://127.0.0.1:8443") as c:
        yield c


def signup(client, email="maya@example.com", business="Glow Studio", kind="hair_salon"):
    r = client.post("/api/auth/signup", headers=JSON, json={
        "name": "Maya", "email": email, "password": "correct horse", "business_name": business,
        "business_type": kind, "timezone": "America/New_York"})
    assert r.status_code == 200, r.text
    return r.json()["business"]


def mcp_call(client, path, tool, args, key=None):
    headers = {**JSON, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    r = client.post(path, headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                 "params": {"name": tool, "arguments": args}})
    return r


def tool_data(r) -> dict:
    content = r.json()["result"]["content"]
    return json.loads("".join(c["text"] for c in content if c["type"] == "text"))


def test_signup_creates_a_business_of_that_type(client):
    slug = signup(client)
    assert slug == "glow-studio"
    me = client.get("/api/me").json()
    assert me["account"]["email"] == "maya@example.com"
    assert [b["slug"] for b in me["businesses"]] == ["glow-studio"]
    setup = client.get(f"/api/b/{slug}/setup").json()
    assert setup["name"] == "Glow Studio" and setup["type"] == "hair_salon" and setup["staff_noun"] == "stylist"
    assert setup["timezone"] == "America/New_York"
    assert "Color" in [s["name"] for s in setup["services"]]


def test_signup_validation(client):
    r = client.post("/api/auth/signup", headers=JSON, json={"name": "A", "email": "nope", "password": "x"})
    assert r.status_code == 400 and "email" in r.json()["error"]
    r = client.post("/api/auth/signup", headers=JSON, json={"name": "A", "email": "a@b.co", "password": "short"})
    assert "8 characters" in r.json()["error"]
    signup(client)
    r = client.post("/api/auth/signup", headers=JSON, json={"name": "B", "email": "maya@example.com", "password": "longenough",
                                                            "business_name": "X", "business_type": "spa", "timezone": "UTC"})
    assert "already an account" in r.json()["error"]


def test_same_name_gets_its_own_address(client):
    signup(client)
    client.post("/api/auth/logout")
    assert signup(client, email="other@example.com") == "glow-studio-2"


def test_login_logout_and_cookie(client):
    signup(client)
    assert "fd_session" in client.cookies
    client.post("/api/auth/logout")
    assert client.get("/api/me").status_code == 401
    r = client.post("/api/auth/login", headers=JSON, json={"email": "MAYA@example.com", "password": "wrong password"})
    assert r.status_code == 400 and r.json()["error"] == "Wrong email or password."
    r = client.post("/api/auth/login", headers=JSON, json={"email": "maya@example.com", "password": "correct horse"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie
    assert client.get("/api/me").status_code == 200


def test_too_many_wrong_passwords(client):
    signup(client)
    client.post("/api/auth/logout")
    for _ in range(5):
        client.post("/api/auth/login", headers=JSON, json={"email": "maya@example.com", "password": "nope nope"})
    r = client.post("/api/auth/login", headers=JSON, json={"email": "maya@example.com", "password": "correct horse"})
    assert "Too many attempts" in r.json()["error"]


def test_owner_api_is_private(client):
    slug = signup(client)
    client.post("/api/auth/logout")
    assert client.get(f"/api/b/{slug}/setup").status_code == 401
    signup(client, email="intruder@example.com", business="Other Place", kind="spa")
    assert client.get(f"/api/b/{slug}/setup").status_code == 403
    assert client.get(f"/api/b/{slug}/week").status_code == 401  # the live board isn't public either
    # mutations need a JSON body
    r = client.post("/api/b/other-place/services", data="name=x", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 400


def test_manage_everything_by_hand(client):
    slug = signup(client)
    base = f"/api/b/{slug}"
    setup = client.get(f"{base}/setup").json()
    # services
    r = client.post(f"{base}/services", headers=JSON, json={"name": "Bridal updo", "duration_min": 90, "price_cents": 15000})
    updo = r.json()["service"]
    assert updo["name"] == "Bridal updo"
    client.patch(f"{base}/services/{updo['id']}", headers=JSON, json={"price_cents": 16000})
    # team
    jo = client.post(f"{base}/staff", headers=JSON, json={"name": "Jo"}).json()["staff"]
    # hours: closed on Monday, open Wednesday
    hours = [{"weekday": d, "opens": "10:00", "closes": "18:00"} for d in (1, 2, 3, 4, 5)]
    assert client.put(f"{base}/hours", headers=JSON, json={"hours": hours}).status_code == 200
    bad = client.put(f"{base}/hours", headers=JSON, json={"hours": [{"weekday": 1, "opens": "18:00", "closes": "10:00"}]})
    assert "after opening" in bad.json()["error"]
    # customers
    kate = client.post(f"{base}/customers", headers=JSON, json={"name": "kate moss", "phone": "(212) 555-0199"}).json()["customer"]
    assert kate["name"] == "Kate Moss" and kate["phone"] == "+12125550199"
    dup = client.post(f"{base}/customers", headers=JSON, json={"name": "Kim", "phone": "212 555 0199"})
    assert "already has that phone" in dup.json()["error"]
    # appointment by hand, then move, complete
    r = client.post(f"{base}/appointments", headers=JSON, json={
        "customer_name": "Kate Moss", "phone": "2125550199", "service_id": updo["id"], "staff_id": jo["id"],
        "date": "2026-10-21", "time": "10:00", "notes": "allergic to latex"})
    appt = r.json()["appointment"]
    assert appt["source"] == "manual" and appt["customer_id"] == kate["id"] and appt["price_cents"] == 16000
    closed = client.post(f"{base}/appointments", headers=JSON, json={
        "customer_name": "Walk In", "service_id": updo["id"], "date": "2026-10-19", "time": "10:00"})
    assert "closed" in closed.json()["error"]
    moved = client.patch(f"{base}/appointments/{appt['id']}", headers=JSON, json={"time": "14:00"}).json()["appointment"]
    assert moved["start"] == "2026-10-21T14:00"
    walk_in = client.post(f"{base}/appointments", headers=JSON, json={
        "customer_name": "Walk In", "service_id": updo["id"], "staff_id": jo["id"], "date": "2026-10-21", "time": "14:30"})
    assert "isn't free" in walk_in.json()["error"] or "taken" in walk_in.json()["error"]
    # the week view sees it, the customer history too
    cal = client.get(f"{base}/calendar?start=2026-10-21&days=1").json()
    assert any(a["id"] == appt["id"] for a in cal["days"][0]["appointments"])
    detail = client.get(f"{base}/customers/{kate['id']}").json()["customer"]
    assert [a["id"] for a in detail["appointments"]] == [appt["id"]]
    # cancel by hand; a block; the waitlist
    client.patch(f"{base}/appointments/{appt['id']}", headers=JSON, json={"status": "cancelled"})
    block = client.post(f"{base}/blocks", headers=JSON, json={"staff_id": jo["id"], "date": "2026-10-22", "start": "12:00",
                                                             "end": "13:00", "reason": "lunch"}).json()["block"]
    assert client.delete(f"{base}/blocks/{block['id']}").status_code == 200
    entry = client.post(f"{base}/waitlist", headers=JSON, json={"customer_name": "Kate Moss", "phone": "2125550199",
                                                               "service_id": updo["id"], "date": "2026-10-22"}).json()["entry"]
    assert client.get(f"{base}/waitlist").json()["waitlist"][0]["id"] == entry["id"]
    client.delete(f"{base}/waitlist/{entry['id']}")
    assert client.get(f"{base}/waitlist").json()["waitlist"] == []
    # a service already booked is archived, not deleted
    assert client.delete(f"{base}/services/{updo['id']}").json()["result"] == "archived"
    stats = client.get(f"{base}/stats").json()
    assert stats["customers"] == 1
    # settings
    r = client.patch(f"{base}/settings", headers=JSON, json={"name": "Glow Studio SoHo", "timezone": "Europe/Lisbon"})
    assert r.json()["name"] == "Glow Studio SoHo"
    assert client.patch(f"{base}/settings", headers=JSON, json={"timezone": "Mars/Base"}).status_code == 400


def test_each_business_has_its_own_data_and_mcp_endpoint(client):
    salon = signup(client)
    client.post("/api/auth/logout")
    signup(client, email="paws@example.com", business="Happy Paws", kind="pet_grooming")
    # each MCP endpoint sees only its own services
    salon_services = tool_data(mcp_call(client, f"/b/{salon}/mcp", "list_services", {}))
    paws_services = tool_data(mcp_call(client, "/b/happy-paws/mcp", "list_services", {}))
    assert "Color" in [s["name"] for s in salon_services["services"]] and salon_services["staff_noun"] == "stylist"
    assert "Full groom" in [s["name"] for s in paws_services["services"]]
    booked = tool_data(mcp_call(client, "/b/happy-paws/mcp", "book_appointment", {
        "customer_name": "Lucy", "customer_phone": "415 555 0123", "service": "full groom", "date": "Wednesday", "time": "10 AM"}))
    assert booked["ok"], booked
    assert booked["speech"] == "You're all set, Lucy! A full groom on Wednesday, October 21 at 10 AM with Rosa."
    no_phone = tool_data(mcp_call(client, "/b/happy-paws/mcp", "book_appointment", {
        "customer_name": "Lucy", "customer_phone": "", "service": "nail trim", "date": "Wednesday", "time": "11 AM"}))
    assert not no_phone["ok"] and "phone number" in no_phone["speech"]
    salon_week = client.get(f"/api/b/{salon}/week?start=2026-10-21&days=1")
    assert salon_week.status_code == 401  # signed in as the pet groomer
    paws_week = client.get("/api/b/happy-paws/week?start=2026-10-21&days=1").json()
    assert [a["customer"] for a in paws_week["days"][0]["appointments"]] == ["Lucy"]
    assert mcp_call(client, "/b/nobody-here/mcp", "list_services", {}).status_code == 404


def test_owner_tools_need_the_api_key(client):
    slug = signup(client)
    path = f"/b/{slug}/mcp"
    assert tool_data(mcp_call(client, path, "daily_summary", {}))["speech"] == "Sorry, only the owner can do that."
    assert mcp_call(client, path, "daily_summary", {}, key="fd_wrong").status_code == 401
    key = client.post(f"/api/b/{slug}/api-key", headers=JSON, json={}).json()["key"]
    summary = tool_data(mcp_call(client, path, "daily_summary", {}, key=key))
    assert summary["ok"] and summary["speech"] == "We're closed today."  # the salon is closed on Mondays
    assert client.get(f"/api/b/{slug}/setup").json()["has_api_key"]
    # a new key replaces the old one
    client.post(f"/api/b/{slug}/api-key", headers=JSON, json={})
    assert mcp_call(client, path, "daily_summary", {}, key=key).status_code == 401


def test_public_phone_line_and_demo(client):
    slug = signup(client)
    info = client.get(f"/api/b/{slug}/public").json()
    assert info["name"] == "Glow Studio" and info["is_owner"] and info["demo_callers"] == []
    r = client.post(f"/api/b/{slug}/conversation", headers=JSON,
                    json={"text": "A blowout tomorrow at 11, I'm Kate", "caller_phone": "(212) 555-0101"})
    data = r.json()
    assert r.status_code == 200 and data["calls"][-1]["tool"] == "book_appointment" and data["calls"][-1]["ok"], data
    # the demo barbershop keeps working at the old addresses, with sample caller IDs
    demo = client.get(f"/api/b/{tenancy.DEMO_SLUG}/public").json()
    assert demo["name"] == "Main Street Barbers" and demo["demo_callers"]
    assert client.get("/api/week?days=1").status_code == 200
    for page in ("/", "/login", "/signup", "/app", "/demo", "/simulator", "/dashboard", f"/b/{slug}", f"/b/{slug}/board"):
        assert client.get(page).status_code == 200, page
    assert client.get("/b/nobody-here").status_code == 404


def test_try_the_demo(client):
    r = client.post("/api/auth/demo", headers=JSON, json={})
    assert r.json()["business"] == tenancy.DEMO_SLUG
    setup = client.get(f"/api/b/{tenancy.DEMO_SLUG}/setup").json()
    assert setup["name"] == "Main Street Barbers" and setup["public_demo"]
    # the shared demo account can't spin up businesses
    r = client.post("/api/businesses", headers=JSON, json={"business_name": "X", "business_type": "spa", "timezone": "UTC"})
    assert r.status_code == 403
