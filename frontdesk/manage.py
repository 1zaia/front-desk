"""What the owner does by hand in the Front Desk app: the same schedule the voice receptionist uses.

Services, team, opening hours, customers, appointments (create, move, change status), waitlist and
blocks. Errors are BookingError with a message ready to show.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, time, timedelta

from frontdesk import business_types, schedule
from frontdesk.schedule import BookingError

STATUSES = ("confirmed", "completed", "cancelled", "no_show")


def _hhmm(value: str, label: str) -> str:
    if not re.fullmatch(r"\d{2}:\d{2}", value or ""):
        raise BookingError(f"{label} must look like 09:30.")
    try:
        return time.fromisoformat(value).strftime("%H:%M")
    except ValueError:
        raise BookingError(f"{label} isn't a valid time.") from None


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise BookingError("The date must look like 2026-10-23.") from None


def _service_by_id(conn, service_id) -> schedule.Service:
    r = conn.execute("SELECT id, name, duration_min, price_cents FROM services WHERE id = ?", (service_id,)).fetchone()
    if not r:
        raise BookingError("Pick a service.")
    return schedule.Service(**dict(r))


def _staff_by_id(conn, staff_id) -> schedule.Staff | None:
    if staff_id in (None, "", 0):
        return None
    r = conn.execute("SELECT id, name FROM staff WHERE id = ?", (staff_id,)).fetchone()
    if not r:
        raise BookingError("That team member doesn't exist.")
    return schedule.Staff(r["id"], r["name"])


# ---------------------------------------------------------------- business setup

def setup(conn: sqlite3.Connection) -> dict:
    info = dict(conn.execute("SELECT name, timezone, type FROM business WHERE id = 1").fetchone())
    kind = business_types.get(info["type"])
    hours = {r["weekday"]: {"opens": r["opens"], "closes": r["closes"]}
             for r in conn.execute("SELECT weekday, opens, closes FROM opening_hours")}
    return {
        **info,
        "type_label": kind["label"], "staff_noun": kind["staff"], "staff_plural": kind["staff_plural"],
        "customer_noun": kind["customer"],
        "services": [dict(r) for r in conn.execute("SELECT id, name, duration_min, price_cents, active FROM services ORDER BY active DESC, id")],
        "staff": [dict(r) for r in conn.execute("SELECT id, name, active FROM staff ORDER BY active DESC, id")],
        "hours": [{"weekday": d, "name": schedule.WEEKDAY_NAMES[d], **hours.get(d, {"opens": None, "closes": None})} for d in range(7)],
    }


def update_settings(conn, name: str | None = None, kind: str | None = None, timezone: str | None = None) -> None:
    if name is not None:
        name = " ".join(name.split())
        if not name:
            raise BookingError("The business needs a name.")
        conn.execute("UPDATE business SET name = ? WHERE id = 1", (name,))
    if kind is not None:
        if kind not in business_types.TYPES:
            raise BookingError("Pick a type of business.")
        conn.execute("UPDATE business SET type = ? WHERE id = 1", (kind,))
    if timezone is not None:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(timezone)
        except Exception:
            raise BookingError("Pick a valid time zone.") from None
        conn.execute("UPDATE business SET timezone = ? WHERE id = 1", (timezone,))
        schedule._tz_cache.clear()
    conn.commit()


def save_service(conn, service_id: int | None, name: str, duration_min, price_cents, active: bool = True) -> dict:
    name = " ".join((name or "").split())
    if not name:
        raise BookingError("The service needs a name.")
    try:
        duration_min, price_cents = int(duration_min), int(price_cents)
    except (TypeError, ValueError):
        raise BookingError("Duration and price must be numbers.") from None
    if not 5 <= duration_min <= 600:
        raise BookingError("Duration must be between 5 and 600 minutes.")
    if not 0 <= price_cents <= 10_000_000:
        raise BookingError("The price doesn't look right.")
    clash = conn.execute("SELECT id FROM services WHERE lower(name) = lower(?) AND id IS NOT ?", (name, service_id)).fetchone()
    if clash:
        raise BookingError(f"There's already a service called {name}.")
    if service_id:
        conn.execute("UPDATE services SET name = ?, duration_min = ?, price_cents = ?, active = ? WHERE id = ?",
                     (name, duration_min, price_cents, int(bool(active)), service_id))
    else:
        service_id = conn.execute("INSERT INTO services (name, duration_min, price_cents) VALUES (?, ?, ?)",
                                  (name, duration_min, price_cents)).lastrowid
    conn.commit()
    return dict(conn.execute("SELECT id, name, duration_min, price_cents, active FROM services WHERE id = ?", (service_id,)).fetchone())


def remove_service(conn, service_id: int) -> str:
    """Deletes a service nobody booked yet; otherwise just stops offering it (history stays intact)."""
    used = conn.execute("SELECT 1 FROM appointments WHERE service_id = ? UNION SELECT 1 FROM waitlist WHERE service_id = ?",
                        (service_id, service_id)).fetchone()
    if used:
        conn.execute("UPDATE services SET active = 0 WHERE id = ?", (service_id,))
        result = "archived"
    else:
        conn.execute("DELETE FROM services WHERE id = ?", (service_id,))
        result = "deleted"
    if not conn.execute("SELECT 1 FROM services WHERE active = 1").fetchone():
        conn.rollback()
        raise BookingError("Keep at least one service.")
    conn.commit()
    return result


def save_staff(conn, staff_id: int | None, name: str, active: bool = True) -> dict:
    name = " ".join((name or "").split())
    if not name:
        raise BookingError("Enter a name.")
    clash = conn.execute("SELECT id FROM staff WHERE lower(name) = lower(?) AND id IS NOT ?", (name, staff_id)).fetchone()
    if clash:
        raise BookingError(f"There's already someone called {name}.")
    if staff_id:
        conn.execute("UPDATE staff SET name = ?, active = ? WHERE id = ?", (name, int(bool(active)), staff_id))
    else:
        staff_id = conn.execute("INSERT INTO staff (name) VALUES (?)", (name,)).lastrowid
    if not conn.execute("SELECT 1 FROM staff WHERE active = 1").fetchone():
        conn.rollback()
        raise BookingError("Keep at least one active team member.")
    conn.commit()
    return dict(conn.execute("SELECT id, name, active FROM staff WHERE id = ?", (staff_id,)).fetchone())


def remove_staff(conn, staff_id: int) -> str:
    used = conn.execute("SELECT 1 FROM appointments WHERE staff_id = ? UNION SELECT 1 FROM blocks WHERE staff_id = ?",
                        (staff_id, staff_id)).fetchone()
    if used:
        conn.execute("UPDATE staff SET active = 0 WHERE id = ?", (staff_id,))
        result = "archived"
    else:
        conn.execute("DELETE FROM staff WHERE id = ?", (staff_id,))
        result = "deleted"
    if not conn.execute("SELECT 1 FROM staff WHERE active = 1").fetchone():
        conn.rollback()
        raise BookingError("Keep at least one active team member.")
    conn.commit()
    return result


def set_hours(conn, hours: list[dict]) -> None:
    """hours: [{"weekday": 0, "opens": "09:00", "closes": "18:00"}, ...]; a weekday left out (or without times) is closed."""
    rows = []
    for h in hours:
        if not h.get("opens") or not h.get("closes"):
            continue
        weekday = int(h["weekday"])
        if not 0 <= weekday <= 6:
            raise BookingError("Unknown weekday.")
        opens, closes = _hhmm(h["opens"], "Opening time"), _hhmm(h["closes"], "Closing time")
        if closes <= opens:
            raise BookingError(f"On {schedule.WEEKDAY_NAMES[weekday]} closing time must be after opening time.")
        rows.append((weekday, opens, closes))
    if not rows:
        raise BookingError("Open at least one day a week.")
    conn.execute("DELETE FROM opening_hours")
    conn.executemany("INSERT INTO opening_hours (weekday, opens, closes) VALUES (?, ?, ?)", rows)
    conn.commit()


# ---------------------------------------------------------------- calendar

def calendar(conn, start: date, days: int, moment: datetime) -> dict:
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
            "blocks": schedule.blocks_on(conn, d),
            "waitlist": schedule.waitlist_on(conn, d),
        })
    staff = schedule.all_staff(conn)
    return {
        "business": conn.execute("SELECT name FROM business WHERE id = 1").fetchone()["name"],
        "now": moment.isoformat(timespec="minutes"),
        "staff": [p.name for p in staff],
        "staff_detail": [{"id": p.id, "name": p.name} for p in staff],
        "days": listing,
    }


def create_appointment(conn, moment: datetime, customer_name: str, phone: str | None, service_id, staff_id,
                       day: str, start_time: str, notes: str = "") -> dict:
    s = _service_by_id(conn, service_id)
    b = _staff_by_id(conn, staff_id)
    start = datetime.combine(_day(day), time.fromisoformat(_hhmm(start_time, "Start time")))
    if phone and not schedule.normalize_phone(phone):
        raise BookingError("That doesn't look like a phone number.")
    return schedule.book(conn, customer_name, s, start, moment, b, source="manual", phone=phone, notes=notes)


def update_appointment(conn, appt_id: int, moment: datetime, changes: dict) -> dict:
    """changes may hold: date + time (move), staff_id, service_id, status, notes, customer_name, phone."""
    current = conn.execute("SELECT * FROM appointments WHERE id = ?", (appt_id,)).fetchone()
    if not current:
        raise BookingError("That appointment doesn't exist.")
    filled = []
    old_start = datetime.fromisoformat(current["start"])
    if any(k in changes for k in ("date", "time", "staff_id", "service_id")):
        if current["status"] != "confirmed":
            raise BookingError("Only confirmed appointments can be moved.")
        s = _service_by_id(conn, changes.get("service_id") or current["service_id"])
        b = _staff_by_id(conn, changes.get("staff_id") or current["staff_id"])
        day = _day(changes["date"]) if changes.get("date") else old_start.date()
        t = time.fromisoformat(_hhmm(changes["time"], "Start time")) if changes.get("time") else old_start.time()
        start = datetime.combine(day, t)
        end = start + timedelta(minutes=s.duration_min)
        schedule._check_window(conn, start, end, moment)
        if not schedule._is_free(conn, b.id, start, end, ignore_id=appt_id):
            raise BookingError(f"{b.name} isn't free at {schedule.say_time(t)}.")
        conn.execute("UPDATE appointments SET start = ?, end = ?, staff_id = ?, service_id = ? WHERE id = ?",
                     (start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes"), b.id, s.id, appt_id))
        conn.commit()
        if start != old_start or b.id != current["staff_id"]:
            filled += schedule.fill_from_waitlist(conn, old_start.date(), moment, old_start)
    if "status" in changes and changes["status"] != current["status"]:
        status = changes["status"]
        if status not in STATUSES:
            raise BookingError("Unknown status.")
        if status == "confirmed" and current["status"] == "cancelled":
            start = datetime.fromisoformat(current["start"])
            if not schedule._is_free(conn, current["staff_id"], start, datetime.fromisoformat(current["end"]), ignore_id=appt_id):
                raise BookingError("That slot was taken in the meantime.")
        conn.execute("UPDATE appointments SET status = ? WHERE id = ?", (status, appt_id))
        conn.commit()
        if status == "cancelled" and old_start >= moment:
            filled += schedule.fill_from_waitlist(conn, old_start.date(), moment, old_start)
    if "notes" in changes:
        conn.execute("UPDATE appointments SET notes = ? WHERE id = ?", ((changes["notes"] or "").strip(), appt_id))
    if changes.get("customer_name") or changes.get("phone"):
        name = schedule.clean_name(changes.get("customer_name") or current["customer"])
        phone = schedule.normalize_phone(changes.get("phone")) if changes.get("phone") else current["phone"]
        if changes.get("phone") and not phone:
            raise BookingError("That doesn't look like a phone number.")
        customer_id = schedule.upsert_customer(conn, name, phone)["id"] if phone else current["customer_id"]
        conn.execute("UPDATE appointments SET customer = ?, phone = ?, customer_id = ? WHERE id = ?",
                     (name, phone or "", customer_id, appt_id))
    conn.commit()
    return {"appointment": schedule.get_appointment(conn, appt_id), "filled_from_waitlist": filled}


# ---------------------------------------------------------------- customers

def customers(conn, query: str = "", moment: datetime | None = None) -> list[dict]:
    now_iso = (moment or schedule.now()).isoformat(timespec="minutes")
    rows = conn.execute(
        "SELECT c.id, c.name, c.phone, c.email, c.notes, c.created_at, "
        "SUM(CASE WHEN a.status = 'completed' OR (a.status = 'confirmed' AND a.start < ?) THEN 1 ELSE 0 END) AS visits, "
        "SUM(CASE WHEN a.status = 'no_show' THEN 1 ELSE 0 END) AS no_shows, "
        "MAX(CASE WHEN a.start < ? AND a.status IN ('completed', 'confirmed') THEN a.start END) AS last_visit, "
        "MIN(CASE WHEN a.start >= ? AND a.status = 'confirmed' THEN a.start END) AS next_visit, "
        "SUM(CASE WHEN a.status IN ('completed', 'confirmed') AND a.start < ? THEN s.price_cents ELSE 0 END) AS spent_cents "
        "FROM customers c LEFT JOIN appointments a ON a.customer_id = c.id LEFT JOIN services s ON s.id = a.service_id "
        "GROUP BY c.id ORDER BY COALESCE(next_visit, '9999'), last_visit DESC, c.name",
        (now_iso, now_iso, now_iso, now_iso),
    ).fetchall()
    found = [dict(r) for r in rows]
    q = schedule.normalize(query or "")
    digits = re.sub(r"\D", "", query or "")
    if q:
        found = [c for c in found if q in schedule.normalize(c["name"]) or (digits and digits in c["phone"])
                 or q in (c["email"] or "").lower()]
    return found


def customer_detail(conn, customer_id: int) -> dict:
    c = schedule.get_customer(conn, customer_id)
    rows = conn.execute("SELECT id FROM appointments WHERE customer_id = ? OR (customer_id IS NULL AND phone = ?) "
                        "ORDER BY start DESC", (customer_id, c["phone"])).fetchall()
    return {**c, "appointments": [schedule.get_appointment(conn, r["id"]) for r in rows]}


def save_customer(conn, customer_id: int | None, name: str, phone: str, email: str = "", notes: str = "") -> dict:
    name = schedule.clean_name(name)
    if not name:
        raise BookingError("Enter the customer's name.")
    normalized = schedule.normalize_phone(phone)
    if not normalized:
        raise BookingError("Enter a valid phone number, e.g. (310) 555-0142.")
    email = (email or "").strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise BookingError("That email doesn't look right.")
    clash = conn.execute("SELECT id FROM customers WHERE phone = ? AND id IS NOT ?", (normalized, customer_id)).fetchone()
    if clash:
        raise BookingError("Another customer already has that phone number.")
    if customer_id:
        conn.execute("UPDATE customers SET name = ?, phone = ?, email = ?, notes = ? WHERE id = ?",
                     (name, normalized, email, (notes or "").strip(), customer_id))
        conn.execute("UPDATE appointments SET phone = ? WHERE customer_id = ?", (normalized, customer_id))
    else:
        customer_id = conn.execute("INSERT INTO customers (name, phone, email, notes) VALUES (?, ?, ?, ?)",
                                   (name, normalized, email, (notes or "").strip())).lastrowid
    conn.commit()
    return schedule.get_customer(conn, customer_id)


# ---------------------------------------------------------------- waitlist and blocks

def waitlist(conn, moment: datetime) -> list[dict]:
    rows = conn.execute("SELECT id FROM waitlist WHERE day >= ? AND status != 'removed' ORDER BY day, id",
                        (moment.date().isoformat(),)).fetchall()
    return [schedule._waitlist_entry(conn, r["id"]) for r in rows]


def add_waitlist(conn, moment: datetime, customer_name: str, phone: str, service_id, staff_id, day: str,
                 at: str | None = None, period: str | None = None) -> dict:
    s = _service_by_id(conn, service_id)
    b = _staff_by_id(conn, staff_id)
    t = time.fromisoformat(_hhmm(at, "Time")) if at else None
    if not schedule.normalize_phone(phone):
        raise BookingError("Enter a valid phone number so we can reach them.")
    entry = schedule.add_to_waitlist(conn, customer_name, s, _day(day), moment, at=t, period=period or None, staff=b, phone=phone)
    schedule.upsert_customer(conn, customer_name, phone)
    return entry


def remove_waitlist(conn, entry_id: int) -> None:
    conn.execute("UPDATE waitlist SET status = 'removed' WHERE id = ? AND status = 'waiting'", (entry_id,))
    conn.commit()


def add_block(conn, moment: datetime, staff_id, day: str, start: str, end: str, reason: str = "") -> dict:
    b = _staff_by_id(conn, staff_id)
    if b is None:
        raise BookingError("Pick a team member.")
    return schedule.block_time(conn, b, _day(day), moment, time.fromisoformat(_hhmm(start, "Start")),
                               time.fromisoformat(_hhmm(end, "End")), None, reason or "")


def remove_block(conn, block_id: int) -> None:
    conn.execute("DELETE FROM blocks WHERE id = ?", (block_id,))
    conn.commit()


# ---------------------------------------------------------------- numbers for the overview

def stats(conn, moment: datetime) -> dict:
    today = moment.date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=7)

    def count(where: str, args: tuple) -> int:
        return conn.execute(f"SELECT COUNT(*) FROM appointments a WHERE {where}", args).fetchone()[0]

    def revenue(where: str, args: tuple) -> int:
        return conn.execute(f"SELECT COALESCE(SUM(s.price_cents), 0) FROM appointments a JOIN services s ON s.id = a.service_id "
                            f"WHERE {where}", args).fetchone()[0]

    day_q = "substr(a.start, 1, 10) = ? AND a.status IN ('confirmed', 'completed')"
    week_q = "a.start >= ? AND a.start < ? AND a.status IN ('confirmed', 'completed')"
    week_args = (week_start.isoformat(), week_end.isoformat())
    by_source = {r["source"]: r["n"] for r in conn.execute(
        "SELECT source, COUNT(*) AS n FROM appointments a WHERE a.created_at >= date('now', '-30 day') "
        "AND source IN ('assistant', 'waitlist', 'manual') GROUP BY source")}
    booked_30 = sum(by_source.values())
    return {
        "today": {"appointments": count(day_q, (today.isoformat(),)), "revenue_cents": revenue(day_q, (today.isoformat(),))},
        "week": {"appointments": count(week_q, week_args), "revenue_cents": revenue(week_q, week_args)},
        "waitlist_waiting": conn.execute("SELECT COUNT(*) FROM waitlist WHERE status = 'waiting' AND day >= ?",
                                         (today.isoformat(),)).fetchone()[0],
        "waitlist_filled": conn.execute("SELECT COUNT(*) FROM waitlist WHERE status = 'booked'").fetchone()[0],
        "customers": conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
        "booked_by_assistant_30d": by_source.get("assistant", 0) + by_source.get("waitlist", 0),
        "booked_total_30d": booked_30,
        "no_shows_30d": count("a.status = 'no_show' AND a.start >= ?", ((today - timedelta(days=30)).isoformat(),)),
    }
