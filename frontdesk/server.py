"""Front Desk MCP server: a voice receptionist for small businesses.

Transport: Streamable HTTP at POST /mcp (MCP spec 2025-11-25 or later).
Every tool returns a "speech" field with the short sentence the voice assistant should say,
plus structured data for anything that wants to show it on screen.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations

from frontdesk import agent, db, rehearsal, schedule
from frontdesk.schedule import BookingError

mcp = MCPServer(
    name="front-desk",
    title="Front Desk",
    version="0.3.0",
    instructions=(
        "You are the receptionist of a small business (a barbershop). Use the tools to look up "
        "services and open slots, and to book, cancel and reschedule appointments. Always confirm the "
        "customer's name before booking. Reply with the sentence in the 'speech' field, short and natural for voice."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


def _conn():
    conn = db.connect()
    db.init_db(conn)
    if os.environ.get("FRONTDESK_DEMO", "1") != "0":
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


@mcp.tool(title="List services and prices", annotations=READ_ONLY)
def list_services() -> dict[str, Any]:
    """Lists the services the business offers, with duration and price."""
    with _conn() as conn:
        services = db.list_services(conn)
        business = db.business_name(conn)
    items = [f"a {s['name'].lower()} for {schedule.dollars(s['price_cents'])}" for s in services]
    return {
        "ok": True,
        "speech": f"At {business} we offer {schedule.join_and(items)}.",
        "services": services,
    }


@mcp.tool(title="Check open slots", annotations=READ_ONLY)
def check_availability(
    date: Annotated[str, Field(description="Requested day: 2026-10-23, October 23, today, tomorrow or a weekday (e.g. Friday).")],
    service: Annotated[str, Field(description="Service name, e.g. haircut, beard trim, haircut and beard.")],
    period: Annotated[str | None, Field(description="Optional: morning, afternoon or evening.")] = None,
    barber: Annotated[str | None, Field(description="Optional: name of the preferred barber.")] = None,
) -> dict[str, Any]:
    """Shows the open start times for a service on a day, optionally by period and barber."""
    moment = schedule.now()
    try:
        with _conn() as conn:
            d = schedule.parse_date(date, moment.date())
            s = schedule.find_service(conn, service)
            b = schedule.find_staff(conn, barber)
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
            "speech": f"Sorry, there's nothing open for a {s.name.lower()} {when}{part}{with_whom}.",
            "date": d.isoformat(), "service": s.name, "slots": [],
        }
    times: list[str] = []
    for item in slots:
        if item["time"] not in times:
            times.append(item["time"])
    spoken = [schedule.say_time(datetime.strptime(t, "%H:%M").time()) for t in times[:3]]
    return {
        "ok": True,
        "speech": f"For a {s.name.lower()} {when}{part}{with_whom}, I have {schedule.join_or(spoken)}. Which works best?",
        "date": d.isoformat(),
        "service": s.name,
        "slots": slots,
    }


@mcp.tool(
    title="Book an appointment",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False),
)
def book_appointment(
    customer_name: Annotated[str, Field(description="Customer's name.")],
    service: Annotated[str, Field(description="Service name.")],
    date: Annotated[str, Field(description="Day: 2026-10-23, October 23, today, tomorrow or a weekday.")],
    time: Annotated[str, Field(description="Start time, e.g. 9 AM, 9:30, 2:30 PM.")],
    barber: Annotated[str | None, Field(description="Optional: requested barber.")] = None,
) -> dict[str, Any]:
    """Books an appointment. Checks opening hours and that the slot is free before saving."""
    moment = schedule.now()
    try:
        with _conn() as conn:
            d = schedule.parse_date(date, moment.date())
            h = schedule.parse_time(time)
            s = schedule.find_service(conn, service)
            b = schedule.find_staff(conn, barber)
            appt = schedule.book(conn, customer_name, s, datetime.combine(d, h), moment, b)
    except BookingError as e:
        return _error(e)
    first = appt["customer"].split()[0]
    return {
        "ok": True,
        "speech": f"You're all set, {first}! A {appt['service'].lower()} {_when(appt, moment.date())}.",
        "appointment": appt,
    }


@mcp.tool(
    title="Cancel an appointment",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False),
)
def cancel_appointment(
    customer_name: Annotated[str, Field(description="Customer's name.")],
    date: Annotated[str | None, Field(description="Optional: day of the appointment, if the customer has more than one.")] = None,
) -> dict[str, Any]:
    """Cancels the customer's next appointment (or the one on the given day)."""
    moment = schedule.now()
    try:
        with _conn() as conn:
            d = schedule.parse_date(date, moment.date()) if date else None
            found = schedule.find_appointments(conn, customer_name, moment, d)
            if not found:
                return {"ok": False, "speech": f"I couldn't find an appointment for {customer_name}."}
            if len(found) > 1 and d is None:
                options = "; ".join(_describe(a, moment.date()) for a in found[:3])
                return {"ok": False, "speech": f"I found more than one: {options}. Which one should I cancel?", "appointments": found}
            appt = schedule.cancel(conn, found[0]["id"])
    except BookingError as e:
        return _error(e)
    return {"ok": True, "speech": f"Done. I cancelled {_describe(appt, moment.date())}.", "appointment": appt}


