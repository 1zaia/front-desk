"""Web routes: pages, sign-up / sign-in, the owner's management API and the voice conversation API.

Owner routes live under /api/b/<slug>/... and need the owner's session cookie. Mutations only accept
JSON bodies (with SameSite=Lax cookies, a cross-site form can't send them).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response

from frontdesk import agent, business_types, db, manage, rehearsal, schedule, tenancy
from frontdesk.schedule import BookingError
from frontdesk.server import _conn, mcp

WEB = Path(__file__).resolve().parent / "web"
COOKIE = "fd_session"
log = logging.getLogger("frontdesk")

RATE_PER_IP = int(os.environ.get("FRONTDESK_RATE_PER_IP", "40"))  # utterances per IP every 10 minutes
DAILY_LIMIT = int(os.environ.get("FRONTDESK_DAILY_LIMIT", "600"))  # utterances per day for the whole server
_utterances_by_ip: dict[str, list[float]] = {}
_utterances_today: dict[str, int] = {}
_bedrock_down: dict = {"until": 0.0, "notice": ""}  # in auto mode, skip Bedrock for a while after a failure


# ---------------------------------------------------------------- helpers

def _ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")


def _within_limits(request: Request) -> bool:
    """Protects the AWS credits on a public endpoint: per-IP rate limit and a daily cap."""
    ip = _ip(request)
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


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _body(request: Request) -> dict:
    if "application/json" not in request.headers.get("content-type", ""):
        raise BookingError("Send JSON.")
    try:
        data = await request.json()
    except ValueError:
        raise BookingError("Invalid JSON body.") from None
    if not isinstance(data, dict):
        raise BookingError("Invalid JSON body.")
    return data


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _signed_in(response: Response, request: Request, account_id: int) -> Response:
    token = tenancy.start_session(account_id)
    response.set_cookie(COOKIE, token, max_age=tenancy.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=_secure(request), path="/")
    return response


def _account_id(request: Request) -> int | None:
    return tenancy.session_account(request.cookies.get(COOKIE))


def _business_json(b: tenancy.Business) -> dict:
    info = b.info()
    return {**info, "mcp_path": f"/b/{b.slug}/mcp", "phone_page": f"/b/{b.slug}", "board": f"/b/{b.slug}/board"}


def route(path: str, methods: list[str]):
    def wrap(fn):
        mcp.custom_route(path, methods=methods)(fn)
        return fn
    return wrap


def owner_route(path: str, methods: list[str]):
    """Owner-only API under /api/b/{slug}/...: checks the session, serves that business, turns BookingError into 400."""
    def wrap(fn):
        @wraps(fn)
        async def handler(request: Request):
            account_id = _account_id(request)
            if account_id is None:
                return _error("Sign in first.", 401)
            business = tenancy.business_by_slug(request.path_params["slug"])
            if business is None or business.owner_id != account_id:
                return _error("You don't have access to this business.", 403)
            with tenancy.serving(business, owner=True):
                try:
                    with _conn() as conn:
                        return await fn(request, conn)
                except BookingError as e:
                    return _error(str(e))
        mcp.custom_route(f"/api/b/{{slug}}{path}", methods=methods)(handler)
        return handler
    return wrap


# ---------------------------------------------------------------- pages

def _page(name: str):
    async def handler(_: Request) -> FileResponse:
        return FileResponse(WEB / name, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})
    return handler


def _redirect(target: str):
    async def handler(_: Request) -> RedirectResponse:
        return RedirectResponse(target)
    return handler


async def _business_page(request: Request, name: str):
    if tenancy.business_by_slug(request.path_params["slug"]) is None:
        return Response("No business with that address.", status_code=404, media_type="text/plain")
    return FileResponse(WEB / name, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


for _path, _file in {"/": "index.html", "/login": "auth.html", "/signup": "auth.html", "/app": "app.html",
                     "/simulator": "simulator.html", "/dashboard": "dashboard.html", "/demo": "demo.html"}.items():
    mcp.custom_route(_path, methods=["GET"])(_page(_file))
# old Portuguese paths from the first version, kept so earlier links still work
mcp.custom_route("/simulador", methods=["GET"])(_redirect("/simulator"))
mcp.custom_route("/painel", methods=["GET"])(_redirect("/dashboard"))


@route("/b/{slug}", ["GET"])
async def phone_page(request: Request):
    return await _business_page(request, "simulator.html")


@route("/b/{slug}/board", ["GET"])
async def board_page(request: Request):
    return await _business_page(request, "dashboard.html")


@route("/b/{slug}/demo", ["GET"])
async def demo_page(request: Request):
    return await _business_page(request, "demo.html")


@route("/health", ["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "front-desk"})


# ---------------------------------------------------------------- accounts

@route("/api/business-types", ["GET"])
async def api_business_types(_: Request) -> JSONResponse:
    return JSONResponse({"types": business_types.catalog()})


@route("/api/auth/signup", ["POST"])
async def api_signup(request: Request) -> JSONResponse:
    try:
        body = await _body(request)
        account_id = tenancy.create_account(body.get("email", ""), body.get("name", ""), body.get("password", ""))
        business = tenancy.create_business(account_id, body.get("business_name", ""), body.get("business_type", ""),
                                           body.get("timezone", "America/Los_Angeles"))
    except (tenancy.AuthError, BookingError) as e:
        return _error(str(e))
    return _signed_in(JSONResponse({"ok": True, "business": business.slug}), request, account_id)


@route("/api/auth/login", ["POST"])
async def api_login(request: Request) -> JSONResponse:
    try:
        body = await _body(request)
        account_id = tenancy.authenticate(body.get("email", ""), body.get("password", ""))
    except (tenancy.AuthError, BookingError) as e:
        return _error(str(e), 400)
    return _signed_in(JSONResponse({"ok": True}), request, account_id)


@route("/api/auth/demo", ["POST"])
async def api_demo_login(request: Request) -> JSONResponse:
    """'Try the demo': signs in to the demo barbershop (shared, reset on every deploy)."""
    return _signed_in(JSONResponse({"ok": True, "business": tenancy.DEMO_SLUG}), request, tenancy.demo_account_id())


@route("/api/auth/logout", ["POST"])
async def api_logout(request: Request) -> JSONResponse:
    tenancy.end_session(request.cookies.get(COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/")
    return response


@route("/api/me", ["GET"])
async def api_me(request: Request) -> JSONResponse:
    account_id = _account_id(request)
    who = tenancy.account(account_id) if account_id else None
    if not who:
        return _error("Not signed in.", 401)
    return JSONResponse({"account": who, "businesses": [_business_json(b) for b in tenancy.businesses_of(account_id)]})


@route("/api/businesses", ["POST"])
async def api_new_business(request: Request) -> JSONResponse:
    """Another business for the same owner (a second location, a different service)."""
    account_id = _account_id(request)
    if account_id is None:
        return _error("Sign in first.", 401)
    if account_id == tenancy.demo_account_id():
        return _error("The demo account can't create businesses. Create your own account instead.", 403)
    try:
        body = await _body(request)
        business = tenancy.create_business(account_id, body.get("business_name", ""), body.get("business_type", ""),
                                           body.get("timezone", "America/Los_Angeles"))
    except (tenancy.AuthError, BookingError) as e:
        return _error(str(e))
    return JSONResponse({"ok": True, "business": business.slug})


# ---------------------------------------------------------------- public business API (the phone line)

def _public_business(request: Request) -> tenancy.Business | None:
    slug = request.path_params.get("slug")
    return tenancy.business_by_slug(slug) if slug else tenancy.demo_business()


@route("/api/b/{slug}/public", ["GET"])
async def api_public(request: Request) -> JSONResponse:
    business = _public_business(request)
    if business is None:
        return _error("No business with that address.", 404)
    with tenancy.serving(business):
        moment = schedule.now()
        info = business.info()
        callers = []
        if business.public_demo:  # sample caller IDs for the demo phone
            callers = [{"name": n, "phone": p, "display": schedule.format_phone(p)} for n, p in schedule.DEMO_PHONES.items()]
    account_id = _account_id(request)
    return JSONResponse({**info, "now": moment.isoformat(timespec="minutes"), "demo_callers": callers,
                         "is_owner": bool(account_id and account_id == business.owner_id)})


async def _conversation(request: Request, business: tenancy.Business) -> JSONResponse:
    if not _within_limits(request):
        return _error("Too many requests in a short time. Please wait a few minutes and try again.", 429)
    try:
        body = await _body(request)
    except BookingError as e:
        return _error(str(e))
    text = str(body.get("text", "")).strip()
    if not text:
        return _error("Send the customer's words in the 'text' field.")
    if len(text) > 500:
        return _error("That message is too long.")
    account_id = _account_id(request)
    owner = bool(account_id and account_id == business.owner_id)
    with tenancy.serving(business, owner=owner):
        info = business.info()
        caller_phone = schedule.normalize_phone(str(body.get("caller_phone") or ""))
        caller_name = None
        if caller_phone:
            with _conn() as conn:
                known = schedule.customer_by_phone(conn, caller_phone)
            caller_name = known["name"] if known else None
        context = {**info, "caller_phone": caller_phone, "caller_name": caller_name,
                   "who": "either" if business.public_demo else ("owner" if owner else "customer")}
        session = body.get("session") or None
        try:
            result = await _answer(text, session, info["name"], context)
        except agent.AgentError as e:  # expired credentials, no model access, network: show it instead of hanging
            log.warning("Agent failure: %s", e, exc_info=e.__cause__)
            return _error(str(e), 502)
    return JSONResponse(result)


def agent_mode() -> str:
    return os.environ.get("FRONTDESK_AGENT", "bedrock").strip().lower()


async def _answer(text: str, session: str | None, name: str, context: dict) -> dict:
    mode = agent_mode()
    if mode == "rehearsal":
        return await rehearsal.respond(text, session, business=name, context=context)
    if mode == "auto" and time.time() < _bedrock_down["until"]:
        # Bedrock failed recently: don't retry on every utterance, stay in rehearsal mode for a few minutes
        result = await rehearsal.respond(text, session, business=name, context=context)
        result["notice"] = _bedrock_down["notice"]
        return result
    try:
        return await agent.respond(text, session, business=name, context=context)
    except agent.AgentError as e:
        if mode != "auto":
            raise
        # auto mode (used by the deployment): if Bedrock fails, the demo keeps going in rehearsal mode
        log.warning("Bedrock unavailable, using rehearsal mode: %s", e)
        _bedrock_down.update(until=time.time() + 300, notice=f"Bedrock is unavailable right now, answering in rehearsal mode. Reason: {e}")
        result = await rehearsal.respond(text, session, business=name, context=context)
        result["notice"] = _bedrock_down["notice"]
        return result


@route("/api/b/{slug}/conversation", ["POST"])
async def api_business_conversation(request: Request) -> JSONResponse:
    business = _public_business(request)
    if business is None:
        return _error("No business with that address.", 404)
    return await _conversation(request, business)


@route("/api/conversation", ["POST"])
async def api_conversation(request: Request) -> JSONResponse:
    """The demo business's phone line (kept for the first version's simulator)."""
    return await _conversation(request, tenancy.demo_business())


async def _end_conversation(request: Request) -> JSONResponse:
    agent.end_session(request.path_params["session"])
    rehearsal.end_session(request.path_params["session"])
    return JSONResponse({"ok": True})


mcp.custom_route("/api/conversation/{session}", methods=["DELETE"])(_end_conversation)
mcp.custom_route("/api/b/{slug}/conversation/{session}", methods=["DELETE"])(_end_conversation)


async def _week(request: Request, business: tenancy.Business) -> JSONResponse:
    """The next days of the schedule for the live board. Optional: ?start=today&days=7."""
    account_id = _account_id(request)
    if not business.public_demo and account_id != business.owner_id:
        return _error("Sign in to see this schedule.", 401)
    with tenancy.serving(business, owner=True):
        moment = schedule.now()
        try:
            start = schedule.parse_date(request.query_params.get("start", "today"), moment.date())
            days = max(1, min(31, int(request.query_params.get("days", "7"))))
        except (BookingError, ValueError) as e:
            return _error(str(e))
        with _conn() as conn:
            data = manage.calendar(conn, start, days, moment)
    return JSONResponse(data)


@route("/api/b/{slug}/week", ["GET"])
async def api_business_week(request: Request) -> JSONResponse:
    business = _public_business(request)
    if business is None:
        return _error("No business with that address.", 404)
    return await _week(request, business)


@route("/api/week", ["GET"])
async def api_week(request: Request) -> JSONResponse:
    return await _week(request, tenancy.demo_business())


@route("/api/schedule", ["GET"])
async def api_schedule(request: Request) -> JSONResponse:
    """One day of the demo business's appointments. Optional: ?date=YYYY-MM-DD."""
    with tenancy.serving(tenancy.demo_business()):
        moment = schedule.now()
        try:
            d = schedule.parse_date(request.query_params.get("date", "today"), moment.date())
        except BookingError as e:
            return _error(str(e))
        with _conn() as conn:
            items = schedule.appointments_on(conn, d)
            business = db.business_name(conn)
    return JSONResponse({"business": business, "date": d.isoformat(), "appointments": items})


