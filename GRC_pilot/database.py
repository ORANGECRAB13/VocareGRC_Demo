"""
database.py — Single-table SQLite persistence for GRC Bulky Waste agent.

Table: residents
  One row per customer. Bookings stored inline (max 2 per calendar year).

  customer_name   TEXT    — full name
  phone           TEXT    — unique, used as lookup key
  address         TEXT    — property address
  services_left   INTEGER — remaining collections this year (default 2)
  booking_1_ref   TEXT    — first booking reference (NULL if unused)
  booking_1_date  TEXT    — first booking preferred date
  booking_2_ref   TEXT    — second booking reference (NULL if unused)
  booking_2_date  TEXT    — second booking preferred date
  created_at      TEXT
"""

import sqlite3
import uuid
from pathlib import Path

DB_PATH      = Path(__file__).parent / "customers.db"
MAX_SERVICES = 2


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the residents table (drops legacy tables if present)."""
    with _connect() as conn:
        # Clean up old split-table schema if it exists
        conn.executescript("""
            DROP TABLE IF EXISTS bookings;
            DROP TABLE IF EXISTS customers;

            CREATE TABLE IF NOT EXISTS residents (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT    NOT NULL,
                phone         TEXT    NOT NULL UNIQUE,
                address       TEXT    NOT NULL,
                services_left INTEGER NOT NULL DEFAULT 2,
                booking_1_ref  TEXT    DEFAULT NULL,
                booking_1_date TEXT    DEFAULT NULL,
                booking_1_changes INTEGER NOT NULL DEFAULT 0,
                booking_2_ref  TEXT    DEFAULT NULL,
                booking_2_date TEXT    DEFAULT NULL,
                booking_2_changes INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT    DEFAULT (datetime('now'))
            );
        """)
        _ensure_columns(conn)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add new columns to existing DBs without destroying data."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(residents)")}
    if "booking_1_changes" not in cols:
        conn.execute(
            "ALTER TABLE residents ADD COLUMN booking_1_changes INTEGER NOT NULL DEFAULT 0"
        )
    if "booking_2_changes" not in cols:
        conn.execute(
            "ALTER TABLE residents ADD COLUMN booking_2_changes INTEGER NOT NULL DEFAULT 0"
        )




# ── Helpers ───────────────────────────────────────────────────────────────────

def get_resident(phone: str) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM residents WHERE phone = ?", (phone,)
        ).fetchone()


def create_resident(name: str, phone: str, address: str) -> sqlite3.Row:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO residents (customer_name, phone, address, services_left)
               VALUES (?, ?, ?, ?)""",
            (name, phone, address, MAX_SERVICES),
        )
    return get_resident(phone)


def add_booking(phone: str, preferred_date: str) -> str | None:
    """
    Add a booking to the resident's record and decrement services_left.
    Fills booking_1 first, then booking_2.
    Returns the booking reference, or None if no slots remain.
    """
    resident = get_resident(phone)
    if not resident or resident["services_left"] <= 0:
        return None

    ref = str(uuid.uuid4())[:8].upper()

    if resident["booking_1_ref"] is None:
        col_ref, col_date = "booking_1_ref", "booking_1_date"
    else:
        col_ref, col_date = "booking_2_ref", "booking_2_date"

    with _connect() as conn:
        conn.execute(
            f"""UPDATE residents
               SET {col_ref}  = ?,
                   {col_date} = ?,
                   services_left = MAX(0, services_left - 1)
               WHERE phone = ?""",
            (ref, preferred_date, phone),
        )
    return ref


def update_booking(phone: str, name: str, new_date: str) -> tuple[str, str | None]:
    """
    Update the most recent booking date for a resident.
    Returns (status, slot) where status is one of:
      ok, not_found, name_mismatch, no_booking, change_limit
    """
    resident = get_resident(phone)
    if not resident:
        return "not_found", None

    if name:
        stored = resident["customer_name"].strip().lower()
        given = name.strip().lower()
        if given not in stored:
            return "name_mismatch", None

    if resident["booking_2_ref"] is not None:
        slot = "booking_2"
    elif resident["booking_1_ref"] is not None:
        slot = "booking_1"
    else:
        return "no_booking", None

    changes_col = f"{slot}_changes"
    date_col = f"{slot}_date"

    if resident[changes_col] >= 1:
        return "change_limit", slot

    with _connect() as conn:
        conn.execute(
            f"""UPDATE residents
               SET {date_col} = ?, {changes_col} = {changes_col} + 1
               WHERE phone = ?""",
            (new_date, phone),
        )
    return "ok", slot


def services_left(phone: str) -> int:
    resident = get_resident(phone)
    return resident["services_left"] if resident else 0
