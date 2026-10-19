"""Front Desk storage in SQLite: business, services, staff, opening hours and appointments."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("FRONTDESK_DB", Path(__file__).resolve().parent.parent / "frontdesk.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS business (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    timezone TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    duration_min INTEGER NOT NULL,
    price_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
-- weekday: 0 = Monday ... 6 = Sunday (same as datetime.weekday())
CREATE TABLE IF NOT EXISTS opening_hours (
    weekday INTEGER PRIMARY KEY,
    opens TEXT NOT NULL,
    closes TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY,
    customer TEXT NOT NULL,
    service_id INTEGER NOT NULL REFERENCES services(id),
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    start TEXT NOT NULL,  -- local ISO time, e.g. 2026-10-23T09:00
    end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed',
    source TEXT NOT NULL DEFAULT 'assistant',  -- assistant | waitlist | history
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_appointments_start ON appointments(start);
-- customers waiting for a slot to open up; filled automatically when someone cancels
CREATE TABLE IF NOT EXISTS waitlist (
    id INTEGER PRIMARY KEY,
    customer TEXT NOT NULL,
    service_id INTEGER NOT NULL REFERENCES services(id),
    staff_id INTEGER REFERENCES staff(id),  -- NULL = any barber
    day TEXT NOT NULL,                      -- YYYY-MM-DD
    earliest TEXT NOT NULL,                 -- HH:MM, first acceptable start
    latest TEXT NOT NULL,                   -- HH:MM, last acceptable start
    status TEXT NOT NULL DEFAULT 'waiting', -- waiting | booked
    appointment_id INTEGER REFERENCES appointments(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- time the owner takes off a barber's schedule (day off, lunch, errands)
CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Fictional barbershop used in the demo
SEED_BUSINESS = ("Main Street Barbers", "America/Los_Angeles")
SEED_SERVICES = [
    ("Haircut", 30, 3500),
    ("Beard trim", 20, 2000),
    ("Haircut and beard", 50, 5000),
    ("Hot towel shave", 40, 4000),
]
SEED_STAFF = ["Marcus", "Diego"]
SEED_HOURS = [
    (0, "09:00", "19:00"),
    (1, "09:00", "19:00"),
    (2, "09:00", "19:00"),
    (3, "09:00", "19:00"),
    (4, "09:00", "20:00"),
    (5, "08:00", "14:00"),
]


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(appointments)")}
    if "source" not in columns:  # databases created before the waitlist existed
        conn.execute("ALTER TABLE appointments ADD COLUMN source TEXT NOT NULL DEFAULT 'assistant'")
    if conn.execute("SELECT COUNT(*) FROM business").fetchone()[0] == 0:
        conn.execute("INSERT INTO business (id, name, timezone) VALUES (1, ?, ?)", SEED_BUSINESS)
        conn.executemany("INSERT INTO services (name, duration_min, price_cents) VALUES (?, ?, ?)", SEED_SERVICES)
        conn.executemany("INSERT INTO staff (name) VALUES (?)", [(n,) for n in SEED_STAFF])
        conn.executemany("INSERT INTO opening_hours (weekday, opens, closes) VALUES (?, ?, ?)", SEED_HOURS)
    conn.commit()


def list_services(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT name, duration_min, price_cents FROM services ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def business_name(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT name FROM business WHERE id = 1").fetchone()["name"]
