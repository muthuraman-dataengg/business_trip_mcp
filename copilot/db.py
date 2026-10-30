"""SQLite persistence for travelers, tasks and bookings.

SQLite in WAL mode is safe for the three MCP server processes and the UI to
share. Swap this module for Postgres (same function signatures) in larger
deployments.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from .config import get_settings
from .models import Booking, BookingRequest, Traveler, TravelerCreate, TravelerPreferences, TripTask

SCHEMA = """
CREATE TABLE IF NOT EXISTS travelers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    home_city TEXT NOT NULL,
    home_currency TEXT NOT NULL DEFAULT 'USD',
    department TEXT NOT NULL DEFAULT '',
    preferences TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    traveler_id TEXT REFERENCES travelers(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    traveler_id TEXT REFERENCES travelers(id) ON DELETE CASCADE,
    city TEXT NOT NULL,
    hotel_name TEXT NOT NULL,
    check_in TEXT NOT NULL,
    nights INTEGER NOT NULL,
    stars INTEGER,
    nightly_rate REAL,
    currency TEXT,
    status TEXT NOT NULL DEFAULT 'confirmed',
    approved_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS booking_requests (
    id TEXT PRIMARY KEY,
    traveler_id TEXT REFERENCES travelers(id) ON DELETE CASCADE,
    city TEXT NOT NULL,
    hotel_name TEXT NOT NULL,
    check_in TEXT NOT NULL,
    nights INTEGER NOT NULL,
    stars INTEGER,
    nightly_rate REAL,
    currency TEXT,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    decided_by TEXT NOT NULL DEFAULT '',
    booking_id TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_status ON booking_requests(status);
CREATE INDEX IF NOT EXISTS idx_tasks_traveler ON tasks(traveler_id);
CREATE INDEX IF NOT EXISTS idx_bookings_traveler ON bookings(traveler_id);
"""

_initialised: set = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _short_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@contextmanager
def connect(path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    db_path = Path(path or get_settings().db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    key = str(db_path)
    if key not in _initialised:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _initialised.add(key)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- travelers
def _row_to_traveler(row: sqlite3.Row) -> Traveler:
    data = dict(row)
    data["preferences"] = TravelerPreferences.model_validate_json(data["preferences"])
    return Traveler.model_validate(data)


def create_traveler(payload: TravelerCreate) -> Traveler:
    now = _now()
    traveler_id = _short_id("TRV")
    with connect() as conn:
        conn.execute(
            "INSERT INTO travelers VALUES (?,?,?,?,?,?,?,?,?)",
            (
                traveler_id,
                payload.name.strip(),
                payload.email,
                payload.home_city.strip(),
                payload.home_currency.upper(),
                payload.department,
                payload.preferences.model_dump_json(),
                now,
                now,
            ),
        )
    return get_traveler(traveler_id)  # type: ignore[return-value]


def get_traveler(traveler_id: str) -> Optional[Traveler]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM travelers WHERE id = ?", (traveler_id,)).fetchone()
    return _row_to_traveler(row) if row else None


def list_travelers() -> List[Traveler]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM travelers ORDER BY name").fetchall()
    return [_row_to_traveler(r) for r in rows]


def update_traveler(traveler_id: str, payload: TravelerCreate) -> Traveler:
    with connect() as conn:
        cur = conn.execute(
            """UPDATE travelers SET name=?, email=?, home_city=?, home_currency=?,
               department=?, preferences=?, updated_at=? WHERE id=?""",
            (
                payload.name.strip(),
                payload.email,
                payload.home_city.strip(),
                payload.home_currency.upper(),
                payload.department,
                payload.preferences.model_dump_json(),
                _now(),
                traveler_id,
            ),
        )
        if cur.rowcount == 0:
            raise KeyError(f"Unknown traveler {traveler_id}")
    return get_traveler(traveler_id)  # type: ignore[return-value]


def delete_traveler(traveler_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM travelers WHERE id = ?", (traveler_id,))


# -------------------------------------------------------------------- tasks
def create_task(traveler_id: Optional[str], title: str, notes: str = "", due_date: Optional[date] = None) -> TripTask:
    task_id = _short_id("TASK")
    with connect() as conn:
        conn.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?)",
            (task_id, traveler_id, title.strip(), notes, due_date.isoformat() if due_date else None, "open", _now()),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return TripTask.model_validate(dict(row))


def list_tasks(traveler_id: Optional[str] = None, status: Optional[str] = None) -> List[TripTask]:
    sql, args = "SELECT * FROM tasks WHERE 1=1", []
    if traveler_id:
        sql += " AND traveler_id = ?"
        args.append(traveler_id)
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY COALESCE(due_date, '9999-12-31'), created_at"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [TripTask.model_validate(dict(r)) for r in rows]


def update_task_status(task_id: str, status: str) -> TripTask:
    with connect() as conn:
        cur = conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
        if cur.rowcount == 0:
            raise KeyError(f"Unknown task {task_id}")
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return TripTask.model_validate(dict(row))


# ----------------------------------------------------------------- bookings
def create_booking(
    traveler_id: Optional[str],
    city: str,
    hotel_name: str,
    check_in: date,
    nights: int,
    stars: Optional[int],
    nightly_rate: Optional[float],
    currency: Optional[str],
    approved_by: str,
) -> Booking:
    booking_id = _short_id("BKG")
    with connect() as conn:
        conn.execute(
            "INSERT INTO bookings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                booking_id,
                traveler_id,
                city,
                hotel_name,
                check_in.isoformat(),
                nights,
                stars,
                nightly_rate,
                currency,
                "confirmed",
                approved_by,
                _now(),
            ),
        )
        row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    return Booking.model_validate(dict(row))


def list_bookings(traveler_id: Optional[str] = None) -> List[Booking]:
    sql, args = "SELECT * FROM bookings", []
    if traveler_id:
        sql += " WHERE traveler_id = ?"
        args.append(traveler_id)
    sql += " ORDER BY created_at DESC"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [Booking.model_validate(dict(r)) for r in rows]


# --------------------------------------------------------- booking requests
def create_booking_request(
    traveler_id: Optional[str],
    city: str,
    hotel_name: str,
    check_in: date,
    nights: int,
    stars: Optional[int] = None,
    nightly_rate: Optional[float] = None,
    currency: Optional[str] = None,
    reason: str = "",
) -> BookingRequest:
    request_id = _short_id("REQ")
    with connect() as conn:
        conn.execute(
            """INSERT INTO booking_requests (id, traveler_id, city, hotel_name, check_in, nights, stars,
               nightly_rate, currency, reason, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?)""",
            (request_id, traveler_id, city, hotel_name, check_in.isoformat(), nights, stars, nightly_rate, currency, reason, _now()),
        )
    return get_booking_request(request_id)  # type: ignore[return-value]


def get_booking_request(request_id: str) -> Optional[BookingRequest]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM booking_requests WHERE id = ?", (request_id,)).fetchone()
    return BookingRequest.model_validate(dict(row)) if row else None


def list_booking_requests(status: Optional[str] = None, traveler_id: Optional[str] = None) -> List[BookingRequest]:
    sql, args = "SELECT * FROM booking_requests WHERE 1=1", []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if traveler_id:
        sql += " AND traveler_id = ?"
        args.append(traveler_id)
    sql += " ORDER BY created_at DESC"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [BookingRequest.model_validate(dict(r)) for r in rows]


def decide_booking_request(request_id: str, status: str, decided_by: str, booking_id: Optional[str] = None) -> BookingRequest:
    with connect() as conn:
        cur = conn.execute(
            """UPDATE booking_requests SET status=?, decided_by=?, booking_id=?, decided_at=?
               WHERE id=? AND status='pending'""",
            (status, decided_by, booking_id, _now(), request_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"Request {request_id} not found or already decided")
    return get_booking_request(request_id)  # type: ignore[return-value]


def to_jsonable(obj) -> object:  # noqa: ANN001
    """Pydantic model / list -> plain JSON-able structures."""
    if isinstance(obj, list):
        return [to_jsonable(o) for o in obj]
    if hasattr(obj, "model_dump"):
        return json.loads(obj.model_dump_json())
    return obj
