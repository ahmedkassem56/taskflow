"""FastAPI application — DESIGN.md §1–§2. All routes live here.

Layout (per create_app, in registration order):
1. Exception handlers (string `detail` on every 4xx/5xx — §2.0).
2. API routes (/api/...).
3. Static catch-all GET /{full_path:path} (§1.3) — registered AFTER every
   /api route.

Item/list mutations run in a single SQLite transaction per request; the
recurrence spawn (mark-done + insert next occurrence) uses BEGIN IMMEDIATE +
a WHERE done = 0 guard so a double PATCH can never spawn twice (§2.5, §8.5).
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import ValidationError

from . import db
from .db import get_db
from .recurrence import next_due
from .schemas import (
    ItemCreate,
    ItemPatch,
    ListCreate,
    SharedItemCreate,
    SharedItemPatch,
    ShareCreate,
    Status,
)

# Error messages mandated by DESIGN.md (exact strings).
ERR_LIST_MISSING = "Referenced list does not exist"
ERR_SHARE_GONE = "Share link not found or revoked"
ERR_READONLY = "This shared list is read-only."
ERR_NO_FIELDS = "No fields to update"
ERR_BAD_JSON = "Invalid JSON body"
ERR_BODY_NOT_OBJECT = "Request body must be a JSON object"

# Explicit media types for static files (§1.3); browsers refuse .webmanifest
# with a wrong MIME (§8.9).
MEDIA_TYPES = {
    ".webmanifest": "application/manifest+json",
    ".js": "text/javascript",
    ".css": "text/css",
    ".png": "image/png",
    ".html": "text/html",
    ".svg": "image/svg+xml",
}
NO_CACHE = {"Cache-Control": "no-cache"}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _fmt(errors, *, body_prefix: bool = False) -> str:
    """422 detail: '; '.join(f'{loc}: {msg}') — loc like `body.title` (§2.0)."""
    parts = []
    for err in errors:
        loc = list(err["loc"])
        if body_prefix:
            loc = ["body", *loc]
        parts.append(f"{'.'.join(str(x) for x in loc)}: {err['msg']}")
    return "; ".join(parts)


async def _await_object_body(request: Request) -> dict:
    """Read a PATCH body manually so we own the 400 semantics (§2.0):
    malformed JSON => 400 'Invalid JSON body'; non-object body => 400."""
    try:
        raw = await request.body()
    except Exception as exc:  # pragma: no cover — defensive
        raise HTTPException(status_code=400, detail=ERR_BAD_JSON) from exc
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=ERR_BAD_JSON) from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail=ERR_BODY_NOT_OBJECT)
    return data


def _store_quantity(value):
    return db.normalize_quantity(value)


def _insert_pending_item(db_conn: sqlite3.Connection, list_id: int, title: str,
                         notes, priority: str, due_date, quantity,
                         recurrence: str, recurrence_interval, now: str) -> int:
    """Insert a new pending item at the TOP of a list (position 0) — new-on-top
    ordering (DESIGN-reorder §1.3): the list's existing pending items shift
    down one slot first. Caller owns the transaction (all three call sites run
    inside BEGIN IMMEDIATE).
    """
    db_conn.execute(
        "UPDATE items SET position = position + 1 WHERE list_id = ? AND done = 0",
        (list_id,),
    )
    cur = db_conn.execute(
        "INSERT INTO items (list_id, title, notes, priority, due_date, quantity,"
        " position, done, recurrence, recurrence_interval, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,0,0,?,?,?,?)",
        (
            list_id,
            title,
            notes,
            priority,
            due_date,
            _store_quantity(quantity),
            recurrence,
            recurrence_interval,
            now,
            now,
        ),
    )
    return cur.lastrowid


def _require_share(db_conn: sqlite3.Connection, token: str) -> sqlite3.Row:
    share = db.fetch_share(db_conn, token)
    if share is None:
        raise HTTPException(status_code=404, detail=ERR_SHARE_GONE)
    return share


def _require_edit(db_conn: sqlite3.Connection, token: str) -> sqlite3.Row:
    share = _require_share(db_conn, token)
    if share["permission"] != "edit":
        raise HTTPException(status_code=403, detail=ERR_READONLY)
    return share


def _apply_item_patch(db_conn: sqlite3.Connection, row, patch) -> tuple[dict, dict | None, dict | None]:
    """Shared core of PATCH /api/items/{id} and the shared PATCH — §2.4/§2.5.

    *row* is the pre-update items row (already locked via BEGIN IMMEDIATE).
    Returns (item_dict, spawned_dict|None, swapped_dict|None) — *swapped* is
    set only for a `move` PATCH that found a neighbor to swap with. Raises
    HTTPException for 422/409.
    """
    provided = patch.model_fields_set
    now = db.utcnow()

    # move_to operation (DESIGN-fix-reorder §1.2) — handled FIRST: reorder the
    # item to ordinal K within its same-list, same-done group in (position, id)
    # order, one transaction. K==current is a no-op (no updated_at bump);
    # out-of-range K clamps to the group's last slot.
    if patch.move_to is not None:
        rows = db_conn.execute(
            "SELECT id FROM items WHERE list_id = ? AND done = ?"
            " ORDER BY position ASC, id ASC",
            (row["list_id"], row["done"]),
        ).fetchall()
        ids = [r["id"] for r in rows]
        cur = ids.index(row["id"])
        if cur == patch.move_to:
            return db.item_from_row(row), None, None
        k = max(0, min(patch.move_to, len(ids) - 1))
        if k == cur:
            # Out-of-range K clamped back onto the current slot — still a no-op.
            return db.item_from_row(row), None, None
        new_ids = list(ids)
        new_ids.pop(cur)
        new_ids.insert(k, row["id"])
        for i, rid in enumerate(new_ids):
            db_conn.execute(
                "UPDATE items SET position = ?, updated_at = ? WHERE id = ?",
                (i, now, rid),
            )
        moved = db.fetch_item(db_conn, row["id"])
        return moved, None, None

    # Move operation (DESIGN-reorder §1.4) — handled FIRST: swap position with
    # the adjacent same-list, same-done neighbor in (position, id) order.
    if patch.move is not None:
        if patch.move == "up":
            neighbor = db_conn.execute(
                "SELECT * FROM items WHERE list_id = ? AND done = ?"
                " AND (position < ? OR (position = ? AND id < ?))"
                " ORDER BY position DESC, id DESC LIMIT 1",
                (row["list_id"], row["done"], row["position"],
                 row["position"], row["id"]),
            ).fetchone()
        else:  # "down"
            neighbor = db_conn.execute(
                "SELECT * FROM items WHERE list_id = ? AND done = ?"
                " AND (position > ? OR (position = ? AND id > ?))"
                " ORDER BY position ASC, id ASC LIMIT 1",
                (row["list_id"], row["done"], row["position"],
                 row["position"], row["id"]),
            ).fetchone()
        if neighbor is None:
            # Boundary: nothing to swap with — 200 no-op, item unchanged.
            return db.item_from_row(row), None, None
        # Swap positions (two UPDATEs inside the existing transaction). updated_at
        # bumps like every other PATCH (DESIGN-reorder §1.4 decision).
        db_conn.execute(
            "UPDATE items SET position = ?, updated_at = ? WHERE id = ?",
            (neighbor["position"], now, row["id"]),
        )
        db_conn.execute(
            "UPDATE items SET position = ?, updated_at = ? WHERE id = ?",
            (row["position"], now, neighbor["id"]),
        )
        moved = db.fetch_item(db_conn, row["id"])
        swapped = db.fetch_item(db_conn, neighbor["id"])
        return moved, None, swapped

    already_done = row["done"] == 1

    # Idempotent no-op: done:true on an already-done item, nothing else given —
    # no spawn, no updated_at bump (double-click guard, §2.3 #8 / §2.4).
    if provided == {"done"} and patch.done is True and already_done:
        return db.item_from_row(row), None, None

    if "list_id" in provided:
        if patch.list_id is None:  # rejected by schema too; belt & suspenders
            raise HTTPException(status_code=422, detail="list_id cannot be null")
        if not db.list_exists(db_conn, patch.list_id):
            raise HTTPException(status_code=409, detail=ERR_LIST_MISSING)

    # Merged post-patch state (absent PATCH fields keep their current values).
    def merged(field):
        return getattr(patch, field) if field in provided else row[field]

    merged_done = 1 if patch.done is True else (0 if patch.done is False else row["done"])
    merged_recurrence = merged("recurrence")
    merged_interval = merged("recurrence_interval")

    # Merged-state interval rules (§2.4): custom requires interval; interval
    # while not custom is invalid (client must clear it first).
    if merged_recurrence == "custom" and merged_interval is None:
        raise HTTPException(
            status_code=422,
            detail="recurrence_interval is required when recurrence is 'custom'",
        )
    if merged_recurrence != "custom" and merged_interval is not None:
        raise HTTPException(
            status_code=422,
            detail="recurrence_interval must be null when recurrence is not 'custom'",
        )

    # Spawn: done flips 0 -> 1 on a recurring item (§2.5). The new occurrence
    # is a fresh pending item, so it lands on TOP like any create — shift the
    # target list's pending items down one slot, insert at position 0
    # (DESIGN-reorder §1.4).
    spawned = None
    if row["done"] == 0 and merged_done == 1 and merged_recurrence != "none":
        # Anchor on the row's PRE-spawn due_date (may be NULL -> created_at).
        next_date = next_due(
            row["due_date"], row["created_at"], merged_recurrence, merged_interval
        )
        cur_id = _insert_pending_item(
            db_conn,
            merged("list_id"),
            merged("title"),
            merged("notes"),
            merged("priority"),
            next_date.isoformat(),
            merged("quantity"),
            merged_recurrence,
            merged_interval,
            now,
        )
        spawned = db.fetch_item(db_conn, cur_id)

    # Apply the provided fields to the original row (value-identical writes
    # still bump updated_at — §2.4).
    sets, params = [], []
    for field in provided:
        if field == "done":
            value = merged_done
        elif field == "quantity":
            value = _store_quantity(merged("quantity"))
        else:
            value = merged(field)
        sets.append(f"{field} = ?")
        params.append(value)
    sets.append("updated_at = ?")
    params.append(now)
    params.append(row["id"])
    db_conn.execute(f"UPDATE items SET {', '.join(sets)} WHERE id = ?", params)

    item = db.fetch_item(db_conn, row["id"])
    return item, spawned, None


# --------------------------------------------------------------------------
# static serving (§1.3)
# --------------------------------------------------------------------------

def serve_static(full_path: str):
    """Catch-all GET handler: SPA shell, static files, API-404, SPA fallback."""
    # 1. App shell routes.
    if full_path == "" or full_path == "index.html" or full_path.startswith("share/"):
        return FileResponse(
            db.STATIC_DIR / "index.html", media_type="text/html", headers=NO_CACHE
        )

    # 2. Real static files (path-traversal guarded).
    candidate = (db.STATIC_DIR / full_path).resolve()
    try:
        inside = candidate.is_relative_to(db.STATIC_DIR.resolve())
    except AttributeError:  # pragma: no cover — py<3.9
        inside = str(candidate).startswith(str(db.STATIC_DIR.resolve()))
    if inside and candidate.is_file():
        media = MEDIA_TYPES.get(candidate.suffix.lower())
        if media is None:
            media = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        return FileResponse(candidate, media_type=media, headers=NO_CACHE)

    # 3. Unknown /api path: JSON 404 (design's lowercase message).
    if full_path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    # 4. SPA fallback for any other unknown path.
    return FileResponse(
        db.STATIC_DIR / "index.html", media_type="text/html", headers=NO_CACHE
    )


# --------------------------------------------------------------------------
# app factory
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    try:
        db.init_schema(conn)
    finally:
        conn.close()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Taskflow", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)

    # -- CORS (Flutter web / other origins during development) --------------
    # Dev-stage: allow any origin so the Flutter web client (served from a
    # different port/origin) can call the API. Tighten to explicit origins
    # when auth/multi-user lands.
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Exception handlers: `detail` is ALWAYS a string (§2.0) -------------
    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        # FastAPI folds malformed-JSON bodies into a json_invalid validation
        # error; the contract wants 400 'Invalid JSON body' for those.
        if any(err.get("type") == "json_invalid" for err in errors):
            return JSONResponse(status_code=400, content={"detail": ERR_BAD_JSON})
        return JSONResponse(status_code=422, content={"detail": _fmt(errors)})

    @app.exception_handler(json.JSONDecodeError)
    async def on_json_decode_error(request: Request, exc: json.JSONDecodeError):
        return JSONResponse(status_code=400, content={"detail": ERR_BAD_JSON})

    @app.exception_handler(Exception)
    async def on_unhandled(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    # -- API responses must never be heuristically cached by the browser ------
    # Without Cache-Control, GET /api/... may be served stale from the browser
    # HTTP cache (breaking toggle/refresh). no-store kills heuristic caching.
    @app.middleware("http")
    async def api_no_store(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # -- API routes ---------------------------------------------------------

    @app.get("/api/health")
    def health():
        try:
            conn = db.connect()
            try:
                conn.execute("SELECT 1").fetchone()
            finally:
                conn.close()
        except Exception:
            raise HTTPException(status_code=500, detail="Database unavailable")
        return {"status": "ok", "database": "ok"}

    # -- lists --------------------------------------------------------------

    @app.get("/api/lists")
    def get_lists(_db: sqlite3.Connection = Depends(get_db)):
        return db.fetch_lists(_db)

    @app.post("/api/lists", status_code=201)
    def create_list(body: ListCreate, _db: sqlite3.Connection = Depends(get_db)):
        now = db.utcnow()
        cur = _db.execute(
            "INSERT INTO lists (name, created_at, updated_at) VALUES (?, ?, ?)",
            (body.name, now, now),
        )
        return db.fetch_list(_db, cur.lastrowid)

    @app.patch("/api/lists/{list_id}")
    async def rename_list(list_id: int, request: Request,
                          _db: sqlite3.Connection = Depends(get_db)):
        data = await _await_object_body(request)
        if not data:
            raise HTTPException(status_code=400, detail=ERR_NO_FIELDS)
        try:
            body = ListCreate.model_validate(data)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=_fmt(exc.errors(), body_prefix=True)) from exc
        _db.execute("BEGIN IMMEDIATE")
        row = _db.execute("SELECT id FROM lists WHERE id = ?", (list_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Not Found")
        _db.execute(
            "UPDATE lists SET name = ?, updated_at = ? WHERE id = ?",
            (body.name, db.utcnow(), list_id),
        )
        return db.fetch_list(_db, list_id)

    @app.delete("/api/lists/{list_id}")
    def delete_list(list_id: int, _db: sqlite3.Connection = Depends(get_db)):
        cur = _db.execute("DELETE FROM lists WHERE id = ?", (list_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not Found")
        return Response(status_code=204)

    # -- items --------------------------------------------------------------

    @app.get("/api/items")
    def get_items(
        list_id: Optional[int] = Query(default=None, gt=0),
        status: Status = "all",
        q: Optional[str] = Query(default=None),
        _db: sqlite3.Connection = Depends(get_db),
    ):
        query = q.strip() if q else None
        if query == "":
            query = None
        return db.fetch_items(_db, list_id=list_id, status=status, q=query)

    @app.post("/api/items", status_code=201)
    def create_item(body: ItemCreate, _db: sqlite3.Connection = Depends(get_db)):
        if not db.list_exists(_db, body.list_id):
            raise HTTPException(status_code=409, detail=ERR_LIST_MISSING)
        now = db.utcnow()
        # Shift + insert run in one transaction (new-on-top, §1.3).
        _db.execute("BEGIN IMMEDIATE")
        cur_id = _insert_pending_item(
            _db, body.list_id, body.title, body.notes, body.priority,
            body.due_date, body.quantity, body.recurrence,
            body.recurrence_interval, now,
        )
        return db.fetch_item(_db, cur_id)

    @app.patch("/api/items/{item_id}")
    async def patch_item(item_id: int, request: Request,
                         _db: sqlite3.Connection = Depends(get_db)):
        data = await _await_object_body(request)
        if not data:
            raise HTTPException(status_code=400, detail=ERR_NO_FIELDS)
        try:
            patch = ItemPatch.model_validate(data)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=_fmt(exc.errors(), body_prefix=True)) from exc
        _db.execute("BEGIN IMMEDIATE")
        row = _db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Not Found")
        item, spawned, swapped = _apply_item_patch(_db, row, patch)
        # Commit BEFORE the response is sent. This endpoint is async with a sync
        # dependency; without an explicit commit the write could still be
        # uncommitted when a follow-up GET (fresh connection) runs, reading
        # stale pre-toggle state (the "toggle doesn't stick" bug).
        _db.commit()
        if patch.move is not None:
            return {"item": item, "swapped": swapped}
        return {"item": item, "spawned": spawned}

    @app.delete("/api/items/{item_id}")
    def delete_item(item_id: int, _db: sqlite3.Connection = Depends(get_db)):
        cur = _db.execute("DELETE FROM items WHERE id = ?", (item_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not Found")
        return Response(status_code=204)

    # -- shares -------------------------------------------------------------

    @app.post("/api/lists/{list_id}/shares", status_code=201)
    def create_share(list_id: int, body: ShareCreate, request: Request,
                     _db: sqlite3.Connection = Depends(get_db)):
        if not db.list_exists(_db, list_id):
            raise HTTPException(status_code=404, detail="Not Found")
        token = secrets.token_urlsafe(16)  # 22 chars, [A-Za-z0-9_-], unguessable
        now = db.utcnow()
        _db.execute(
            "INSERT INTO shares (token, list_id, permission, created_at)"
            " VALUES (?, ?, ?, ?)",
            (token, list_id, body.permission, now),
        )
        url = str(request.base_url).rstrip("/") + "/share/" + token
        return {"token": token, "permission": body.permission, "url": url,
                "created_at": now}

    @app.delete("/api/shares/{token}")
    def revoke_share(token: str, _db: sqlite3.Connection = Depends(get_db)):
        cur = _db.execute("DELETE FROM shares WHERE token = ?", (token,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not Found")
        return Response(status_code=204)

    @app.get("/api/shared/{token}")
    def get_shared(token: str, _db: sqlite3.Connection = Depends(get_db)):
        share = _require_share(_db, token)
        return {
            "list": db.fetch_list(_db, share["list_id"]),
            "items": db.fetch_items(_db, list_id=share["list_id"]),
            "permission": share["permission"],
        }

    @app.post("/api/shared/{token}/items", status_code=201)
    def create_shared_item(token: str, body: SharedItemCreate,
                           _db: sqlite3.Connection = Depends(get_db)):
        share = _require_edit(_db, token)
        now = db.utcnow()
        # Shift + insert run in one transaction (new-on-top, §1.3).
        _db.execute("BEGIN IMMEDIATE")
        cur_id = _insert_pending_item(
            _db, share["list_id"], body.title, body.notes, body.priority,
            body.due_date, body.quantity, body.recurrence,
            body.recurrence_interval, now,
        )
        return db.fetch_item(_db, cur_id)

    @app.patch("/api/shared/{token}/items/{item_id}")
    async def patch_shared_item(token: str, item_id: int, request: Request,
                                _db: sqlite3.Connection = Depends(get_db)):
        share = _require_edit(_db, token)
        data = await _await_object_body(request)
        if not data:
            raise HTTPException(status_code=400, detail=ERR_NO_FIELDS)
        try:
            patch = SharedItemPatch.model_validate(data)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=_fmt(exc.errors(), body_prefix=True)) from exc
        _db.execute("BEGIN IMMEDIATE")
        row = _db.execute(
            "SELECT * FROM items WHERE id = ? AND list_id = ?",
            (item_id, share["list_id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Not Found")
        item, spawned, swapped = _apply_item_patch(_db, row, patch)
        if patch.move is not None:
            return {"item": item, "swapped": swapped}
        return {"item": item, "spawned": spawned}

    @app.delete("/api/shared/{token}/items/{item_id}")
    def delete_shared_item(token: str, item_id: int,
                           _db: sqlite3.Connection = Depends(get_db)):
        share = _require_edit(_db, token)
        cur = _db.execute(
            "DELETE FROM items WHERE id = ? AND list_id = ?",
            (item_id, share["list_id"]),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not Found")
        return Response(status_code=204)

    # -- static catch-all (§1.3) — registered AFTER every /api route ---------

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        return serve_static(full_path)

    return app


app = create_app()
