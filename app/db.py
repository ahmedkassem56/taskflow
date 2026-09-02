"""SQLite persistence layer — DESIGN.md §1.2, §3.

One connection per request (FastAPI dependency), WAL mode, `PRAGMA
foreign_keys = ON` on every connection (FK enforcement is per-connection in
SQLite — without it `ON DELETE CASCADE` silently no-ops). Schema is created
idempotently at app startup. Row -> dict serializers live here too
(done -> bool, quantity int-when-integral, canonical item ORDER BY).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"
DEFAULT_DB_PATH = str(PROJECT_ROOT / "todo.db")

# DESIGN.md §3 — exact DDL (PRAGMA foreign_keys is set per-connection in code).
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS lists (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT    NOT NULL,
  created_at TEXT    NOT NULL,         -- ISO-8601 UTC 'Z', see §1.6
  updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  list_id             INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
  title               TEXT    NOT NULL,
  notes               TEXT,
  priority            TEXT    NOT NULL DEFAULT 'none'
                      CHECK (priority IN ('none','low','medium','high')),
  due_date            TEXT    CHECK (due_date IS NULL OR
                              due_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  quantity            REAL    NOT NULL DEFAULT 1 CHECK (quantity > 0),
  position            INTEGER NOT NULL DEFAULT 0,
  done                INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0,1)),
  recurrence          TEXT    NOT NULL DEFAULT 'none'
                      CHECK (recurrence IN ('none','daily','weekly','monthly','custom')),
  recurrence_interval INTEGER CHECK (recurrence_interval IS NULL OR recurrence_interval >= 1),
  created_at          TEXT    NOT NULL,
  updated_at          TEXT    NOT NULL,
  CHECK ((recurrence = 'custom') = (recurrence_interval IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS shares (
  token      TEXT PRIMARY KEY,         -- secrets.token_urlsafe(16), 22 chars
  list_id    INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
  permission TEXT    NOT NULL CHECK (permission IN ('read','edit')),
  created_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_list_done ON items(list_id, done);
CREATE INDEX IF NOT EXISTS idx_items_done      ON items(done);
CREATE INDEX IF NOT EXISTS idx_shares_list     ON shares(list_id);
"""

# DESIGN-reorder §1.2 — canonical item sort (single SQL used by every
# item-returning endpoint): pending first, then position (lower = higher on
# screen), then id. The query must alias the items table as `i`.
ITEM_ORDER_SQL = (
    "ORDER BY i.done ASC, i.position ASC, i.id ASC"
)


def db_path() -> str:
    """DB file: env TODO_DB override, else <project root>/todo.db (§1.4)."""
    return os.environ.get("TODO_DB") or DEFAULT_DB_PATH


def utcnow() -> str:
    """Fixed-width UTC timestamp: `%Y-%m-%dT%H:%M:%S.%f` + 'Z' (§1.6).

    Fixed width => lexicographic TEXT ordering == chronological ordering.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open one connection with the per-connection PRAGMAs (§1.2, §8.1).

    isolation_level=None => autocommit; multi-statement write transactions are
    opened explicitly with BEGIN IMMEDIATE (see spawn logic in main.py).
    check_same_thread=False: FastAPI runs sync dependencies and endpoints in a
    threadpool, but async PATCH endpoints consume the same connection from the
    event-loop thread — each connection is still owned by exactly one request.
    """
    conn = sqlite3.connect(path or db_path(), timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema creation; WAL is set once at startup (persistent).

    Guarded migration (DESIGN-reorder §1.1): DBs created before the `position`
    column existed get it added via ALTER and backfilled to `id` (ids are
    monotonic in creation order, so this preserves current relative order).
    Runs only when the column is missing -> idempotent across restarts.
    """
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA_SQL)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()]
    if "position" not in cols:
        conn.execute(
            "ALTER TABLE items ADD COLUMN position INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute("UPDATE items SET position = id")


def get_db():
    """FastAPI dependency: one connection per request — commit on success,
    roll back on exception, always close."""
    conn = connect()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Serializers
# --------------------------------------------------------------------------

def normalize_quantity(q):
    """Quantity serializes as int when integral (1, 2) else float (0.5) —
    never 1.0 (§2.0). Also used before storing so SQLite keeps INTEGERs."""
    if isinstance(q, int) and not isinstance(q, bool):
        return q
    f = float(q)
    return int(f) if f.is_integer() else f


def item_from_row(row) -> dict | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "list_id": row["list_id"],
        "title": row["title"],
        "notes": row["notes"],
        "priority": row["priority"],
        "due_date": row["due_date"],
        "quantity": normalize_quantity(row["quantity"]),
        "position": row["position"],
        "done": bool(row["done"]),
        "recurrence": row["recurrence"],
        "recurrence_interval": row["recurrence_interval"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# --------------------------------------------------------------------------
# Read queries
# --------------------------------------------------------------------------

# LEFT JOIN counts per list — item_count / pending_count are read-only derived
# fields computed per request (§2.1), served by idx_items_list_done.
_LIST_SELECT = """
SELECT l.id AS id,
       l.name AS name,
       l.created_at AS created_at,
       l.updated_at AS updated_at,
       COUNT(i.id) AS item_count,
       COUNT(i.id) FILTER (WHERE i.done = 0) AS pending_count
FROM lists l
LEFT JOIN items i ON i.list_id = l.id
"""


def fetch_lists(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        _LIST_SELECT
        + " GROUP BY l.id ORDER BY l.name COLLATE NOCASE ASC, l.id ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_list(conn: sqlite3.Connection, list_id: int) -> dict | None:
    row = conn.execute(
        _LIST_SELECT + " WHERE l.id = ? GROUP BY l.id", (list_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def list_exists(conn: sqlite3.Connection, list_id: int) -> bool:
    return conn.execute("SELECT 1 FROM lists WHERE id = ?", (list_id,)).fetchone() is not None


def fetch_items(conn: sqlite3.Connection, list_id: int | None = None,
                status: str = "all", q: str | None = None) -> list[dict]:
    """Filtered/sorted items (DESIGN.md §2.3 #6), canonical sort order."""
    where, params = [], []
    if list_id is not None:
        where.append("i.list_id = ?")
        params.append(list_id)
    if status == "pending":
        where.append("i.done = 0")
    elif status == "done":
        where.append("i.done = 1")
    if q:
        # LIKE is ASCII-case-insensitive; escape %/_ so user input stays honest
        # (DESIGN.md §8.6).
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        where.append(
            "(i.title LIKE ? ESCAPE '\\' OR COALESCE(i.notes, '') LIKE ? ESCAPE '\\')"
        )
        params.extend([pattern, pattern])
    sql = "SELECT i.* FROM items i"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " " + ITEM_ORDER_SQL
    rows = conn.execute(sql, params).fetchall()
    return [item_from_row(r) for r in rows]


def fetch_item(conn: sqlite3.Connection, item_id: int) -> dict | None:
    row = conn.execute("SELECT i.* FROM items i WHERE i.id = ?", (item_id,)).fetchone()
    return item_from_row(row)


def fetch_share(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM shares WHERE token = ?", (token,)).fetchone()
