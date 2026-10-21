"""Booking rules, with the clock pinned to Monday, October 19, 2026 at 8 AM."""
from datetime import date, datetime, time, timedelta

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


_phones = iter(range(1000, 9999))


def book(*args, **kwargs):
    """schedule.book with a phone number (always required for assistant bookings), one per call."""
    kwargs.setdefault("phone", f"+1310555{next(_phones):04d}")
    return schedule.book(*args, **kwargs)


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
    book(conn, "Ana", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW, marcus)
    slots = schedule.open_slots(conn, date(2026, 10, 23), haircut(conn), NOW, "morning", marcus)
    assert "09:00" not in {x["time"] for x in slots}
    assert "09:30" in {x["time"] for x in slots}


# ------------------------------------------------------------ booking

def test_book_picks_a_free_barber(conn):
    s = haircut(conn)
    a1 = book(conn, "john smith", s, datetime(2026, 10, 23, 9, 0), NOW)
    a2 = book(conn, "Maria", s, datetime(2026, 10, 23, 9, 0), NOW)
    assert a1["customer"] == "John Smith"
    assert {a1["staff"], a2["staff"]} == {"Marcus", "Diego"}
    with pytest.raises(BookingError, match="already taken"):
        book(conn, "Peter", s, datetime(2026, 10, 23, 9, 0), NOW)


def test_book_blocks_overlap(conn):
    combo = schedule.find_service(conn, "haircut and beard")  # 50 min
    marcus = schedule.find_staff(conn, "Marcus")
    book(conn, "Ana", combo, datetime(2026, 10, 23, 10, 0), NOW, marcus)
    with pytest.raises(BookingError):
        book(conn, "Bea", haircut(conn), datetime(2026, 10, 23, 10, 30), NOW, marcus)
    book(conn, "Bea", haircut(conn), datetime(2026, 10, 23, 11, 0), NOW, marcus)  # fits after


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
        book(conn, "Ana", haircut(conn), start, NOW)


def test_book_in_the_past(conn):
    with pytest.raises(BookingError, match="passed"):
        book(conn, "Ana", haircut(conn), datetime(2026, 10, 19, 9, 0), datetime(2026, 10, 19, 10, 0))


# ------------------------------------------------------------ cancel and reschedule

def test_cancel_frees_the_slot(conn):
    marcus = schedule.find_staff(conn, "Marcus")
    appt = book(conn, "Ana Paula", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW, marcus)
    found = schedule.find_appointments(conn, "ana", NOW)
    assert [a["id"] for a in found] == [appt["id"]]
    assert schedule.cancel(conn, appt["id"])["status"] == "cancelled"
    assert schedule.find_appointments(conn, "ana", NOW) == []
    book(conn, "Bea", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW, marcus)


def test_reschedule_keeps_the_barber(conn):
    diego = schedule.find_staff(conn, "Diego")
    appt = book(conn, "Caleb", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW, diego)
    moved = schedule.reschedule(conn, appt["id"], datetime(2026, 10, 23, 16, 0), NOW)
    assert moved["start"] == "2026-10-23T16:00"
    assert moved["staff"] == "Diego"


def test_reschedule_to_the_same_slot_does_not_conflict_with_itself(conn):
    appt = book(conn, "Caleb", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW)
    moved = schedule.reschedule(conn, appt["id"], datetime(2026, 10, 23, 9, 0), NOW)
    assert moved["staff"] == appt["staff"]


# ------------------------------------------------------------ summary and demo data

def test_daily_summary(conn):
    book(conn, "Ana", haircut(conn), datetime(2026, 10, 19, 10, 0), NOW)
    book(conn, "Bea", schedule.find_service(conn, "beard"), datetime(2026, 10, 19, 11, 0), NOW)
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


# ------------------------------------------------------------ waitlist

def test_waitlist_books_the_freed_slot_first(conn):
    s = haircut(conn)
    friday = datetime(2026, 10, 23, 10, 0)
    a = book(conn, "Ana", s, friday, NOW)
    book(conn, "Bea", s, friday, NOW)
    entry = schedule.add_to_waitlist(conn, "sam torres", s, friday.date(), NOW, period="morning")
    assert entry["customer"] == "Sam Torres" and entry["earliest"] == "09:00" and entry["latest"] == "12:00"
    schedule.cancel(conn, a["id"])
    filled = schedule.fill_from_waitlist(conn, friday.date(), NOW, freed=friday)
    assert [(x["customer"], x["start"], x["source"]) for x in filled] == [("Sam Torres", "2026-10-23T10:00", "waitlist")]
    assert schedule.waitlist_on(conn, friday.date())[0]["status"] == "booked"
    assert schedule.fill_from_waitlist(conn, friday.date(), NOW) == []  # nobody left waiting


def test_waitlist_for_an_exact_time_waits_for_that_time(conn):
    s = haircut(conn)
    friday = date(2026, 10, 23)
    marcus, diego = schedule.find_staff(conn, "Marcus"), schedule.find_staff(conn, "Diego")
    book(conn, "Ana", s, datetime(2026, 10, 23, 10, 0), NOW, marcus)
    book(conn, "Bea", s, datetime(2026, 10, 23, 10, 0), NOW, diego)
    later = book(conn, "Cal", s, datetime(2026, 10, 23, 15, 0), NOW, marcus)
    schedule.add_to_waitlist(conn, "Sam", s, friday, NOW, at=time(10, 0))
    schedule.cancel(conn, later["id"])
    assert schedule.fill_from_waitlist(conn, friday, NOW) == []  # 3 PM opened, but Sam only wants 10 AM


def test_waitlist_on_a_closed_day(conn):
    with pytest.raises(BookingError, match="closed"):
        schedule.add_to_waitlist(conn, "Sam", haircut(conn), date(2026, 10, 25), NOW)