@mcp.tool(
    title="Reschedule an appointment",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False),
)
def reschedule_appointment(
    customer_name: Annotated[str, Field(description="Customer's name.")],
    new_time: Annotated[str, Field(description="New time, e.g. 4 PM.")],
    new_date: Annotated[str | None, Field(description="Optional: new day. Default: same day as the current appointment.")] = None,
    current_date: Annotated[str | None, Field(description="Optional: day of the current appointment, if there is more than one.")] = None,
) -> dict[str, Any]:
    """Moves an appointment to another day or time, keeping the same barber when possible."""
    moment = schedule.now()
    try:
        with _conn() as conn:
            d_current = schedule.parse_date(current_date, moment.date()) if current_date else None
            found = schedule.find_appointments(conn, customer_name, moment, d_current)
            if not found:
                return {"ok": False, "speech": f"I couldn't find an appointment for {customer_name}."}
            if len(found) > 1 and d_current is None:
                options = "; ".join(_describe(a, moment.date()) for a in found[:3])
                return {"ok": False, "speech": f"I found more than one: {options}. Which one should I move?", "appointments": found}
            current_day = datetime.fromisoformat(found[0]["start"]).date()
            day = schedule.parse_date(new_date, moment.date()) if new_date else current_day
            new_start = datetime.combine(day, schedule.parse_time(new_time))
            appt = schedule.reschedule(conn, found[0]["id"], new_start, moment)
    except BookingError as e:
        return _error(e)
    return {"ok": True, "speech": f"Done! Your appointment is now {_when(appt, moment.date())}.", "appointment": appt}


@mcp.tool(title="Owner's daily summary", annotations=READ_ONLY)
def daily_summary(
    date: Annotated[str | None, Field(description="Optional: day of the summary. Default: today.")] = None,
) -> dict[str, Any]:
    """Summary for the owner: number of customers, next appointment, expected revenue and free time."""
    moment = schedule.now()
    try:
        with _conn() as conn:
            d = schedule.parse_date(date, moment.date()) if date else moment.date()
            r = schedule.daily_summary(conn, d, moment)
    except BookingError as e:
        return _error(e)
    when = schedule.say_date(d, moment.date())
    if not r["open"]:
        return {"ok": True, "speech": f"The shop is closed {schedule.say_date_on(d, moment.date())}.", **r}
    if not r["total_customers"]:
        return {"ok": True, "speech": f"{when.capitalize()} is wide open, with no appointments yet.", **r}
    n = r["total_customers"]
    count = "1 customer" if n == 1 else f"{n} customers"
    parts = [f"{when.capitalize()} you have {count}, with {schedule.dollars(r['expected_revenue_cents'])} expected."]
    if r["next"]:
        parts.append(f"Next up is {_describe(r['next'], moment.date())}.")
    free = r["free_minutes_by_staff"]
    if free:
        most_free = max(free, key=free.get)
        hours = free[most_free] // 60
        parts.append(f"{most_free} has the most free time, about {hours} hour{'s' if hours != 1 else ''}.")
    return {"ok": True, "speech": " ".join(parts), **r}


# ---------------------------------------------------------------- HTTP routes

@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "front-desk"})


@mcp.custom_route("/api/schedule", methods=["GET"])
async def api_schedule(request: Request) -> JSONResponse:
    """One day of appointments as JSON. Optional parameter: ?date=YYYY-MM-DD."""
    moment = schedule.now()
    try:
        d = schedule.parse_date(request.query_params.get("date", "today"), moment.date())
    except BookingError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    with _conn() as conn:
        items = schedule.appointments_on(conn, d)
        business = db.business_name(conn)
    return JSONResponse({"business": business, "date": d.isoformat(), "appointments": items})


