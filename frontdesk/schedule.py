"""Booking rules: open slots, book, cancel, reschedule and the owner's daily summary.

Every function takes the SQLite connection and "now" explicitly, so tests and the demo can
pin the date. All dates and times are local to the business.
"""
from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("FRONTDESK_TZ", "America/Los_Angeles"))
STEP_MIN = 30  # how often we offer a start time

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAY_NAMES = [d.capitalize() for d in WEEKDAYS]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
MONTH_NAMES = [m.capitalize() for m in MONTHS]
PERIODS = {
    "morning": (time(0, 0), time(12, 0)),
    "afternoon": (time(12, 0), time(17, 0)),
    "evening": (time(17, 0), time(23, 59)),
}
PERIOD_ALIASES = {"tonight": "evening", "night": "evening", "noon": "afternoon", "lunch": "afternoon"}
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


class BookingError(ValueError):
    """Error whose message is ready to be spoken to the customer."""


@dataclass
class Service:
    id: int
    name: str
    duration_min: int
    price_cents: int


@dataclass
class Staff:
    id: int
    name: str


# ---------------------------------------------------------------- helpers

def now() -> datetime:
    """Business local time. FRONTDESK_NOW (e.g. 2026-10-23T08:00) pins the clock for the demo and tests."""
    pinned = os.environ.get("FRONTDESK_NOW")
    if pinned:
        return datetime.fromisoformat(pinned).replace(tzinfo=None)
    return datetime.now(TZ).replace(tzinfo=None)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").strip().lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _normalize_service(text: str) -> str:
    t = normalize(text).replace("+", " and ").replace("&", " and ").replace("-", " ")
    t = re.sub(r"\b(a|an|the|my|just|please)\b", " ", t)
    t = t.replace("hair cut", "haircut")
    return " ".join(t.split())


def parse_date(text: str, today: date) -> date:
    """Accepts 2026-10-23, 10/23, 10/23/2026, October 23, Oct 23rd, the 23rd, today, tomorrow or a weekday."""
    t = normalize(text).replace(",", " ")
    t = " ".join(t.split())
    if t in ("today", "tonight", "this morning", "this afternoon", "this evening"):
        return today
    if t == "tomorrow":
        return today + timedelta(days=1)
    if t in ("day after tomorrow", "the day after tomorrow"):
        return today + timedelta(days=2)
    # "next friday", "this friday", "on friday", "friday morning"
    words = t.split()
    next_week = words[:1] == ["next"]
    words = [w for w in words if w not in ("next", "this", "on", "coming", "the", "for")]
    for i, d in enumerate(WEEKDAYS):
        if words and (words[0] == d or words[0] == d[:3] or words[0] == d + "s"):
            delta = (i - today.weekday()) % 7
            if next_week and delta == 0:
                delta = 7
            return today + timedelta(days=delta)
    try:
        return date.fromisoformat(t)
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", t)
    if m:  # US order: month/day
        month, day = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        return _build_date(year, month, day, today, explicit_year=bool(m.group(3)), original=text)
    m = re.fullmatch(r"([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?", t)
    if m and _month(m.group(1)):
        year = int(m.group(3)) if m.group(3) else today.year
        return _build_date(year, _month(m.group(1)), int(m.group(2)), today, bool(m.group(3)), text)
    m = re.fullmatch(r"(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+([a-z]+)\.?(?:\s+(\d{4}))?", t)
    if m and _month(m.group(2)):
        year = int(m.group(3)) if m.group(3) else today.year
        return _build_date(year, _month(m.group(2)), int(m.group(1)), today, bool(m.group(3)), text)
    m = re.fullmatch(r"(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)", t)
    if m:  # "the 23rd": this month, or next month if it already passed
        day = int(m.group(1))
        d = _build_date(today.year, today.month, day, today, True, text)
        if d < today:
            month = today.month % 12 + 1
            d = _build_date(today.year + (month == 1), month, day, today, True, text)
        return d
    raise BookingError(f"Sorry, I didn't catch the date {text}. You can say, for example, Friday or October 23.")


