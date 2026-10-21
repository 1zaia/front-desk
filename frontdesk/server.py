"""Front Desk MCP server: a voice receptionist for any appointment-based small business.

Transport: Streamable HTTP (MCP spec 2025-11-25 or later).
- POST /b/<business>/mcp  one business (e.g. /b/main-street-barbers/mcp)
- POST /mcp               the public demo business
Customer tools are open, like a phone line. Owner tools (daily_summary, block_time) need the business's
API key as "Authorization: Bearer <key>" (created in the owner app), except on the public demo.

Every tool returns a "speech" field with the short sentence the voice assistant should say,
plus structured data for anything that wants to show it on screen.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import Field

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations

from frontdesk import db, schedule, tenancy
from frontdesk.schedule import BookingError

mcp = MCPServer(
    name="front-desk",
    title="Front Desk",
    version="0.5.0",
    instructions=(
        "You are the receptionist of a small appointment-based business (a barbershop, salon, clinic, studio...). "
        "Use the tools to look up services and open slots, to book, cancel and reschedule appointments, to put "
        "customers on the waitlist and to recognize returning customers by phone number. Every booking needs the "
        "customer's name and phone number. The owner can also block time and ask for the daily summary. "
        "Reply with the sentence in the 'speech' field, short and natural for voice."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
CHANGES = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)
FIRST_AVAILABLE = {"first available", "first", "earliest", "asap", "soonest", "next available", "any time", "anytime",
                   "whenever", "first open", "first opening", "the earliest"}
OWNER_ONLY = {"ok": False, "speech": "Sorry, only the owner can do that."}


def _conn():
    conn = db.connect()
    db.init_db(conn)
    if tenancy.current().public_demo and os.environ.get("FRONTDESK_DEMO", "1") != "0":
        schedule.seed_demo(conn, schedule.now().date())
    return conn


def _error(e: BookingError) -> dict:
    return {"ok": False, "speech": str(e)}


def _when(a: dict, today: date) -> str:
    start = datetime.fromisoformat(a["start"])
    return f"{schedule.say_date_on(start.date(), today)} at {schedule.say_time(start.time())} with {a['staff']}"


def _describe(a: dict, today: date) -> str:
    first_name = a["customer"].split()[0]
    return f"{first_name}'s {a['service'].lower()} {_when(a, today)}"


def _hhmm(iso: str) -> str:
    return schedule.say_time(datetime.fromisoformat(iso).time())


def _first_available(text: str | None) -> bool:
    return schedule.normalize(text or "").strip(" .!") in FIRST_AVAILABLE


def _who(name: str | None, phone: str | None) -> str:
    return (name or "").strip() or schedule.say_phone(schedule.normalize_phone(phone))


@mcp.tool(title="List services and prices", annotations=READ_ONLY)
def list_services() -> dict[str, Any]:
    """Lists the services the business offers (duration and price) and who works there."""
    with _conn() as conn:
        services = db.list_services(conn)
        business = db.business_name(conn)
        team = [p.name for p in schedule.all_staff(conn)]
        words = schedule.staff_words(conn)
    items = [f"{schedule.a_service(s['name'])} for {schedule.dollars(s['price_cents'])}" if s["price_cents"]
             else f"{schedule.a_service(s['name'])} (free)" for s in services]
    return {
        "ok": True,
        "speech": f"At {business} we offer {schedule.join_and(items)}.",
        "services": services,
        "team": team,
        "staff_noun": words["staff"],
    }


@mcp.tool(title="Check open slots", annotations=READ_ONLY)
def check_availability(
    date: Annotated[str, Field(description="Requested day: 2026-10-23, October 23, today, tomorrow or a weekday (e.g. Friday).")],
    service: Annotated[str, Field(description="Service name, as listed by list_services.")],
    period: Annotated[str | None, Field(description="Optional: morning, afternoon or evening.")] = None,
    staff: Annotated[str | None, Field(description="Optional: name of the preferred staff member (barber, stylist, therapist...).")] = None,
) -> dict[str, Any]:
    """Shows the first open start times for a service on a day, optionally by period and staff member."""
    moment = schedule.now()
    try:
        with _conn() as conn:
            d = schedule.parse_date(date, moment.date())
            s = schedule.find_service(conn, service)
            b = schedule.find_staff(conn, staff)
            is_open = schedule.hours_for(conn, d) is not None
            slots = schedule.open_slots(conn, d, s, moment, period, b) if is_open else []
    except BookingError as e:
        return _error(e)
    when = schedule.say_date_on(d, moment.date())
    if not is_open:
        return {"ok": True, "speech": f"Sorry, we're closed {when}.", "date": d.isoformat(), "open": False, "slots": []}
    part = ""
    if period:
        p = schedule.PERIOD_ALIASES.get(schedule.normalize(period), schedule.normalize(period))
        if when == "today":
            when, part = ("tonight" if p == "evening" else f"this {p}"), ""
        elif when == "tomorrow":
            part = f" {p}"
        else:
            part = f" in the {p}"
    with_whom = f" with {b.name}" if b else ""
    if not slots:
        return {
            "ok": True,
            "speech": f"Sorry, there's nothing open for {schedule.a_service(s.name)} {when}{part}{with_whom}. Want me to put you on the waitlist?",
            "date": d.isoformat(), "service": s.name, "slots": [],
        }
    times: list[str] = []
    for item in slots:
        if item["time"] not in times:
            times.append(item["time"])
    spoken = [schedule.say_time(datetime.strptime(t, "%H:%M").time()) for t in times[:3]]
    return {
        "ok": True,
        "speech": f"For {schedule.a_service(s.name)} {when}{part}{with_whom}, I have {schedule.join_or(spoken)}. Which works best?",
        "date": d.isoformat(),
        "service": s.name,
        "slots": slots,
    }


@mcp.tool(title="Book an appointment", annotations=WRITES)
def book_appointment(
    customer_name: Annotated[str, Field(description="Customer's name. Always required.")],
    customer_phone: Annotated[str, Field(description="Customer's phone number (caller ID or as they said it). Always required.")],
    service: Annotated[str, Field(description="Service name, or \"the usual\" for a returning customer.")],
    date: Annotated[str, Field(description="Day: 2026-10-23, October 23, today, tomorrow or a weekday.")],
    time: Annotated[str, Field(description="Start time, e.g. 9 AM, 9:30, 2:30 PM, or \"first available\".")],
    staff: Annotated[str | None, Field(description="Optional: requested staff member.")] = None,
    period: Annotated[str | None, Field(description="Optional, with \"first available\": morning, afternoon or evening.")] = None,
    notes: Annotated[str | None, Field(description="Optional: anything the business should know.")] = None,
) -> dict[str, Any]:
    """Books an appointment after checking hours and that the slot is free. Needs the customer's name and phone.

    For a returning customer, service can be "the usual": it books their usual service with their usual staff member.
    time can be "first available" to take the earliest open slot that day (optionally in a period).
    """
    moment = schedule.now()
    if not (customer_name or "").strip():
        return {"ok": False, "speech": "Can I get your name for the booking?"}
    if not schedule.normalize_phone(customer_phone):
        return {"ok": False, "speech": "And what's the best phone number for the booking?"}
    usual = schedule.is_usual(service)
    try:
        with _conn() as conn:
            d = schedule.parse_date(date, moment.date())
            b = schedule.find_staff(conn, staff)
            profile = schedule.customer_profile(conn, customer_name, moment, phone=customer_phone)
            preferred = None  # their usual staff member, used when they didn't name one
            if usual:
                if not profile or not profile["usual_service"]:
                    return {"ok": False, "speech": f"I don't see a previous visit for {customer_name}. Which service would you like?"}
                s = schedule.find_service(conn, profile["usual_service"])
            else:
                s = schedule.find_service(conn, service)
                # a returning customer asking for their usual service by name ("a beard trim") is also "the usual"
                usual = bool(profile and profile["visits"] and profile["usual_service"] == s.name)
            if usual:
                if profile["customer"] and schedule.same_person(customer_name, profile["customer"]):
                    customer_name = profile["customer"]
                if not b:
                    preferred = schedule.find_staff(conn, profile["usual_staff"])
            if _first_available(time):
                slots = schedule.open_slots(conn, d, s, moment, period, b)
                if preferred and any(x["staff"] == preferred.name for x in slots):
                    slots = [x for x in slots if x["staff"] == preferred.name]
                if not slots:
                    when = schedule.say_date_on(d, moment.date())
                    return {"ok": False, "speech": f"Sorry, there's nothing open for {schedule.a_service(s.name)} {when}. Want me to put you on the waitlist?"}
                start = datetime.combine(d, datetime.strptime(slots[0]["time"], "%H:%M").time())
                b = b or schedule.find_staff(conn, slots[0]["staff"])
            else:
                start = datetime.combine(d, schedule.parse_time(time))
            try:
                appt = schedule.book(conn, customer_name, s, start, moment, b or preferred, phone=customer_phone, notes=notes or "")
            except BookingError:
                if not preferred:
                    raise
                appt = schedule.book(conn, customer_name, s, start, moment, None, phone=customer_phone, notes=notes or "")
    except BookingError as e:
        if "already taken" in str(e):
            return {"ok": False, "speech": f"{e} I can check other times, or put you on the waitlist."}
        return _error(e)
    first = appt["customer"].split()[0]
    what = f"your usual {appt['service'].lower()}" if usual else schedule.a_service(appt["service"])
    return {
        "ok": True,
        "speech": f"You're all set, {first}! {what[0].upper()}{what[1:]} {_when(appt, moment.date())}.",
        "appointment": appt,
    }


def _one_appointment(conn, name: str | None, phone: str | None, moment: datetime, d: date | None, verb: str):
    found = schedule.find_appointments(conn, name, moment, d, phone=phone)
    if not found and phone and name:  # booked under another number: try the name
        found = schedule.find_appointments(conn, name, moment, d)
    if not found:
        return None, {"ok": False, "speech": f"I couldn't find an upcoming appointment for {_who(name, phone)}."}
    if len(found) > 1 and d is None:
        options = "; ".join(_describe(a, moment.date()) for a in found[:3])
        return None, {"ok": False, "speech": f"I found more than one: {options}. Which one should I {verb}?", "appointments": found}
    return found[0], None


@mcp.tool(title="Cancel an appointment", annotations=CHANGES)
def cancel_appointment(
    customer_phone: Annotated[str | None, Field(description="Customer's phone number (preferred way to find the booking).")] = None,
    customer_name: Annotated[str | None, Field(description="Customer's name.")] = None,
    date: Annotated[str | None, Field(description="Optional: day of the appointment, if the customer has more than one.")] = None,
) -> dict[str, Any]:
    """Cancels the customer's next appointment (or the one on the given day). If someone is on the waitlist, the slot goes to them."""
    moment = schedule.now()
    if not (customer_name or customer_phone):
        return {"ok": False, "speech": "Sure. What name or phone number is the appointment under?"}
    try:
        with _conn() as conn:
            d = schedule.parse_date(date, moment.date()) if date else None
            found, problem = _one_appointment(conn, customer_name, customer_phone, moment, d, "cancel")
            if problem:
                return problem
            appt = schedule.cancel(conn, found["id"])
            freed = datetime.fromisoformat(appt["start"])
            filled = schedule.fill_from_waitlist(conn, freed.date(), moment, freed)
    except BookingError as e:
        return _error(e)
    # the slot may go straight to someone on the waitlist; the caller isn't told who (not even in the data the
    # voice agent reads), the owner sees it on the calendar and the live board
    return {"ok": True, "speech": f"Done. I cancelled {_describe(appt, moment.date())}.", "appointment": appt,
            "waitlist_filled": len(filled)}


