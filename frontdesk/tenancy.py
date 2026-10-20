"""Many businesses on one Front Desk: owner accounts, businesses, sign-in sessions and API keys.

The platform database (platform.db, next to the demo database) lists accounts and businesses.
Each business keeps its schedule in its own SQLite file, so one business never sees another's data.
The business being served is a context variable, set per request (web, MCP endpoint or agent).
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from frontdesk import business_types, db

DEMO_SLUG = "main-street-barbers"
DEMO_EMAIL = "demo@frontdesk.dev"
SESSION_DAYS = 14
MIN_PASSWORD = 8

PLATFORM_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES accounts(id),
    db_file TEXT NOT NULL DEFAULT '',   -- empty: the demo database (FRONTDESK_DB)
    api_key_hash TEXT NOT NULL DEFAULT '',
    public_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    expires_at REAL NOT NULL
);
"""


class AuthError(ValueError):
    """Message safe to show on the sign-in / sign-up form."""


@dataclass(frozen=True)
class Business:
    id: int
    slug: str
    owner_id: int
    db_file: str
    public_demo: bool

    @property
    def db_path(self) -> Path:
        return Path(self.db_file) if self.db_file else db.DB_PATH

    def info(self) -> dict:
        """Name, time zone and type, from the business's own database."""
        with closing(db.connect(self.db_path)) as conn:
            db.init_db(conn)
            info = db.business_info(conn)
        kind = business_types.get(info["type"])
        return {**info, "slug": self.slug, "type_label": kind["label"], "staff_noun": kind["staff"],
                "staff_plural": kind["staff_plural"], "customer_noun": kind["customer"], "public_demo": self.public_demo}


# ---------------------------------------------------------------- the business being served

_current: contextvars.ContextVar[Business | None] = contextvars.ContextVar("frontdesk_business", default=None)
_owner: contextvars.ContextVar[bool] = contextvars.ContextVar("frontdesk_owner", default=False)


def current() -> Business:
    return _current.get() or demo_business()


def current_db_path() -> Path:
    b = _current.get()
    return b.db_path if b else db.DB_PATH


def is_owner() -> bool:
    """True when the request comes from the business's owner (signed in, or the MCP API key), or for the public demo."""
    return _owner.get() or current().public_demo


@contextmanager
def serving(business: Business, owner: bool = False):
    t1, t2 = _current.set(business), _owner.set(owner)
    try:
        yield business
    finally:
        _current.reset(t1)
        _owner.reset(t2)


def set_serving(business: Business, owner: bool = False) -> None:
    """For middleware: sets the business for the rest of this request's context."""
    _current.set(business)
    _owner.set(owner)


# ---------------------------------------------------------------- platform database

class closing:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self.conn

    def __exit__(self, *exc) -> None:
        self.conn.close()


def platform_path() -> Path:
    return db.DB_PATH.parent / "platform.db"


def businesses_dir() -> Path:
    return db.DB_PATH.parent / "businesses"


def _platform() -> closing:
    path = platform_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(PLATFORM_SCHEMA)
    return closing(conn)


def _row_to_business(r: sqlite3.Row) -> Business:
    return Business(id=r["id"], slug=r["slug"], owner_id=r["owner_id"], db_file=r["db_file"], public_demo=bool(r["public_demo"]))


def demo_business() -> Business:
    """The public demo barbershop (always exists; its owner account is the demo account)."""
    with _platform() as conn:
        r = conn.execute("SELECT * FROM businesses WHERE slug = ?", (DEMO_SLUG,)).fetchone()
        if r is None:
            owner = conn.execute("SELECT id FROM accounts WHERE email = ?", (DEMO_EMAIL,)).fetchone()
            owner_id = owner["id"] if owner else conn.execute(
                "INSERT INTO accounts (email, name, password_hash) VALUES (?, ?, ?)",
                (DEMO_EMAIL, "Demo owner", "!"),  # no password: entered through "Try the demo"
            ).lastrowid
            conn.execute("INSERT OR IGNORE INTO businesses (slug, owner_id, db_file, public_demo) VALUES (?, ?, '', 1)",
                         (DEMO_SLUG, owner_id))
            conn.commit()
            r = conn.execute("SELECT * FROM businesses WHERE slug = ?", (DEMO_SLUG,)).fetchone()
        return _row_to_business(r)


def business_by_slug(slug: str) -> Business | None:
    if slug == DEMO_SLUG:
        return demo_business()
    with _platform() as conn:
        r = conn.execute("SELECT * FROM businesses WHERE slug = ?", (slug,)).fetchone()
    return _row_to_business(r) if r else None