# ------------------------------------------------------------ returning customers

def test_customer_profile(conn):
    schedule.seed_demo(conn, TODAY)
    p = schedule.customer_profile(conn, "chris", NOW)
    assert p["customer"] == "Chris Miller" and p["visits"] == 3
    assert p["usual_service"] == "Haircut" and p["usual_staff"] == "Marcus"
    assert p["upcoming"] and p["upcoming"][0]["start"].startswith("2026-10-19T10:00")
    assert schedule.customer_profile(conn, "Nobody", NOW) is None
    assert schedule.customer_profile(conn, "an", NOW) is None  # matches whole words, not pieces of names
    assert schedule.is_usual("The usual") and schedule.is_usual("same as last time") and not schedule.is_usual("haircut")


# ------------------------------------------------------------ owner blocks

def test_block_time_removes_slots_and_reports_conflicts(conn):
    diego = schedule.find_staff(conn, "Diego")
    thursday = date(2026, 10, 22)
    book(conn, "Tom", haircut(conn), datetime(2026, 10, 22, 14, 0), NOW, diego)
    block = schedule.block_time(conn, diego, thursday, NOW, period="afternoon", reason="dentist")
    assert (block["start"], block["end"]) == ("2026-10-22T12:00", "2026-10-22T17:00")
    assert [a["customer"] for a in block["conflicts"]] == ["Tom"]
    slots = schedule.open_slots(conn, thursday, haircut(conn), NOW, "afternoon", diego)
    assert slots == []
    assert schedule.blocks_on(conn, thursday)[0]["reason"] == "dentist"
    with pytest.raises(BookingError, match="already taken"):
        book(conn, "Ana", haircut(conn), datetime(2026, 10, 22, 15, 0), NOW, diego)


def test_block_time_with_explicit_hours(conn):
    marcus = schedule.find_staff(conn, "Marcus")
    block = schedule.block_time(conn, marcus, date(2026, 10, 23), NOW, start=time(13, 0), end=time(15, 0))
    assert (block["start"], block["end"], block["conflicts"]) == ("2026-10-23T13:00", "2026-10-23T15:00", [])
    with pytest.raises(BookingError, match="after the start"):
        schedule.block_time(conn, marcus, date(2026, 10, 23), NOW, start=time(15, 0), end=time(13, 0))


# ------------------------------------------------------------ customers and phone numbers

@pytest.mark.parametrize("text, expected", [
    ("(310) 555-0142", "+13105550142"),
    ("310.555.0142", "+13105550142"),
    ("+1 310 555 0142", "+13105550142"),
    ("13105550142", "+13105550142"),
    ("three one zero five five five zero one four two", "+13105550142"),
    ("+44 20 7946 0958", "+442079460958"),
    ("555-0142", ""),
    ("", ""),
])
def test_normalize_phone(text, expected):
    assert schedule.normalize_phone(text) == expected


def test_assistant_booking_needs_name_and_phone(conn):
    with pytest.raises(BookingError, match="phone number"):
        schedule.book(conn, "Ana", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW)
    with pytest.raises(BookingError, match="name"):
        schedule.book(conn, " ", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW, phone="3105550111")
    # the owner can add a walk-in by hand without a number
    walk_in = schedule.book(conn, "Ana", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW, source="manual")
    assert walk_in["phone"] == "" and walk_in["customer_id"] is None


def test_booking_creates_and_reuses_the_customer(conn):
    a = schedule.book(conn, "sam", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW, phone="(310) 555-0142")
    b = schedule.book(conn, "Sam Torres", haircut(conn), datetime(2026, 10, 24, 9, 0), NOW, phone="310 555 0142")
    assert a["customer_id"] == b["customer_id"] and a["phone"] == "+13105550142"
    assert schedule.customer_by_phone(conn, "3105550142")["name"] == "Sam Torres"  # the fuller name wins
    # someone else on a shared family phone keeps their own name on the appointment
    c = schedule.book(conn, "Ana", haircut(conn), datetime(2026, 10, 24, 10, 0), NOW, phone="3105550142")
    assert c["customer"] == "Ana" and c["customer_id"] == a["customer_id"]


def test_profile_by_phone(conn):
    s = haircut(conn)
    past = datetime(2026, 10, 12, 9, 0)
    schedule.book(conn, "Sam Torres", s, past, past - timedelta(days=1), schedule.find_staff(conn, "Diego"), phone="3105550142")
    p = schedule.customer_profile(conn, None, NOW, phone="(310) 555-0142")
    assert p["customer"] == "Sam Torres" and p["visits"] == 1 and p["usual_staff"] == "Diego"
    assert p["phone"] == "+13105550142"
    assert schedule.customer_profile(conn, None, NOW, phone="3105559999") is None


def test_find_appointments_by_phone(conn):
    a = book(conn, "Leo", haircut(conn), datetime(2026, 10, 23, 9, 0), NOW, phone="3105550177")
    book(conn, "Leo", haircut(conn), datetime(2026, 10, 23, 11, 0), NOW, phone="3105550178")
    found = schedule.find_appointments(conn, None, NOW, phone="(310) 555-0177")
    assert [x["id"] for x in found] == [a["id"]]


def test_other_business_types_use_their_own_words(tmp_path):
    c = db.connect(tmp_path / "salon.db")
    db.init_db(c, name="Glow Studio", timezone="America/New_York", kind="hair_salon")
    assert [s["name"] for s in db.list_services(c)][:2] == ["Women's haircut", "Men's haircut"]
    with pytest.raises(BookingError, match="Our stylists are Ava and Mia"):
        schedule.find_staff(c, "Marcus")
    assert schedule.find_service(c, "color").name == "Color"
    c.close()