@mcp.tool(title="Reschedule an appointment", annotations=CHANGES)
def reschedule_appointment(
    new_time: Annotated[str, Field(description="New time, e.g. 4 PM.")],
    customer_phone: Annotated[str | None, Field(description="Customer's phone number (preferred way to find the booking).")] = None,
    customer_name: Annotated[str | None, Field(description="Customer's name.")] = None,
    new_date: Annotated[str | None, Field(description="Optional: new day. Default: same day as the current appointment.")] = None,
    current_date: Annotated[str | None, Field(description="Optional: day of the current appointment, if there is more than one.")] = None,
) -> dict[str, Any]:
    """Moves an appointment to another day or time, keeping the same staff member when possible."""
    moment = schedule.now()
    if not (customer_name or customer_phone):
        return {"ok": False, "speech": "Sure. What name or phone number is the appointment under?"}
    try:
        with _conn() as conn:
            d_current = schedule.parse_date(current_date, moment.date()) if current_date else None
            found, problem = _one_appointment(conn, customer_name, customer_phone, moment, d_current, "move")
            if problem:
                return problem
            old_start = datetime.fromisoformat(found["start"])
            day = schedule.parse_date(new_date, moment.date()) if new_date else old_start.date()
            new_start = datetime.combine(day, schedule.parse_time(new_time))
            appt = schedule.reschedule(conn, found["id"], new_start, moment)
            filled = schedule.fill_from_waitlist(conn, old_start.date(), moment, old_start)
    except BookingError as e:
        return _error(e)
    return {"ok": True, "speech": f"Done! Your appointment is now {_when(appt, moment.date())}.", "appointment": appt,
            "waitlist_filled": len(filled)}