# ---------------------------------------------------------------- owner API

def _int(value, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise BookingError(f"{label} must be a number.") from None


@owner_route("/setup", ["GET"])
async def api_setup(request: Request, conn) -> JSONResponse:
    business = tenancy.current()
    return JSONResponse({**manage.setup(conn), "slug": business.slug, "public_demo": business.public_demo,
                         "mcp_path": f"/b/{business.slug}/mcp", "has_api_key": tenancy.has_api_key(business),
                         "now": schedule.now().isoformat(timespec="minutes"),
                         "timezones": _timezones()})


def _timezones() -> list[str]:
    return ["America/Los_Angeles", "America/Denver", "America/Phoenix", "America/Chicago", "America/New_York",
            "America/Anchorage", "Pacific/Honolulu", "America/Toronto", "America/Vancouver", "America/Mexico_City",
            "America/Sao_Paulo", "Europe/London", "Europe/Lisbon", "Europe/Madrid", "Europe/Paris", "Europe/Berlin",
            "Africa/Johannesburg", "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney"]


@owner_route("/settings", ["PATCH"])
async def api_settings(request: Request, conn) -> JSONResponse:
    body = await _body(request)
    manage.update_settings(conn, name=body.get("name"), kind=body.get("type"), timezone=body.get("timezone"))
    return JSONResponse(manage.setup(conn))


@owner_route("/api-key", ["POST"])
async def api_new_key(request: Request, conn) -> JSONResponse:
    business = tenancy.current()
    if business.public_demo:
        raise BookingError("The public demo doesn't need a key: its owner tools are open.")
    return JSONResponse({"key": tenancy.new_api_key(business)})


@owner_route("/stats", ["GET"])
async def api_stats(request: Request, conn) -> JSONResponse:
    moment = schedule.now()
    return JSONResponse({**manage.stats(conn, moment), "summary": schedule.daily_summary(conn, moment.date(), moment)})


@owner_route("/calendar", ["GET"])
async def api_calendar(request: Request, conn) -> JSONResponse:
    moment = schedule.now()
    try:
        start = date.fromisoformat(request.query_params.get("start", moment.date().isoformat()))
    except ValueError:
        raise BookingError("start must look like 2026-10-23.") from None
    days = max(1, min(31, _int(request.query_params.get("days", "7"), "days")))
    return JSONResponse(manage.calendar(conn, start, days, moment))


@owner_route("/slots", ["GET"])
async def api_slots(request: Request, conn) -> JSONResponse:
    q = request.query_params
    moment = schedule.now()
    s = manage._service_by_id(conn, q.get("service_id"))
    b = manage._staff_by_id(conn, q.get("staff_id"))
    slots = schedule.open_slots(conn, manage._day(q.get("date")), s, moment, None, b)
    return JSONResponse({"slots": slots})


@owner_route("/appointments", ["POST"])
async def api_create_appointment(request: Request, conn) -> JSONResponse:
    b = await _body(request)
    appt = manage.create_appointment(conn, schedule.now(), b.get("customer_name", ""), b.get("phone"), b.get("service_id"),
                                     b.get("staff_id"), b.get("date", ""), b.get("time", ""), b.get("notes", ""))
    return JSONResponse({"appointment": appt})


@owner_route("/appointments/{appt_id:int}", ["PATCH"])
async def api_update_appointment(request: Request, conn) -> JSONResponse:
    changes = await _body(request)
    return JSONResponse(manage.update_appointment(conn, request.path_params["appt_id"], schedule.now(), changes))


@owner_route("/customers", ["GET"])
async def api_customers(request: Request, conn) -> JSONResponse:
    return JSONResponse({"customers": manage.customers(conn, request.query_params.get("q", ""), schedule.now())})


@owner_route("/customers", ["POST"])
async def api_create_customer(request: Request, conn) -> JSONResponse:
    b = await _body(request)
    return JSONResponse({"customer": manage.save_customer(conn, None, b.get("name", ""), b.get("phone", ""),
                                                          b.get("email", ""), b.get("notes", ""))})


@owner_route("/customers/{customer_id:int}", ["GET"])
async def api_customer(request: Request, conn) -> JSONResponse:
    return JSONResponse({"customer": manage.customer_detail(conn, request.path_params["customer_id"])})


@owner_route("/customers/{customer_id:int}", ["PATCH"])
async def api_update_customer(request: Request, conn) -> JSONResponse:
    b = await _body(request)
    current = schedule.get_customer(conn, request.path_params["customer_id"])
    merged = {**current, **{k: v for k, v in b.items() if k in ("name", "phone", "email", "notes")}}
    return JSONResponse({"customer": manage.save_customer(conn, current["id"], merged["name"], merged["phone"],
                                                          merged["email"], merged["notes"])})


@owner_route("/services", ["POST"])
async def api_create_service(request: Request, conn) -> JSONResponse:
    b = await _body(request)
    return JSONResponse({"service": manage.save_service(conn, None, b.get("name", ""), b.get("duration_min"), b.get("price_cents"))})


@owner_route("/services/{item_id:int}", ["PATCH"])
async def api_update_service(request: Request, conn) -> JSONResponse:
    b = await _body(request)
    current = dict(conn.execute("SELECT * FROM services WHERE id = ?", (request.path_params["item_id"],)).fetchone() or {})
    if not current:
        raise BookingError("That service doesn't exist.")
    merged = {**current, **b}
    return JSONResponse({"service": manage.save_service(conn, current["id"], merged["name"], merged["duration_min"],
                                                        merged["price_cents"], bool(merged["active"]))})


@owner_route("/services/{item_id:int}", ["DELETE"])
async def api_delete_service(request: Request, conn) -> JSONResponse:
    return JSONResponse({"result": manage.remove_service(conn, request.path_params["item_id"])})


@owner_route("/staff", ["POST"])
async def api_create_staff(request: Request, conn) -> JSONResponse:
    b = await _body(request)
    return JSONResponse({"staff": manage.save_staff(conn, None, b.get("name", ""))})


@owner_route("/staff/{item_id:int}", ["PATCH"])
async def api_update_staff(request: Request, conn) -> JSONResponse:
    b = await _body(request)
    current = dict(conn.execute("SELECT * FROM staff WHERE id = ?", (request.path_params["item_id"],)).fetchone() or {})
    if not current:
        raise BookingError("That team member doesn't exist.")
    merged = {**current, **b}
    return JSONResponse({"staff": manage.save_staff(conn, current["id"], merged["name"], bool(merged["active"]))})


@owner_route("/staff/{item_id:int}", ["DELETE"])
async def api_delete_staff(request: Request, conn) -> JSONResponse:
    return JSONResponse({"result": manage.remove_staff(conn, request.path_params["item_id"])})


@owner_route("/hours", ["PUT"])
async def api_hours(request: Request, conn) -> JSONResponse:
    b = await _body(request)
    manage.set_hours(conn, b.get("hours") or [])
    return JSONResponse(manage.setup(conn))


@owner_route("/waitlist", ["GET"])
async def api_waitlist(request: Request, conn) -> JSONResponse:
    return JSONResponse({"waitlist": manage.waitlist(conn, schedule.now())})


@owner_route("/waitlist", ["POST"])
async def api_add_waitlist(request: Request, conn) -> JSONResponse:
    b = await _body(request)
    entry = manage.add_waitlist(conn, schedule.now(), b.get("customer_name", ""), b.get("phone", ""), b.get("service_id"),
                                b.get("staff_id"), b.get("date", ""), b.get("time") or None, b.get("period") or None)
    return JSONResponse({"entry": entry})


@owner_route("/waitlist/{item_id:int}", ["DELETE"])
async def api_remove_waitlist(request: Request, conn) -> JSONResponse:
    manage.remove_waitlist(conn, request.path_params["item_id"])
    return JSONResponse({"ok": True})


@owner_route("/blocks", ["POST"])
async def api_add_block(request: Request, conn) -> JSONResponse:
    b = await _body(request)
    block = manage.add_block(conn, schedule.now(), b.get("staff_id"), b.get("date", ""), b.get("start", ""),
                             b.get("end", ""), b.get("reason", ""))
    return JSONResponse({"block": block})


@owner_route("/blocks/{item_id:int}", ["DELETE"])
async def api_remove_block(request: Request, conn) -> JSONResponse:
    manage.remove_block(conn, request.path_params["item_id"])
    return JSONResponse({"ok": True})
