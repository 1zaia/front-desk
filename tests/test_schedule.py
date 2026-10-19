"""Booking rules, with the clock pinned to Monday, October 19, 2026 at 8 AM."""
from datetime import date, datetime, time

import pytest

from frontdesk import db, schedule
from frontdesk.schedule import BookingError

NOW = datetime(2026, 10, 19, 8, 0)  # Monday
TODAY = NOW.date()


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def haircut(conn):
    return schedule.find_service(conn, "haircut")


# ------------------------------------------------------------ parsing

@pytest.mark.parametrize(
    "text, expected",
    [
        ("today", date(2026, 10, 19)),
        ("tomorrow", date(2026, 10, 20)),
        ("the day after tomorrow", date(2026, 10, 21)),
        ("Friday", date(2026, 10, 23)),
        ("this friday", date(2026, 10, 23)),
        ("on Wednesday", date(2026, 10, 21)),
        ("fri", date(2026, 10, 23)),
        ("monday", date(2026, 10, 19)),
        ("next monday", date(2026, 10, 26)),
        ("Saturday", date(2026, 10, 24)),
        ("2026-10-23", date(2026, 10, 23)),
        ("10/23", date(2026, 10, 23)),
        ("1/5", date(2027, 1, 5)),
        ("10/23/2026", date(2026, 10, 23)),
        ("October 23", date(2026, 10, 23)),
        ("Oct 23rd", date(2026, 10, 23)),
        ("the 23rd", date(2026, 10, 23)),
        ("23rd of October", date(2026, 10, 23)),
        ("the 2nd", date(2026, 11, 2)),
    ],
)
def test_parse_date(text, expected):
    assert schedule.parse_date(text, TODAY) == expected


def test_parse_date_invalid():
    with pytest.raises(BookingError):
        schedule.parse_date("sometime next week maybe", TODAY)


@pytest.mark.parametrize(
    "text, expected",
    [("9", time(9)), ("9am", time(9)), ("9 a.m.", time(9)), ("9:30", time(9, 30)), ("9:30 PM", time(21, 30)),
     ("14:00", time(14)), ("3", time(15)), ("at 3", time(15)), ("noon", time(12)), ("12 pm", time(12)),
     ("half past 9", time(9, 30)), ("nine thirty", time(9, 30)), ("2 o'clock", time(14)), ("10:30 AM", time(10, 30))],
)
def test_parse_time(text, expected):
    assert schedule.parse_time(text) == expected


@pytest.mark.parametrize(
    "h, spoken",
    [(time(9), "9 AM"), (time(9, 30), "9:30 AM"), (time(12), "noon"), (time(12, 30), "12:30 PM"), (time(16), "4 PM")],
)
def test_say_time(h, spoken):
    assert schedule.say_time(h) == spoken


def test_say_date():
    assert schedule.say_date(date(2026, 10, 19), TODAY) == "today"
    assert schedule.say_date(date(2026, 10, 20), TODAY) == "tomorrow"
    assert schedule.say_date(date(2026, 10, 23), TODAY) == "Friday, October 23"
    assert schedule.say_date_on(date(2026, 10, 23), TODAY) == "on Friday, October 23"


@pytest.mark.parametrize(
    "text, expected",
    [("haircut", "Haircut"), ("a haircut", "Haircut"), ("cut", "Haircut"), ("Beard", "Beard trim"),
     ("haircut and beard", "Haircut and beard"), ("haircut + beard", "Haircut and beard"),
     ("cut and beard", "Haircut and beard"), ("shave", "Hot towel shave"), ("hot towel shave", "Hot towel shave")],
)
def test_find_service(conn, text, expected):
    assert schedule.find_service(conn, text).name == expected


def test_unknown_service(conn):
    with pytest.raises(BookingError, match="We have"):
        schedule.find_service(conn, "massage")


def test_unknown_barber(conn):
    with pytest.raises(BookingError, match="Our barbers are Marcus and Diego"):
        schedule.find_staff(conn, "Bob")
    assert schedule.find_staff(conn, "anyone") is None


# ------------------------------------------------------------ open slots

def test_open_slots_morning(conn):
    slots = schedule.open_slots(conn, date(2026, 10, 23), haircut(conn), NOW, "morning")
    times = {x["time"] for x in slots}
    assert "09:00" in times and "11:30" in times
    assert all(x["time"] < "12:00" for x in slots)
    assert {x["staff"] for x in slots} == {"Marcus", "Diego"}


def test_slots_respect_closing_time(conn):
    # Saturday closes at 2 PM: a 30-minute haircut starts at 1:30 PM at the latest
    slots = schedule.open_slots(conn, date(2026, 10, 24), haircut(conn), NOW)
    assert max(x["time"] for x in slots) == "13:30"
    assert schedule.open_slots(conn, date(2026, 10, 25), haircut(conn), NOW) == []  # Sunday