@mcp.tool(title="Join the waitlist", annotations=WRITES)
def join_waitlist(
    customer_name: Annotated[str, Field(description="Customer's name.")],
    customer_phone: Annotated[str, Field(description="Customer's phone number, to reach them when a spot opens.")],
    service: Annotated[str, Field(description="Service name.")],
    date: Annotated[str, Field(description="Day the customer wants.")],
    time: Annotated[str | None, Field(description="Optional: the exact start time they want, e.g. 10 AM.")] = None,
    period: Annotated[str | None, Field(description="Optional: morning, afternoon or evening. Default: any time that day.")] = None,
    staff: Annotated[str | None, Field(description="Optional: preferred staff member.")] = None,
) -> dict[str, Any]:
    """Puts the customer on the waitlist when the time they want is full. If someone cancels, the slot is booked for them automatically."""
    moment = schedule.now()
    if not schedule.normalize_phone(customer_phone):
        return {"ok": False, "speech": "Sure. What's the best phone number to reach you if a spot opens up?"}
    try:
        with _conn() as conn:
            d = schedule.parse_date(date, moment.date())
            s = schedule.find_service(conn, service)
            b = schedule.find_staff(conn, staff)
            at = schedule.parse_time(time) if time and not _first_available(time) else None
            entry = schedule.add_to_waitlist(conn, customer_name, s, d, moment, at=at, period=period, staff=b, phone=customer_phone)
    except BookingError as e:
        return _error(e)
    when = schedule.say_date_on(d, moment.date())
    if at:
        window = f" at {schedule.say_time(at)}"
    elif period:
        window = f" in the {schedule.PERIOD_ALIASES.get(schedule.normalize(period), schedule.normalize(period))}"
    else:
        window = ""
    first = entry["customer"].split()[0]
    return {
        "ok": True,
        "speech": f"Got it, {first}. You're on the waitlist for {schedule.a_service(entry['service'])} {when}{window}. "
                  "If a spot opens up, I'll book it for you automatically.",
        "waitlist_entry": entry,
    }