def _month(word: str) -> int | None:
    for i, m in enumerate(MONTHS):
        if word == m or (len(word) >= 3 and m.startswith(word)):
            return i + 1
    return None


def _build_date(year: int, month: int, day: int, today: date, explicit_year: bool, original: str) -> date:
    if year < 100:
        year += 2000
    try:
        d = date(year, month, day)
    except ValueError as e:
        raise BookingError(f"Sorry, {original} isn't a valid date.") from e
    if not explicit_year and d < today:
        d = date(year + 1, month, day)
    return d


def parse_time(text: str) -> time:
    """Accepts 9, 9am, 9 a.m., 9:30, 9:30 pm, 14:00, 2 o'clock, noon, half past 9, nine thirty."""
    t = normalize(text).replace(".", "").replace("o'clock", "").replace("oclock", "")
    t = " ".join(t.split())
    if t in ("noon", "midday", "12 noon"):
        return time(12, 0)
    for word, n in NUMBER_WORDS.items():
        t = re.sub(rf"\b{word}\b", str(n), t)
    t = re.sub(r"\b(\d{1,2}) thirty\b", r"\1:30", t)
    t = re.sub(r"\b(\d{1,2}) fifteen\b", r"\1:15", t)
    t = re.sub(r"\b(\d{1,2}) forty five\b", r"\1:45", t)
    half = re.fullmatch(r"half past (\d{1,2})(?:\s*(am|pm))?", t)
    if half:
        t = f"{half.group(1)}:30{half.group(2) or ''}"
    quarter = re.fullmatch(r"quarter past (\d{1,2})(?:\s*(am|pm))?", t)
    if quarter:
        t = f"{quarter.group(1)}:15{quarter.group(2) or ''}"
    m = re.fullmatch(r"(?:at\s+)?(\d{1,2})(?::|h|\s)?(\d{2})?\s*(am|pm|in the morning|in the afternoon|in the evening)?", t)
    if not m:
        raise BookingError(f"Sorry, I didn't catch the time {text}. You can say, for example, 9:30 AM.")
    hour, minute, suffix = int(m.group(1)), int(m.group(2) or 0), m.group(3) or ""
    if suffix in ("pm", "in the afternoon", "in the evening") and hour < 12:
        hour += 12
    elif suffix in ("am", "in the morning") and hour == 12:
        hour = 0
    elif not suffix and 1 <= hour <= 7:
        hour += 12  # "at 3" means 3 PM at a barbershop
    try:
        return time(hour, minute)
    except ValueError as e:
        raise BookingError(f"Sorry, {text} isn't a valid time.") from e


def say_date(d: date, today: date) -> str:
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    return f"{WEEKDAY_NAMES[d.weekday()]}, {MONTH_NAMES[d.month - 1]} {d.day}"


def say_date_on(d: date, today: date) -> str:
    """'today', 'tomorrow' or 'on Friday, October 23' (for the middle of a sentence)."""
    spoken = say_date(d, today)
    return spoken if spoken in ("today", "tomorrow") else f"on {spoken}"


def say_time(h: time) -> str:
    suffix = "AM" if h.hour < 12 else "PM"
    hour = h.hour % 12 or 12
    if h.hour == 12 and h.minute == 0:
        return "noon"
    return f"{hour} {suffix}" if h.minute == 0 else f"{hour}:{h.minute:02d} {suffix}"


def dollars(cents: int) -> str:
    return f"${cents // 100}" if cents % 100 == 0 else f"${cents / 100:.2f}"


def join_or(items: list[str]) -> str:
    if len(items) <= 2:
        return " or ".join(items)
    return ", ".join(items[:-1]) + ", or " + items[-1]


