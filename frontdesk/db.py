"""Storage of one business in SQLite: business, services, staff, hours, customers, appointments, waitlist, blocks.

Each business has its own database file (see frontdesk.tenancy); the platform database keeps the
accounts and the list of businesses.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from frontdesk import business_types

DB_PATH = Path(os.environ.get("FRONTDESK_DB", Path(__file__).resolve().parent.parent / "frontdesk.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS business (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    timezone TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'barbershop'
);
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    duration_min INTEGER NOT NULL,
    price_cents INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1
);
-- weekday: 0 = Monday ... 6 = Sunday (same as datetime.weekday()); a missing weekday means closed
CREATE TABLE IF NOT EXISTS opening_hours (
    weekday INTEGER PRIMARY KEY,
    opens TEXT NOT NULL,
    closes TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,  -- normalized, e.g. +13105550142
    email TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY,
    customer TEXT NOT NULL,
    service_id INTEGER NOT NULL REFERENCES services(id),
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    start TEXT NOT NULL,  -- local ISO time, e.g. 2026-10-23T09:00
    end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed',  -- confirmed | completed | cancelled | no_show
    source TEXT NOT NULL DEFAULT 'assistant',  -- assistant | waitlist | manual | history
    customer_id INTEGER REFERENCES customers(id),
    phone TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_appointments_start ON appointments(start);
-- customers waiting for a slot to open up; filled automatically when someone cancels
CREATE TABLE IF NOT EXISTS waitlist (
    id INTEGER PRIMARY KEY,
    customer TEXT NOT NULL,
    service_id INTEGER NOT NULL REFERENCES services(id),
    staff_id INTEGER REFERENCES staff(id),  -- NULL = anyone
    day TEXT NOT NULL,                      -- YYYY-MM-DD
    earliest TEXT NOT NULL,                 -- HH:MM, first acceptable start
    latest TEXT NOT NULL,                   -- HH:MM, last acceptable start
    status TEXT NOT NULL DEFAULT 'waiting', -- waiting | booked | removed
    appointment_id INTEGER REFERENCES appointments(id),
    phone TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- time the owner takes off someone's schedule (day off, lunch, errands)
CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# columns added after the first version: (table, column, definition)
MIGRATIONS = [
    ("business", "type", "TEXT NOT NULL DEFAULT 'barbershop'"),
    ("services", "active", "INTEGER NOT NULL DEFAULT 1"),
    ("staff", "active", "INTEGER NOT NULL DEFAULT 1"),
    ("appointments", "source", "TEXT NOT NULL DEFAULT 'assistant'"),
    ("appointments", "customer_id", "INTEGER REFERENCES customers(id)"),
    ("appointments", "phone", "TEXT NOT NULL DEFAULT ''"),
    ("appointments", "notes", "TEXT NOT NULL DEFAULT ''"),
    ("waitlist", "phone", "TEXT NOT NULL DEFAULT ''"),
]

# Fictional barbershop used in the demo
SEED_BUSINESS = ("Main Street Barbers", "America/Los_Angeles")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    if path is None:
        from frontdesk import tenancy  # the database of the business being served right now
        path = tenancy.current_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection, name: str | None = None, timezone: str | None = None, kind: str | None = None) -> None:
    """Creates the tables (and adds newer columns to older databases). An empty database gets a starter setup:
    the demo barbershop by default, or the given name, time zone and business type."""
    conn.executescript(SCHEMA)
    for table, column, definition in MIGRATIONS:
        if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    if conn.execute("SELECT COUNT(*) FROM business").fetchone()[0] == 0:
        kind = kind or business_types.DEFAULT
        template = business_types.get(kind)
        conn.execute("INSERT INTO business (id, name, timezone, type) VALUES (1, ?, ?, ?)",
                     (name or SEED_BUSINESS[0], timezone or SEED_BUSINESS[1], kind))
        conn.executemany("INSERT INTO services (name, duration_min, price_cents) VALUES (?, ?, ?)", template["services"])
        conn.executemany("INSERT INTO staff (name) VALUES (?)", [(n,) for n in template["team"]])
        conn.executemany("INSERT INTO opening_hours (weekday, opens, closes) VALUES (?, ?, ?)", template["hours"])
    conn.commit()


def list_services(conn: sqlite3.Connection, include_inactive: bool = False) -> list[dict]:
    where = "" if include_inactive else "WHERE active = 1"
    rows = conn.execute(f"SELECT id, name, duration_min, price_cents, active FROM services {where} ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def business_name(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT name FROM business WHERE id = 1").fetchone()["name"]


def business_info(conn: sqlite3.Connection) -> dict:
    return dict(conn.execute("SELECT name, timezone, type FROM business WHERE id = 1").fetchone())