@mcp.tool(title="Look up a returning customer", annotations=READ_ONLY)
def customer_history(
    customer_phone: Annotated[str | None, Field(description="Customer's phone number (caller ID). Preferred.")] = None,
    customer_name: Annotated[str | None, Field(description="Customer's name, as they said it.")] = None,
) -> dict[str, Any]:
    """Finds a returning customer by phone number (or name): visits, usual service and staff member, last visit and upcoming appointments."""
    moment = schedule.now()
    with _conn() as conn:
        profile = schedule.customer_profile(conn, customer_name, moment, phone=customer_phone)
    if not profile or not profile["visits"]:
        if profile and profile["upcoming"]:
            nxt = profile["upcoming"][0]
            return {"ok": True, "speech": f"Hi {profile['customer'].split()[0]}! You're booked {_when(nxt, moment.date())}.", "profile": profile}
        greeting = f"Welcome, {customer_name.split()[0].title()}!" if (customer_name or "").strip() else "Welcome!"
        return {"ok": True, "speech": f"{greeting} Looks like it's your first visit with us.", "profile": profile}
    first = profile["customer"].split()[0]
    last = datetime.fromisoformat(profile["last_visit"]).date()
    speech = (f"Welcome back, {first}! Last time was {schedule.a_service(profile['usual_service'])} with {profile['usual_staff']} "
              f"on {schedule.MONTH_NAMES[last.month - 1]} {last.day}.")
    if profile["upcoming"]:
        speech += f" You're also booked {_when(profile['upcoming'][0], moment.date())}."
    else:
        speech += " Want the usual?"
    return {"ok": True, "speech": speech, "profile": profile}