def join_and(items: list[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


# ---------------------------------------------------------------- queries

SERVICE_SYNONYMS = {
    "cut": "haircut", "trim": "haircut", "hair": "haircut", "fade": "haircut", "buzz": "haircut",
    "buzz cut": "haircut", "beard": "beard trim", "shave": "hot towel shave", "hot shave": "hot towel shave",
    "haircut and beard trim": "haircut and beard", "cut and beard": "haircut and beard",
    "beard and haircut": "haircut and beard", "hair and beard": "haircut and beard",
    "cut and shave": "haircut and beard", "the works": "haircut and beard", "combo": "haircut and beard",
}


def find_service(conn: sqlite3.Connection, name: str) -> Service:
    target = _normalize_service(name)
    target = SERVICE_SYNONYMS.get(target, target)
    rows = conn.execute("SELECT id, name, duration_min, price_cents FROM services").fetchall()
    services = [Service(**dict(r)) for r in rows]
    exact = [s for s in services if _normalize_service(s.name) == target]
    if exact:
        return exact[0]
    similar = [s for s in services if target and target in _normalize_service(s.name)]
    if len(similar) == 1:
        return similar[0]
    if similar:
        similar.sort(key=lambda s: len(s.name))
        return similar[0]
    contained = [s for s in services if _normalize_service(s.name) in target]
    contained.sort(key=lambda s: len(s.name), reverse=True)
    if contained:
        return contained[0]
    names = join_and([s.name.lower() for s in services])
    raise BookingError(f"We don't offer {name}. We have {names}.")


def find_staff(conn: sqlite3.Connection, name: str | None) -> Staff | None:
    if not name or normalize(name) in ("anyone", "any", "whoever", "no preference", "anybody"):
        return None
    target = normalize(name)
    for r in conn.execute("SELECT id, name FROM staff ORDER BY id").fetchall():
        if normalize(r["name"]) == target or target in normalize(r["name"]) or normalize(r["name"]) in target:
            return Staff(r["id"], r["name"])
    names = join_and([r["name"] for r in conn.execute("SELECT name FROM staff ORDER BY id")])
    raise BookingError(f"{name} doesn't work here. Our barbers are {names}.")


def all_staff(conn: sqlite3.Connection) -> list[Staff]:
    return [Staff(r["id"], r["name"]) for r in conn.execute("SELECT id, name FROM staff ORDER BY id")]


def hours_for(conn: sqlite3.Connection, d: date) -> tuple[time, time] | None:
    r = conn.execute("SELECT opens, closes FROM opening_hours WHERE weekday = ?", (d.weekday(),)).fetchone()
    if not r:
        return None
    return time.fromisoformat(r["opens"]), time.fromisoformat(r["closes"])


def _busy(conn: sqlite3.Connection, staff_id: int, d: date, ignore_id: int | None = None):
    rows = conn.execute(
        "SELECT id, start, end FROM appointments WHERE staff_id = ? AND status = 'confirmed' "
        "AND substr(start, 1, 10) = ?",
        (staff_id, d.isoformat()),
    ).fetchall()
    return [
        (datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"]))
        for r in rows
        if r["id"] != ignore_id
    ]


def _is_free(conn, staff_id: int, start: datetime, end: datetime, ignore_id: int | None = None) -> bool:
    return all(end <= a or start >= b for a, b in _busy(conn, staff_id, start.date(), ignore_id))


def _check_window(conn, start: datetime, end: datetime, moment: datetime) -> None:
    today = moment.date()
    window = hours_for(conn, start.date())
    if window is None:
        raise BookingError(f"We're closed {say_date_on(start.date(), today)}.")
    opens, closes = window
    if start.time() < opens or end.time() > closes or end.date() != start.date():
        raise BookingError(
            f"{say_date(start.date(), today).capitalize()} we're open from {say_time(opens)} to {say_time(closes)}."
        )
    if start < moment:
        raise BookingError("That time has already passed.")


def open_slots(
    conn: sqlite3.Connection,
    d: date,
    service: Service,
    moment: datetime,
    period: str | None = None,
    staff: Staff | None = None,
) -> list[dict]:
    """Start times where the service fits, per barber, every STEP_MIN minutes."""
    window = hours_for(conn, d)
    if window is None:
        return []
    opens, closes = window
    p = normalize(period or "")
    p = PERIOD_ALIASES.get(p, p)
    period_start, period_end = PERIODS.get(p, (time(0, 0), time(23, 59)))
    duration = timedelta(minutes=service.duration_min)
    team = [staff] if staff else all_staff(conn)
    result = []
    for member in team:
        cursor = datetime.combine(d, opens)
        limit = datetime.combine(d, closes)
        while cursor + duration <= limit:
            if (
                cursor >= moment
                and period_start <= cursor.time() < period_end
                and _is_free(conn, member.id, cursor, cursor + duration)
            ):
                result.append({"staff": member.name, "time": cursor.strftime("%H:%M")})
            cursor += timedelta(minutes=STEP_MIN)
    result.sort(key=lambda x: (x["time"], x["staff"]))
    return result


# ---------------------------------------------------------------- actions

def book(
    conn: sqlite3.Connection,
    customer: str,
    service: Service,
    start: datetime,
    moment: datetime,
    staff: Staff | None = None,
) -> dict:
    customer = " ".join((customer or "").split()).title()
    if not customer:
        raise BookingError("Can I get your name?")
    end = start + timedelta(minutes=service.duration_min)
    _check_window(conn, start, end, moment)
    candidates = [staff] if staff else all_staff(conn)
    chosen = next((p for p in candidates if _is_free(conn, p.id, start, end)), None)
    if chosen is None:
        who = f" with {staff.name}" if staff else ""
        raise BookingError(f"Sorry, {say_time(start.time())}{who} is already taken.")
    cur = conn.execute(
        "INSERT INTO appointments (customer, service_id, staff_id, start, end) VALUES (?, ?, ?, ?, ?)",
        (customer, service.id, chosen.id, start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")),
    )
    conn.commit()
    return get_appointment(conn, cur.lastrowid)


def get_appointment(conn: sqlite3.Connection, appt_id: int) -> dict:
    r = conn.execute(
        "SELECT a.id, a.customer, a.start, a.end, a.status, s.name AS service, s.price_cents, p.name AS staff "
        "FROM appointments a JOIN services s ON s.id = a.service_id JOIN staff p ON p.id = a.staff_id "
        "WHERE a.id = ?",
        (appt_id,),
    ).fetchone()
    return dict(r)


def find_appointments(conn: sqlite3.Connection, customer: str, moment: datetime, d: date | None = None) -> list[dict]:
    """Upcoming confirmed appointments for a customer (accent-insensitive, matches part of the name)."""
    target = normalize(customer)
    rows = conn.execute(
        "SELECT id, customer, start FROM appointments WHERE status = 'confirmed' AND start >= ? ORDER BY start",
        (moment.isoformat(timespec="minutes"),),
    ).fetchall()
    found = []
    for r in rows:
        if target and target in normalize(r["customer"]):
            if d is None or r["start"].startswith(d.isoformat()):
                found.append(get_appointment(conn, r["id"]))
    return found


def cancel(conn: sqlite3.Connection, appt_id: int) -> dict:
    conn.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (appt_id,))
    conn.commit()
    return get_appointment(conn, appt_id)


def reschedule(conn: sqlite3.Connection, appt_id: int, new_start: datetime, moment: datetime) -> dict:
    current = conn.execute(
        "SELECT a.service_id, a.staff_id, s.duration_min FROM appointments a "
        "JOIN services s ON s.id = a.service_id WHERE a.id = ?",
        (appt_id,),
    ).fetchone()
    end = new_start + timedelta(minutes=current["duration_min"])
    _check_window(conn, new_start, end, moment)
    preferred = current["staff_id"]
    order = [preferred] + [p.id for p in all_staff(conn) if p.id != preferred]
    chosen = next((pid for pid in order if _is_free(conn, pid, new_start, end, ignore_id=appt_id)), None)
    if chosen is None:
        raise BookingError(f"Sorry, {say_time(new_start.time())} is already taken.")
    conn.execute(
        "UPDATE appointments SET start = ?, end = ?, staff_id = ? WHERE id = ?",
        (new_start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes"), chosen, appt_id),
    )
    conn.commit()
    return get_appointment(conn, appt_id)


def daily_summary(conn: sqlite3.Connection, d: date, moment: datetime) -> dict:
    rows = conn.execute(
        "SELECT id FROM appointments WHERE status = 'confirmed' AND substr(start, 1, 10) = ? ORDER BY start",
        (d.isoformat(),),
    ).fetchall()
    appts = [get_appointment(conn, r["id"]) for r in rows]
    upcoming = [a for a in appts if datetime.fromisoformat(a["start"]) >= moment]
    revenue = sum(a["price_cents"] for a in appts)
    window = hours_for(conn, d)
    free_by_staff = {}
    if window:
        opens, closes = window
        for p in all_staff(conn):
            busy = sum((b - a).total_seconds() / 60 for a, b in _busy(conn, p.id, d))
            total = (datetime.combine(d, closes) - datetime.combine(d, opens)).total_seconds() / 60
            free_by_staff[p.name] = int(total - busy)
    return {
        "date": d.isoformat(),
        "open": window is not None,
        "total_customers": len(appts),
        "expected_revenue_cents": revenue,
        "next": upcoming[0] if upcoming else None,
        "appointments": appts,
        "free_minutes_by_staff": free_by_staff,
    }


def appointments_on(conn: sqlite3.Connection, d: date) -> list[dict]:
    rows = conn.execute(
        "SELECT id FROM appointments WHERE substr(start, 1, 10) = ? ORDER BY start", (d.isoformat(),)
    ).fetchall()
    return [get_appointment(conn, r["id"]) for r in rows]


# ---------------------------------------------------------------- demo data

def seed_demo(conn: sqlite3.Connection, today: date) -> None:
    """Adds a few booked slots over the next days so the schedule looks real."""
    if conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]:
        return
    services = {s["name"]: s["id"] for s in conn.execute("SELECT id, name FROM services")}
    staff = {p["name"]: p["id"] for p in conn.execute("SELECT id, name FROM staff")}
    durations = {s["id"]: s["duration_min"] for s in conn.execute("SELECT id, duration_min FROM services")}
    template = [
        (0, "10:00", "Chris Miller", "Haircut", "Marcus"),
        (0, "15:30", "Kevin Lee", "Haircut and beard", "Diego"),
        (1, "09:00", "Luke Rogers", "Beard trim", "Marcus"),
        (1, "11:00", "Andre Wilson", "Haircut", "Diego"),
        (1, "17:00", "Frank Moore", "Hot towel shave", "Marcus"),
        (2, "09:30", "Mark Davis", "Haircut and beard", "Marcus"),
        (2, "14:00", "Tom Allen", "Haircut", "Diego"),
        (3, "10:30", "Ryan Nunez", "Haircut", "Marcus"),
        (4, "09:00", "Greg Reyes", "Haircut", "Diego"),
    ]
    for days, hhmm, customer, service, barber in template:
        d = today + timedelta(days=days)
        window = hours_for(conn, d)
        if window is None:
            continue
        start = datetime.combine(d, time.fromisoformat(hhmm))
        end = start + timedelta(minutes=durations[services[service]])
        if start.time() < window[0] or end.time() > window[1]:
            continue
        conn.execute(
            "INSERT INTO appointments (customer, service_id, staff_id, start, end) VALUES (?, ?, ?, ?, ?)",
            (customer, services[service], staff[barber], start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")),
        )
    conn.commit()