@mcp.custom_route("/api/week", methods=["GET"])
async def api_week(request: Request) -> JSONResponse:
    """The next days of the schedule for the live dashboard. Optional parameters: ?start=today&days=7."""
    moment = schedule.now()
    try:
        start = schedule.parse_date(request.query_params.get("start", "today"), moment.date())
        days = max(1, min(14, int(request.query_params.get("days", "7"))))
    except (BookingError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    with _conn() as conn:
        business = db.business_name(conn)
        staff = [p.name for p in schedule.all_staff(conn)]
        listing = []
        for i in range(days):
            d = start + timedelta(days=i)
            window = schedule.hours_for(conn, d)
            listing.append({
                "date": d.isoformat(),
                "weekday": schedule.WEEKDAY_NAMES[d.weekday()],
                "open": window is not None,
                "opens": window[0].strftime("%H:%M") if window else None,
                "closes": window[1].strftime("%H:%M") if window else None,
                "appointments": schedule.appointments_on(conn, d),
            })
    return JSONResponse({
        "business": business,
        "now": moment.isoformat(timespec="minutes"),
        "staff": staff,
        "days": listing,
    })


WEB = Path(__file__).resolve().parent / "web"
log = logging.getLogger("frontdesk")

RATE_PER_IP = int(os.environ.get("FRONTDESK_RATE_PER_IP", "40"))  # utterances per IP every 10 minutes
DAILY_LIMIT = int(os.environ.get("FRONTDESK_DAILY_LIMIT", "600"))  # utterances per day for the whole server
_utterances_by_ip: dict[str, list[float]] = {}
_utterances_today: dict[str, int] = {}
_bedrock_down: dict = {"until": 0.0, "notice": ""}  # in auto mode, skip Bedrock for a while after a failure


def _within_limits(request: Request) -> bool:
    """Protects the AWS credits on a public endpoint: per-IP rate limit and a daily cap."""
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")
    t = time.time()
    recent = [x for x in _utterances_by_ip.get(ip, []) if t - x < 600]
    day = date.today().isoformat()
    if len(recent) >= RATE_PER_IP or _utterances_today.get(day, 0) >= DAILY_LIMIT:
        _utterances_by_ip[ip] = recent
        return False
    recent.append(t)
    _utterances_by_ip[ip] = recent
    if day not in _utterances_today:  # new day: reset the count
        _utterances_today.clear()
    _utterances_today[day] = _utterances_today.get(day, 0) + 1
    return True


def agent_mode() -> str:
    return os.environ.get("FRONTDESK_AGENT", "bedrock").strip().lower()


@mcp.custom_route("/api/conversation", methods=["POST"])
async def api_conversation(request: Request) -> JSONResponse:
    """One customer utterance for the agent on Bedrock. Body: {"text": "...", "session": "optional"}."""
    if not _within_limits(request):
        return JSONResponse({"error": "Too many requests in a short time. Please wait a few minutes and try again."}, status_code=429)
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "Invalid JSON body."}, status_code=400)
    text = str(body.get("text", "")).strip()
    if not text:
        return JSONResponse({"error": "Send the customer's words in the 'text' field."}, status_code=400)
    if len(text) > 500:
        return JSONResponse({"error": "That message is too long."}, status_code=400)
    with _conn() as conn:
        business = db.business_name(conn)
    mode = agent_mode()
    session = body.get("session") or None
    try:
        if mode == "rehearsal":
            result = await rehearsal.respond(text, session, business=business)
        elif mode == "auto" and time.time() < _bedrock_down["until"]:
            # Bedrock failed recently: don't retry on every utterance, stay in rehearsal mode for a few minutes
            result = await rehearsal.respond(text, session, business=business)
            result["notice"] = _bedrock_down["notice"]
        else:
            try:
                result = await agent.respond(text, session, business=business)
            except agent.AgentError as e:
                if mode != "auto":
                    raise
                # auto mode (used by the deployment): if Bedrock fails, the demo keeps going in rehearsal mode
                log.warning("Bedrock unavailable, using rehearsal mode: %s", e)
                _bedrock_down.update(until=time.time() + 300, notice=f"Bedrock is unavailable right now, answering in rehearsal mode. Reason: {e}")
                result = await rehearsal.respond(text, session, business=business)
                result["notice"] = _bedrock_down["notice"]
    except agent.AgentError as e:  # expired credentials, no model access, network: show it instead of hanging
        log.warning("Agent failure: %s", e, exc_info=e.__cause__)
        return JSONResponse({"error": str(e)}, status_code=502)
    return JSONResponse(result)


@mcp.custom_route("/api/conversation/{session}", methods=["DELETE"])
async def api_end_conversation(request: Request) -> JSONResponse:
    agent.end_session(request.path_params["session"])
    rehearsal.end_session(request.path_params["session"])
    return JSONResponse({"ok": True})


def _page(name: str):
    async def route(_: Request) -> FileResponse:
        return FileResponse(WEB / name, media_type="text/html; charset=utf-8")
    return route


def _redirect(target: str):
    async def route(_: Request) -> RedirectResponse:
        return RedirectResponse(target)
    return route


mcp.custom_route("/simulator", methods=["GET"])(_page("simulator.html"))
mcp.custom_route("/dashboard", methods=["GET"])(_page("dashboard.html"))
mcp.custom_route("/demo", methods=["GET"])(_page("demo.html"))
mcp.custom_route("/", methods=["GET"])(_redirect("/demo"))
# old Portuguese paths from the first version, kept so earlier links still work
mcp.custom_route("/simulador", methods=["GET"])(_redirect("/simulator"))
mcp.custom_route("/painel", methods=["GET"])(_redirect("/dashboard"))


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
    return mcp.streamable_http_app(stateless_http=True, json_response=True, transport_security=_transport_security())


def main() -> None:
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app(), host=host, port=port)


if __name__ == "__main__":
    main()