def test_slots_skip_the_past(conn):
    moment = datetime(2026, 10, 19, 15, 10)
    slots = schedule.open_slots(conn, TODAY, haircut(conn), moment)
    assert min(x["time"] for x in slots) == "15:30"


def test_booked_slot_disappears(conn):
    marcus = schedule.find_staff(conn, "marcus")
    schedule.book(conn, "Ana", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW, marcus)
    slots = schedule.open_slots(conn, date(2026, 10, 23), haircut(conn), NOW, "morning", marcus)
    assert "09:00" not in {x["time"] for x in slots}
    assert "09:30" in {x["time"] for x in slots}


# ------------------------------------------------------------ booking

def test_book_picks_a_free_barber(conn):
    s = haircut(conn)
    a1 = schedule.book(conn, "john smith", s, datetime(2026, 10, 23, 9, 0), NOW)
    a2 = schedule.book(conn, "Maria", s, datetime(2026, 10, 23, 9, 0), NOW)
    assert a1["customer"] == "John Smith"
    assert {a1["staff"], a2["staff"]} == {"Marcus", "Diego"}
    with pytest.raises(BookingError, match="already taken"):
        schedule.book(conn, "Peter", s, datetime(2026, 10, 23, 9, 0), NOW)


def test_book_blocks_overlap(conn):
    combo = schedule.find_service(conn, "haircut and beard")  # 50 min
    marcus = schedule.find_staff(conn, "Marcus")
    schedule.book(conn, "Ana", combo, datetime(2026, 10, 23, 10, 0), NOW, marcus)
    with pytest.raises(BookingError):
        schedule.book(conn, "Bea", haircut(conn), datetime(2026, 10, 23, 10, 30), NOW, marcus)
    schedule.book(conn, "Bea", haircut(conn), datetime(2026, 10, 23, 11, 0), NOW, marcus)  # fits after


@pytest.mark.parametrize(
    "start, fragment",
    [
        (datetime(2026, 10, 25, 10, 0), "closed"),
        (datetime(2026, 10, 23, 7, 0), "open from"),
        (datetime(2026, 10, 24, 13, 45), "open from"),
        (datetime(2026, 10, 19, 7, 0), "open from"),
    ],
)
def test_book_outside_opening_hours(conn, start, fragment):
    with pytest.raises(BookingError, match=fragment):
        schedule.book(conn, "Ana", haircut(conn), start, NOW)


def test_book_in_the_past(conn):
    with pytest.raises(BookingError, match="passed"):
        schedule.book(conn, "Ana", haircut(conn), datetime(2026, 10, 19, 9, 0), datetime(2026, 10, 19, 10, 0))


# ------------------------------------------------------------ cancel and reschedule

def test_cancel_frees_the_slot(conn):
    marcus = schedule.find_staff(conn, "Marcus")
    appt = schedule.book(conn, "Ana Paula", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW, marcus)
    found = schedule.find_appointments(conn, "ana", NOW)
    assert [a["id"] for a in found] == [appt["id"]]
    assert schedule.cancel(conn, appt["id"])["status"] == "cancelled"
    assert schedule.find_appointments(conn, "ana", NOW) == []
    schedule.book(conn, "Bea", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW, marcus)


def test_reschedule_keeps_the_barber(conn):
    diego = schedule.find_staff(conn, "Diego")
    appt = schedule.book(conn, "Caleb", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW, diego)
    moved = schedule.reschedule(conn, appt["id"], datetime(2026, 10, 23, 16, 0), NOW)
    assert moved["start"] == "2026-10-23T16:00"
    assert moved["staff"] == "Diego"


def test_reschedule_to_the_same_slot_does_not_conflict_with_itself(conn):
    appt = schedule.book(conn, "Caleb", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW)
    moved = schedule.reschedule(conn, appt["id"], datetime(2026, 10, 23, 9, 0), NOW)
    assert moved["staff"] == appt["staff"]


# ------------------------------------------------------------ summary and demo data

def test_daily_summary(conn):
    schedule.book(conn, "Ana", haircut(conn), datetime(2026, 10, 19, 10, 0), NOW)
    schedule.book(conn, "Bea", schedule.find_service(conn, "beard"), datetime(2026, 10, 19, 11, 0), NOW)
    r = schedule.daily_summary(conn, TODAY, NOW)
    assert r["total_customers"] == 2
    assert r["expected_revenue_cents"] == 3500 + 2000
    assert r["next"]["customer"] == "Ana"


def test_seed_demo_only_once(conn):
    schedule.seed_demo(conn, TODAY)
    n = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
    assert n > 0
    schedule.seed_demo(conn, TODAY)
    assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == n