@mcp.tool(title="Owner's daily summary", annotations=READ_ONLY)
def daily_summary(
    date: Annotated[str | None, Field(description="Optional: day of the summary. Default: today.")] = None,
) -> dict[str, Any]:
    """For the owner only: number of customers, next appointment, expected revenue, waitlist and free time."""
    if not tenancy.is_owner():
        return OWNER_ONLY
    moment = schedule.now()
    try:
        with _conn() as conn:
            d = schedule.parse_date(date, moment.date()) if date else moment.date()
            r = schedule.daily_summary(conn, d, moment)
            noun = schedule.staff_words(conn)["customer"]
    except BookingError as e:
        return _error(e)
    when = schedule.say_date(d, moment.date())
    if not r["open"]:
        return {"ok": True, "speech": f"We're closed {schedule.say_date_on(d, moment.date())}.", **r}
    if not r["total_customers"]:
        return {"ok": True, "speech": f"{when.capitalize()} is wide open, with no appointments yet.", **r}
    n = r["total_customers"]
    count = f"1 {noun}" if n == 1 else f"{n} {noun}s"
    parts = [f"{when.capitalize()} you have {count}, with {schedule.dollars(r['expected_revenue_cents'])} expected."]
    if r["next"]:
        parts.append(f"Next up is {_describe(r['next'], moment.date())}.")
    waiting = [w for w in r["waitlist"] if w["status"] == "waiting"]
    filled = [w for w in r["waitlist"] if w["status"] == "booked"]
    if filled:
        parts.append(f"The waitlist already filled {len(filled)} cancelled slot{'s' if len(filled) != 1 else ''}.")
    if waiting:
        parts.append(f"{len(waiting)} {'person is' if len(waiting) == 1 else 'people are'} still on the waitlist.")
    for block in r["blocks"]:
        parts.append(f"{block['staff']} is blocked from {_hhmm(block['start'])} to {_hhmm(block['end'])}.")
    free = r["free_minutes_by_staff"]
    if free and not r["blocks"]:
        most_free = max(free, key=free.get)
        hours = free[most_free] // 60
        parts.append(f"{most_free} has the most free time, about {hours} hour{'s' if hours != 1 else ''}.")
    return {"ok": True, "speech": " ".join(parts), **r}