def businesses_of(account_id: int) -> list[Business]:
    demo_business()  # make sure it exists
    with _platform() as conn:
        rows = conn.execute("SELECT * FROM businesses WHERE owner_id = ? ORDER BY id", (account_id,)).fetchall()
    return [_row_to_business(r) for r in rows]


def slugify(name: str) -> str:
    t = unicodedata.normalize("NFD", name.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:40].strip("-") or "business"


def create_business(owner_id: int, name: str, kind: str, timezone: str) -> Business:
    name = " ".join((name or "").split())
    if not name:
        raise AuthError("Give your business a name.")
    if kind not in business_types.TYPES:
        raise AuthError("Pick a type of business.")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(timezone)
    except Exception:
        raise AuthError("Pick a valid time zone.") from None
    base = slugify(name)
    with _platform() as conn:
        taken = {r["slug"] for r in conn.execute("SELECT slug FROM businesses WHERE slug LIKE ?", (base + "%",))}
        slug, n = base, 2
        while slug in taken or slug == DEMO_SLUG:
            slug, n = f"{base}-{n}", n + 1
        folder = businesses_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{slug}.db"
        with closing(db.connect(path)) as bconn:
            db.init_db(bconn, name=name, timezone=timezone, kind=kind)
        cur = conn.execute("INSERT INTO businesses (slug, owner_id, db_file) VALUES (?, ?, ?)", (slug, owner_id, str(path)))
        conn.commit()
        r = conn.execute("SELECT * FROM businesses WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_business(r)


# ---------------------------------------------------------------- accounts and sessions

def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def _check_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    return hmac.compare_digest(_hash_password(password, bytes.fromhex(salt)), stored)


def _clean_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise AuthError("Enter a valid email address.")
    return email


def create_account(email: str, name: str, password: str) -> int:
    email = _clean_email(email)
    name = " ".join((name or "").split())
    if not name:
        raise AuthError("Enter your name.")
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"Use a password with at least {MIN_PASSWORD} characters.")
    with _platform() as conn:
        if conn.execute("SELECT 1 FROM accounts WHERE email = ?", (email,)).fetchone():
            raise AuthError("There's already an account with this email. Sign in instead.")
        cur = conn.execute("INSERT INTO accounts (email, name, password_hash) VALUES (?, ?, ?)",
                           (email, name, _hash_password(password)))
        conn.commit()
        return cur.lastrowid


_failed_logins: dict[str, list[float]] = {}


def authenticate(email: str, password: str) -> int:
    key = (email or "").strip().lower()
    t = time.time()
    recent = [x for x in _failed_logins.get(key, []) if t - x < 900]
    if len(recent) >= 5:
        raise AuthError("Too many attempts. Wait a few minutes and try again.")
    with _platform() as conn:
        r = conn.execute("SELECT id, password_hash FROM accounts WHERE email = ?", (key,)).fetchone()
    if not r or not _check_password(password or "", r["password_hash"]):
        _failed_logins[key] = recent + [t]
        raise AuthError("Wrong email or password.")
    _failed_logins.pop(key, None)
    return r["id"]


def account(account_id: int) -> dict | None:
    with _platform() as conn:
        r = conn.execute("SELECT id, email, name FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return dict(r) if r else None


def demo_account_id() -> int:
    return demo_business().owner_id


def start_session(account_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with _platform() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        conn.execute("INSERT INTO sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
                     (_sha(token), account_id, time.time() + SESSION_DAYS * 86400))
        conn.commit()
    return token


def session_account(token: str | None) -> int | None:
    if not token:
        return None
    with _platform() as conn:
        r = conn.execute("SELECT account_id FROM sessions WHERE token_hash = ? AND expires_at > ?",
                         (_sha(token), time.time())).fetchone()
    return r["account_id"] if r else None


def end_session(token: str | None) -> None:
    if token:
        with _platform() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha(token),))
            conn.commit()


# ---------------------------------------------------------------- MCP API keys (owner access from Alexa+ or other clients)

def new_api_key(business: Business) -> str:
    key = "fd_" + secrets.token_urlsafe(24)
    with _platform() as conn:
        conn.execute("UPDATE businesses SET api_key_hash = ? WHERE id = ?", (_sha(key), business.id))
        conn.commit()
    return key


def has_api_key(business: Business) -> bool:
    with _platform() as conn:
        r = conn.execute("SELECT api_key_hash FROM businesses WHERE id = ?", (business.id,)).fetchone()
    return bool(r and r["api_key_hash"])


def api_key_matches(business: Business, key: str | None) -> bool:
    if not key:
        return False
    with _platform() as conn:
        r = conn.execute("SELECT api_key_hash FROM businesses WHERE id = ?", (business.id,)).fetchone()
    return bool(r and r["api_key_hash"]) and hmac.compare_digest(r["api_key_hash"], _sha(key))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
