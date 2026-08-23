"""SQLite access layer.

Deliberately thin: raw sqlite3 with row factories rather than an ORM. The data
model is stable and query-shaped (aggregations over a few tables), so an ORM
would add indirection without buying anything.
"""

from __future__ import annotations

import hashlib
import sqlite3
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator, Sequence

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def utcnow() -> str:
    """Current time as an ISO-8601 UTC string, matching the schema convention."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def money(value: Any) -> int | None:
    """Coerce a price-ish value to integer cents.

    Accepts '$1,234.56', '1234.56', Decimal, int, float. Returns None for
    anything unparseable rather than guessing — a wrong number is worse than a
    missing one in a cost analysis.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 100  # bare ints are always dollars; callers pass cents directly
    if isinstance(value, Decimal):
        return int((value * 100).quantize(Decimal("1")))
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None
    try:
        cents = int((Decimal(cleaned) * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        return None
    return -cents if negative and cents > 0 else cents


def fmt_money(cents: int | None) -> str:
    """Render integer cents as a display string."""
    if cents is None:
        return "—"
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
        return self._conn

    # Columns added after the initial schema. CREATE TABLE IF NOT EXISTS will
    # not add a column to a table that already exists, so additive changes are
    # applied explicitly here. Each entry is (table, column, SQL type clause).
    COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
        ("line_items", "relevance", "TEXT"),
        ("line_items", "relevance_by", "TEXT"),
        ("line_items", "relevance_conf", "REAL"),
        ("line_items", "relevance_note", "TEXT"),
        ("line_items", "insurable", "INTEGER"),
        ("orders", "vessel", "TEXT"),
        ("orders", "reference", "TEXT"),
        # show_log grew its competition columns after the table first shipped on
        # the studio branch; a database created in between gets them here.
        ("show_log", "kind", "TEXT NOT NULL DEFAULT 'set'"),
        ("show_log", "attendees", "INTEGER"),
    )

    def migrate(self) -> None:
        """Apply schema.sql, then any additive column migrations.

        schema.sql is all CREATE ... IF NOT EXISTS, so it is idempotent but
        cannot alter existing tables; COLUMN_MIGRATIONS covers that gap."""
        self.conn.executescript(SCHEMA_PATH.read_text())
        for table, column, decl in self.COLUMN_MIGRATIONS:
            if not self._has_column(table, column):
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        # Views are dropped and recreated so a changed definition takes effect;
        # CREATE VIEW IF NOT EXISTS would otherwise keep a stale one forever.
        self.conn.executescript(
            "DROP VIEW IF EXISTS v_review_queue; DROP VIEW IF EXISTS v_unclassified; "
            "DROP VIEW IF EXISTS v_spend_by_system;"
        )
        self.conn.executescript(SCHEMA_PATH.read_text())

    def _has_column(self, table: str, column: str) -> bool:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == column for r in rows)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN")
        try:
            yield self.conn
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    # --- raw document store -------------------------------------------------

    def store_raw(
        self,
        source: str,
        external_id: str,
        payload: bytes,
        *,
        content_type: str = "application/json",
        occurred_at: str | None = None,
    ) -> tuple[int, bool]:
        """Append a raw document. Returns (row_id, is_new).

        Identity is (source, external_id, content_hash), so re-fetching an
        unchanged document is a no-op while a *changed* document (an order that
        shipped, a transaction that settled) is stored as a new version
        alongside the old one. Nothing is ever overwritten.
        """
        digest = hashlib.sha256(payload).hexdigest()
        existing = self.one(
            "SELECT id FROM raw_documents WHERE source=? AND external_id=? AND content_hash=?",
            (source, external_id, digest),
        )
        if existing:
            return existing["id"], False
        cur = self.execute(
            """INSERT INTO raw_documents
                 (source, external_id, content_hash, content_type, payload, occurred_at, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (source, external_id, digest, content_type,
             zlib.compress(payload), occurred_at, utcnow()),
        )
        return int(cur.lastrowid), True

    def load_raw(self, row_id: int) -> bytes:
        row = self.one("SELECT payload FROM raw_documents WHERE id=?", (row_id,))
        if row is None:
            raise KeyError(f"no raw_document with id={row_id}")
        return zlib.decompress(row["payload"])

    def unparsed(self, source: str | None = None, limit: int = 1000) -> list[sqlite3.Row]:
        if source:
            return self.query(
                "SELECT * FROM raw_documents WHERE parsed_at IS NULL AND source=? "
                "ORDER BY id LIMIT ?", (source, limit))
        return self.query(
            "SELECT * FROM raw_documents WHERE parsed_at IS NULL ORDER BY id LIMIT ?", (limit,))

    def mark_parsed(self, row_id: int, error: str | None = None) -> None:
        self.execute(
            "UPDATE raw_documents SET parsed_at=?, parse_error=? WHERE id=?",
            (utcnow(), error, row_id),
        )

    # --- sync bookkeeping ---------------------------------------------------

    def get_cursor(self, source: str) -> str | None:
        row = self.one("SELECT cursor FROM sync_state WHERE source=?", (source,))
        return row["cursor"] if row else None

    def set_sync_state(
        self, source: str, cursor: str | None, status: str, detail: str | None = None
    ) -> None:
        self.execute(
            """INSERT INTO sync_state (source, cursor, last_run_at, last_status, detail)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(source) DO UPDATE SET
                 cursor=excluded.cursor, last_run_at=excluded.last_run_at,
                 last_status=excluded.last_status, detail=excluded.detail""",
            (source, cursor, utcnow(), status, detail),
        )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def connect(path: Path | str | None = None) -> Database:
    from ..config import get_settings

    settings = get_settings()
    db = Database(path or settings.db_path)
    db.migrate()
    return db