@mcp.tool(title="Block time off someone's schedule", annotations=WRITES)
def block_time(
    staff: Annotated[str, Field(description="Staff member whose time is blocked.")],
    date: Annotated[str, Field(description="Day to block.")],
    start_time: Annotated[str | None, Field(description="Optional: start of the block, e.g. 1 PM. Default: opening time.")] = None,
    end_time: Annotated[str | None, Field(description="Optional: end of the block, e.g. 3 PM. Default: closing time.")] = None,
    period: Annotated[str | None, Field(description="Optional: morning, afternoon or evening, instead of start and end times.")] = None,
    reason: Annotated[str | None, Field(description="Optional: why, e.g. dentist, day off.")] = None,
) -> dict[str, Any]:
    """For the owner only: takes time off a staff member's schedule so nobody can book it. Reports appointments already in that time."""
    if not tenancy.is_owner():
        return OWNER_ONLY
    moment = schedule.now()
    try:
        with _conn() as conn:
            b = schedule.find_staff(conn, staff)
            if b is None:
                return {"ok": False, "speech": "Whose time should I block?"}
            d = schedule.parse_date(date, moment.date())
            start = schedule.parse_time(start_time) if start_time else None
            end = schedule.parse_time(end_time) if end_time else None
            block = schedule.block_time(conn, b, d, moment, start, end, period, reason or "")
    except BookingError as e:
        return _error(e)
    s, e_ = datetime.fromisoformat(block["start"]), datetime.fromisoformat(block["end"])
    speech = (f"Done. {b.name} is blocked {schedule.say_date_on(d, moment.date())} "
              f"from {schedule.say_time(s.time())} to {schedule.say_time(e_.time())}.")
    if block["conflicts"]:
        names = schedule.join_and([f"{a['customer'].split()[0]} at {_hhmm(a['start'])}" for a in block["conflicts"]])
        speech += f" Heads up: {names} {'is' if len(block['conflicts']) == 1 else 'are'} still booked in that time."
    return {"ok": True, "speech": speech, "block": block}


# ---------------------------------------------------------------- HTTP app

from frontdesk import web_api  # noqa: E402  (registers the web routes on this server)

MCP_PATH = re.compile(r"^/b/([a-z0-9-]+)/mcp/?$")


class TenantMiddleware:
    """Picks the business from the URL (/b/<slug>/mcp) and checks the owner's API key, for the MCP endpoint."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope["path"]
            m = MCP_PATH.match(path)
            if m:
                business = tenancy.business_by_slug(m.group(1))
                if business is None:
                    return await _json(send, 404, {"error": "No business with that address."})
                key = _bearer(scope)
                owner = tenancy.api_key_matches(business, key)
                if key and not owner:
                    return await _json(send, 401, {"error": "Invalid API key for this business."})
                tenancy.set_serving(business, owner)
                scope = dict(scope, path="/mcp", raw_path=b"/mcp")
            elif path.rstrip("/") == "/mcp":
                tenancy.set_serving(tenancy.demo_business(), False)
        await self.app(scope, receive, send)


def _bearer(scope) -> str | None:
    for k, v in scope.get("headers", []):
        if k == b"authorization":
            value = v.decode("latin-1")
            if value.lower().startswith("bearer "):
                return value[7:].strip()
    return None


async def _json(send, status: int, body: dict) -> None:
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
    await send({"type": "http.response.body", "body": data})


def _transport_security() -> TransportSecuritySettings | None:
    """The SDK's DNS rebinding protection, also allowing the deployment's public domain.

    Without this, behind a proxy (Caddy, a load balancer) the SDK answers 421 "Invalid Host header",
    because it only accepts localhost. FRONTDESK_HOSTS lists the public domains, comma-separated.
    """
    public = [h.strip() for h in os.environ.get("FRONTDESK_HOSTS", "").split(",") if h.strip()]
    if not public:
        return None  # SDK default: localhost only
    local = ["127.0.0.1", "localhost", "[::1]"]
    hosts = [f"{h}{port}" for h in local + public for port in ("", ":*")]
    origins = [f"http://{h}:*" for h in local] + [f"https://{h}" for h in public]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)


def app():
    """ASGI app (for uvicorn or the deployment)."""
    inner = mcp.streamable_http_app(stateless_http=True, json_response=True, transport_security=_transport_security())
    return TenantMiddleware(inner)


def main() -> None:
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app(), host=host, port=port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    # run the importable module (not __main__), so the web routes registered by web_api land on the same server
    from frontdesk.server import main as run
    run()
