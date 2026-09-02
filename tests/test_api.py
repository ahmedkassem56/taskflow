"""API acceptance tests — DESIGN.md §7 (rows AC1–AC6) against an isolated
temp DB. Every assertion checks status codes, exact JSON field names/types,
canonical ordering, and string-shaped {"detail": ...} error bodies.
"""

import re
from datetime import date

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
ITEM_KEYS = {
    "id", "list_id", "title", "notes", "priority", "due_date", "quantity",
    "position", "done", "recurrence", "recurrence_interval", "created_at",
    "updated_at",
}
LIST_KEYS = {"id", "name", "item_count", "pending_count", "created_at", "updated_at"}


def make_list(client, name):
    r = client.post("/api/lists", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def make_item(client, list_id, title="Task", **over):
    payload = {"list_id": list_id, "title": title}
    payload.update(over)
    r = client.post("/api/items", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def toggle(client, item_id, done=True, **over):
    payload = {"done": done}
    payload.update(over)
    r = client.patch(f"/api/items/{item_id}", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# AC1 — CRUD lists & items, validation with 4xx errors
# ---------------------------------------------------------------------------

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}


def test_list_crud(client):
    lst = make_list(client, "Groceries")
    assert set(lst.keys()) == LIST_KEYS
    assert lst["name"] == "Groceries"
    assert lst["item_count"] == 0 and lst["pending_count"] == 0
    assert TS_RE.match(lst["created_at"]) and TS_RE.match(lst["updated_at"])
    lid = lst["id"]

    # name is trimmed on create
    lst2 = make_list(client, "  Chores  ")
    assert lst2["name"] == "Chores"

    # GET /api/lists: ordered name COLLATE NOCASE ASC, id ASC
    lst3 = make_list(client, "apple")
    names = [l["name"] for l in client.get("/api/lists").json()]
    assert names == ["apple", "Chores", "Groceries"]

    # rename bumps updated_at
    r = client.patch(f"/api/lists/{lid}", json={"name": "  Market  "})
    assert r.status_code == 200
    renamed = r.json()
    assert renamed["name"] == "Market" and renamed["id"] == lid
    assert set(renamed.keys()) == LIST_KEYS

    # derived counts appear on list objects
    it = make_item(client, lid, "Buy milk")
    make_item(client, lid, "Buy bread")
    client.patch(f"/api/items/{it['id']}", json={"done": True})
    (l,) = [l for l in client.get("/api/lists").json() if l["id"] == lid]
    assert l["item_count"] == 2 and l["pending_count"] == 1

    # 404s
    assert client.patch("/api/lists/999999", json={"name": "x"}).status_code == 404
    assert client.delete("/api/lists/999999").status_code == 404
    # rename validation: empty/too-long/bad type
    assert client.patch(f"/api/lists/{lid}", json={"name": "   "}).status_code == 422
    assert client.patch(f"/api/lists/{lid}", json={"name": "x" * 201}).status_code == 422
    assert client.patch(f"/api/lists/{lid}", json={"name": 42}).status_code == 422

    # delete
    assert client.delete(f"/api/lists/{lid}").status_code == 204
    assert all(l["id"] != lid for l in client.get("/api/lists").json())


def test_item_crud_and_envelope(client):
    lid = make_list(client, "List")["id"]

    # POST with every field, exact serialization
    body = {
        "list_id": lid,
        "title": "  Buy milk  ",
        "notes": "2% organic",
        "priority": "high",
        "due_date": "2026-09-05",
        "quantity": 2,
        "recurrence": "none",
        "recurrence_interval": None,
    }
    r = client.post("/api/items", json=body)
    assert r.status_code == 201, r.text
    item = r.json()
    assert set(item.keys()) == ITEM_KEYS
    assert item["title"] == "Buy milk"          # trimmed
    assert item["notes"] == "2% organic"
    assert item["priority"] == "high"
    assert item["due_date"] == "2026-09-05"
    assert item["quantity"] == 2 and isinstance(item["quantity"], int)
    assert item["done"] is False
    assert item["recurrence"] == "none"
    assert item["recurrence_interval"] is None
    assert TS_RE.match(item["created_at"]) and TS_RE.match(item["updated_at"])
    iid = item["id"]

    # defaults
    dflt = make_item(client, lid, "Defaults")
    assert (dflt["notes"], dflt["priority"], dflt["due_date"], dflt["quantity"],
            dflt["recurrence"], dflt["done"]) == (None, "none", None, 1, "none", False)

    # fractional quantity stays float, integral becomes int — never 1.0
    half = make_item(client, lid, "Half", quantity=0.5)
    assert half["quantity"] == 0.5 and isinstance(half["quantity"], float)
    whole = make_item(client, lid, "Whole", quantity=1.0)
    assert whole["quantity"] == 1

    # GET /api/items returns everything in canonical order with full shape —
    # new items land on top (position 0), so newest id comes first.
    r = client.get("/api/items")
    assert r.status_code == 200
    assert [i["id"] for i in r.json()] == [whole["id"], half["id"], dflt["id"], iid]
    for i in r.json():
        assert set(i.keys()) == ITEM_KEYS
    assert [i["position"] for i in r.json()] == [0, 1, 2, 3]

    # PATCH partial update; envelope ALWAYS {item, spawned}
    r = client.patch(f"/api/items/{iid}", json={"title": "Buy oat milk", "quantity": 3})
    assert r.status_code == 200
    env = r.json()
    assert set(env.keys()) == {"item", "spawned"} and env["spawned"] is None
    assert env["item"]["title"] == "Buy oat milk"
    assert env["item"]["quantity"] == 3 and isinstance(env["item"]["quantity"], int)
    assert env["item"]["notes"] == "2% organic"      # untouched
    assert env["item"]["priority"] == "high"          # untouched

    # value-identical write still bumps updated_at
    r2 = client.patch(f"/api/items/{iid}", json={"title": "Buy oat milk"})
    assert r2.status_code == 200
    assert r2.json()["item"]["updated_at"] >= env["item"]["updated_at"]

    # toggle done, then back
    r = toggle(client, iid, True)
    assert r["item"]["done"] is True and r["spawned"] is None
    assert toggle(client, iid, False)["item"]["done"] is False
    # GET with status filters (done round-trip left positions untouched)
    assert [i["id"] for i in client.get("/api/items", params={"status": "pending"}).json()] \
        == [whole["id"], half["id"], dflt["id"], iid]
    assert client.get("/api/items", params={"status": "done"}).json() == []

    # DELETE -> 204 then gone; 404 when missing
    assert client.delete(f"/api/items/{iid}").status_code == 204
    assert client.get("/api/items").status_code == 200
    assert all(i["id"] != iid for i in client.get("/api/items").json())
    assert client.delete(f"/api/items/{iid}").status_code == 404
    assert client.patch(f"/api/items/{iid}", json={"title": "x"}).status_code == 404


def test_item_patch_clears_nullable_and_rejects_null(client):
    lid = make_list(client, "L")["id"]
    item = make_item(client, lid, "T", notes="note", due_date="2026-09-05",
                     priority="low", recurrence="weekly")

    r = client.patch(f"/api/items/{item['id']}", json={"notes": None, "due_date": None})
    assert r.status_code == 200
    got = r.json()["item"]
    assert got["notes"] is None and got["due_date"] is None

    # explicit null on non-nullable fields -> 422
    for payload in ({"title": None}, {"priority": None}, {"recurrence": None},
                    {"done": None}, {"quantity": None}, {"list_id": None}):
        rr = client.patch(f"/api/items/{item['id']}", json=payload)
        assert rr.status_code == 422, (payload, rr.text)

    # empty-after-trim title -> 422; too-long notes/title -> 422
    assert client.patch(f"/api/items/{item['id']}", json={"title": "   "}).status_code == 422
    assert client.patch(f"/api/items/{item['id']}", json={"title": "x" * 201}).status_code == 422
    assert client.patch(f"/api/items/{item['id']}", json={"notes": "x" * 5001}).status_code == 422

    # unknown field -> 422 (extra="forbid")
    assert client.patch(f"/api/items/{item['id']}", json={"bogus": 1}).status_code == 422


def test_validation_rejects_empty_title(client):
    lid = make_list(client, "L")["id"]
    r = client.post("/api/lists", json={"name": "   "})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    r = client.post("/api/items", json={"list_id": lid, "title": ""})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    r = client.post("/api/items", json={"list_id": lid, "title": "   "})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    # missing required field
    r = client.post("/api/items", json={"title": "no list"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_validation_rejects_bad_priority(client):
    lid = make_list(client, "L")["id"]
    for bad in ("urgent", "HIGH", "", None, 3):
        r = client.post("/api/items", json={"list_id": lid, "title": "T", "priority": bad})
        assert r.status_code == 422, (bad, r.text)


def test_validation_rejects_bad_due_date(client):
    lid = make_list(client, "L")["id"]
    for bad in ("2026-02-30", "2026-13-01", "2026-00-10", "not-a-date",
                "2026-9-05", "2026/09/05", "2026-09-05T10:00:00Z", "", 5):
        r = client.post("/api/items", json={"list_id": lid, "title": "T", "due_date": bad})
        assert r.status_code == 422, (bad, r.text)
        assert isinstance(r.json()["detail"], str)
    # and via PATCH
    item = make_item(client, lid, "T")
    r = client.patch(f"/api/items/{item['id']}", json={"due_date": "2026-02-30"})
    assert r.status_code == 422


def test_validation_quantity(client):
    lid = make_list(client, "L")["id"]
    for bad in (0, -1, -0.5, "lots", True):
        r = client.post("/api/items", json={"list_id": lid, "title": "T", "quantity": bad})
        assert r.status_code == 422, (bad, r.text)
    item = make_item(client, lid, "T", quantity=2)
    assert client.patch(f"/api/items/{item['id']}", json={"quantity": 0}).status_code == 422


def test_validation_unknown_list_id_409(client):
    r = client.post("/api/items", json={"list_id": 999999, "title": "T"})
    assert r.status_code == 409
    assert r.json() == {"detail": "Referenced list does not exist"}
    # moving an item to a missing list -> 409 too
    l1 = make_list(client, "A")["id"]
    item = make_item(client, l1, "T")
    r = client.patch(f"/api/items/{item['id']}", json={"list_id": 123456})
    assert r.status_code == 409
    assert r.json() == {"detail": "Referenced list does not exist"}
    # list_id typed wrong -> 422, not 409
    r = client.post("/api/items", json={"list_id": "abc", "title": "T"})
    assert r.status_code == 422


def test_validation_extra_fields(client):
    lid = make_list(client, "L")["id"]
    r = client.post("/api/lists", json={"name": "ok", "extra": 1})
    assert r.status_code == 422
    r = client.post("/api/items", json={"list_id": lid, "title": "T", "surprise": True})
    assert r.status_code == 422
    r = client.post("/api/lists/1/shares", json={"permission": "read", "nope": 1})
    assert r.status_code == 422
    for bad in ("banana", 1, None):
        r = client.post(f"/api/lists/{lid}/shares", json={"permission": bad})
        assert r.status_code == 422, bad


def test_malformed_json_400(client):
    r = client.post("/api/lists", content='{"name": ', headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json() == {"detail": "Invalid JSON body"}


def test_patch_empty_and_non_object_400(client):
    lid = make_list(client, "L")["id"]
    item = make_item(client, lid, "T")

    r = client.patch(f"/api/items/{item['id']}", json={})
    assert r.status_code == 400 and r.json() == {"detail": "No fields to update"}
    r = client.patch(f"/api/lists/{lid}", json={})
    assert r.status_code == 400 and r.json() == {"detail": "No fields to update"}

    for bad_body in ("[1, 2, 3]", '"just a string"', "42", "null"):
        r = client.patch(f"/api/items/{item['id']}", content=bad_body,
                         headers={"content-type": "application/json"})
        assert r.status_code == 400, bad_body
        assert r.json() == {"detail": "Request body must be a JSON object"}
    r = client.patch(f"/api/items/{item['id']}", content="{broken",
                     headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json() == {"detail": "Invalid JSON body"}


def test_error_shape_is_string_detail(client):
    lid = make_list(client, "L")["id"]
    make_item(client, lid, "T")
    cases = [
        ("get", "/api/nope", None),
        ("get", "/api/shared/DefinitelyNotAToken123", None),
        ("post", "/api/lists", {}),
        ("post", "/api/lists", {"name": "x", "extra": 1}),
        ("post", "/api/items", {"list_id": 999999, "title": "x"}),
        ("post", "/api/items", {"list_id": "abc", "title": "x"}),
        ("post", "/api/items", {"list_id": lid}),
        ("post", "/api/items", {"list_id": lid, "title": "x", "priority": "urgent"}),
        ("post", "/api/items", {"list_id": lid, "title": "x", "due_date": "2026-02-30"}),
        ("patch", "/api/lists/999999", {"name": "x"}),
        ("patch", "/api/items/999999", {"title": "x"}),
        ("delete", "/api/lists/999999", None),
        ("delete", "/api/items/999999", None),
        ("delete", "/api/shares/NoSuchToken", None),
        ("post", "/api/lists/999999/shares", {"permission": "read"}),
    ]
    for method, path, payload in cases:
        r = client.request(method, path, json=payload)
        assert r.status_code >= 400, (method, path)
        body = r.json()
        assert set(body.keys()) == {"detail"}, (method, path, body)
        assert isinstance(body["detail"], str), (method, path, body)


def test_delete_list_cascades_items(client):
    l1 = make_list(client, "One")["id"]
    l2 = make_list(client, "Two")["id"]
    i1 = make_item(client, l1, "a")
    make_item(client, l1, "b")
    keep = make_item(client, l2, "keep")
    share = client.post(f"/api/lists/{l1}/shares", json={"permission": "read"}).json()

    assert client.delete(f"/api/lists/{l1}").status_code == 204

    # l1's items and shares are gone (FK ON DELETE CASCADE); l2's remain
    remaining = client.get("/api/items").json()
    assert [i["id"] for i in remaining] == [keep["id"]]
    assert client.get("/api/shared/{t}".format(t=share["token"])).status_code == 404
    assert client.patch(f"/api/items/{i1['id']}", json={"done": True}).status_code == 404


# ---------------------------------------------------------------------------
# AC2 — canonical sort
# ---------------------------------------------------------------------------

def test_sort_order_done_then_position_then_id(make_client, sql):
    """Crafted rows with scrambled priorities/due dates, explicit positions,
    and a position tie — asserts the exact id sequence of the canonical
    ORDER BY: done ASC, position ASC, id ASC (DESIGN-reorder §1.2)."""
    T0 = "2026-09-01T08:00:00.000000Z"
    sql.execute("INSERT INTO lists (name, created_at, updated_at) VALUES ('Sort', ?, ?)", (T0, T0))
    lid = sql.execute("SELECT id FROM lists").fetchone()["id"]

    def ins(title, position, done, priority="none", due_date=None):
        cur = sql.execute(
            "INSERT INTO items (list_id, title, notes, priority, due_date, quantity,"
            " position, done, recurrence, recurrence_interval, created_at, updated_at)"
            " VALUES (?,?,NULL,?,?,1,?,?,'none',NULL,?,?)",
            (lid, title, priority, due_date, position, done, T0, T0),
        )
        return cur.lastrowid

    # Insertion order is scrambled so id order != position order. Priorities
    # and due dates are set deliberately non-monotonic — under the reorder
    # contract they no longer sort (position and id decide).
    pend_b = ins("pend pos1", 1, 0, priority="high", due_date="2026-09-10")
    done_b = ins("done pos1", 1, 1)
    pend_a = ins("pend pos0", 0, 0, priority="low")
    pend_e = ins("pend pos5", 5, 0)
    pend_f = ins("pend pos5", 5, 0)          # position tie -> id tie-break
    done_a = ins("done pos0", 0, 1)
    pend_c = ins("pend pos2", 2, 0, priority="medium", due_date="2026-08-01")
    done_c = ins("done pos2", 2, 1)

    with make_client() as client:
        r = client.get("/api/items")
        assert r.status_code == 200
        ids = [i["id"] for i in r.json()]
        # pending (done=0) sorted by position then id, then done group likewise
        assert ids == [pend_a, pend_b, pend_c, pend_e, pend_f,
                       done_a, done_b, done_c], ids


# ---------------------------------------------------------------------------
# AC3 — filters & search
# ---------------------------------------------------------------------------

def test_filter_by_list(client):
    l1 = make_list(client, "One")["id"]
    l2 = make_list(client, "Two")["id"]
    a = make_item(client, l1, "in one")
    make_item(client, l1, "also one")
    b = make_item(client, l2, "in two")
    make_item(client, l2, "also two")

    assert [i["id"] for i in client.get("/api/items", params={"list_id": l1}).json()] == \
        [a["id"] + 1, a["id"]]   # newest first: second create landed on top
    assert [i["id"] for i in client.get("/api/items", params={"list_id": l2}).json()] == \
        [b["id"] + 1, b["id"]]
    # unknown list -> 200 [] (empty subset, not an error)
    r = client.get("/api/items", params={"list_id": 424242})
    assert r.status_code == 200 and r.json() == []
    # invalid list_id -> 422
    assert client.get("/api/items", params={"list_id": "abc"}).status_code == 422
    assert client.get("/api/items", params={"list_id": 0}).status_code == 422
    assert client.get("/api/items", params={"list_id": -3}).status_code == 422


def test_filter_status_pending_done(client):
    lid = make_list(client, "L")["id"]
    a = make_item(client, lid, "a")
    b = make_item(client, lid, "b")
    c = make_item(client, lid, "c")
    toggle(client, b["id"], True)

    assert [i["id"] for i in client.get("/api/items", params={"status": "all"}).json()] \
        == [c["id"], a["id"], b["id"]]   # canonical: pending (pos) first, done last
    assert [i["id"] for i in client.get("/api/items", params={"status": "pending"}).json()] \
        == [c["id"], a["id"]]
    assert [i["id"] for i in client.get("/api/items", params={"status": "done"}).json()] \
        == [b["id"]]

    r = client.get("/api/items", params={"status": "urgent"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_search_title_and_notes_case_insensitive(client):
    lid = make_list(client, "L")["id"]
    a = make_item(client, lid, "Buy MILK", notes="Organic & fresh")
    b = make_item(client, lid, "Walk the dog", notes=None)
    make_item(client, lid, "Call dentist", notes="Tuesday 9am")

    def search(q):
        r = client.get("/api/items", params={"q": q})
        assert r.status_code == 200
        return [i["id"] for i in r.json()]

    assert search("milk") == [a["id"]]              # title, case-insensitive
    assert search("ORGANIC") == [a["id"]]           # notes, case-insensitive
    assert search("organic") == [a["id"]]
    assert search("the dog") == [b["id"]]           # substring mid-string
    dent_ids = search("dent")
    assert dent_ids == [dent_ids[0]] and len(dent_ids) == 1
    assert search("tuesday") == dent_ids
    assert search("zzz-no-match") == []
    # empty/whitespace q means no filter
    assert len(search("")) == 3
    assert len(search("   ")) == 3


def test_search_escapes_like_wildcards(client):
    lid = make_list(client, "L")["id"]
    pct1 = make_item(client, lid, "100% pure")
    pct2 = make_item(client, lid, "50% off sale")
    under = make_item(client, lid, "under_score")
    make_item(client, lid, "plain task")

    def search(q):
        return [i["id"] for i in client.get("/api/items", params={"q": q}).json()]

    # '%' and '_' must match literally, not act as LIKE wildcards
    assert search("%") == [pct2["id"], pct1["id"]]   # canonical: newest first
    assert search("_") == [under["id"]]
    assert search("100%") == [pct1["id"]]
    assert search("under_") == [under["id"]]


def test_filter_combined(client):
    l1 = make_list(client, "One")["id"]
    l2 = make_list(client, "Two")["id"]
    a = make_item(client, l1, "Buy milk", notes="2%")
    b = make_item(client, l1, "Buy bread", notes="whole wheat")
    make_item(client, l2, "Buy milk", notes="oat")
    toggle(client, a["id"], True)

    r = client.get("/api/items", params={"list_id": l1, "status": "pending", "q": "milk"})
    assert r.status_code == 200
    assert r.json() == []  # only matching item in l1 is done
    r = client.get("/api/items", params={"list_id": l1, "status": "done", "q": "MILK"})
    assert [i["id"] for i in r.json()] == [a["id"]]
    r = client.get("/api/items", params={"status": "pending", "q": "wheat"})
    assert [i["id"] for i in r.json()] == [b["id"]]


# ---------------------------------------------------------------------------
# AC4 — recurrence
# ---------------------------------------------------------------------------

def test_next_due_pure():
    """next_due/add_months are pure — unit-test directly (§8.4)."""
    from app.recurrence import add_months, next_due

    created = "2026-01-01T00:00:00.000000Z"
    assert next_due("2026-09-05", created, "daily", None) == date(2026, 9, 6)
    assert next_due("2026-09-05", created, "weekly", None) == date(2026, 9, 12)
    assert next_due("2026-01-31", created, "monthly", None) == date(2026, 2, 28)   # clamp
    assert next_due("2024-01-31", created, "monthly", None) == date(2024, 2, 29)   # leap
    assert next_due("2026-03-31", created, "monthly", None) == date(2026, 4, 30)
    assert next_due("2025-12-31", created, "monthly", None) == date(2026, 1, 31)   # year roll
    assert next_due("2026-09-05", created, "custom", 3) == date(2026, 9, 8)
    # created_at anchor when prev_due is None ("creation date if none")
    assert next_due(None, "2026-03-15T10:30:00.000000Z", "daily", None) == date(2026, 3, 16)
    assert next_due(None, "2026-03-15T10:30:00.000000Z", "monthly", None) == date(2026, 4, 15)
    assert next_due(None, "2026-03-15T10:30:00.000000Z", "custom", 10) == date(2026, 3, 25)
    assert next_due("2026-09-05", created, "none", None) is None

    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 8, 31), 1) == date(2026, 9, 30)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)


def _recurring_item(client, recurrence="weekly", due_date="2026-09-05", **over):
    lid = make_list(client, "R")["id"]
    payload = {"recurrence": recurrence, "due_date": due_date, "quantity": 1}
    payload.update(over)
    item = make_item(client, lid, "Repeat", **payload)
    return item


def test_recurrence_daily(client):
    item = _recurring_item(client, "daily")
    env = toggle(client, item["id"], True)
    assert env["item"]["done"] is True
    assert env["item"]["due_date"] == "2026-09-05"          # history kept
    spawned = env["spawned"]
    assert spawned is not None
    assert spawned["id"] != item["id"]
    assert spawned["due_date"] == "2026-09-06"
    assert spawned["done"] is False and spawned["recurrence"] == "daily"


def test_recurrence_weekly(client):
    item = _recurring_item(client, "weekly")
    spawned = toggle(client, item["id"], True)["spawned"]
    assert spawned["due_date"] == "2026-09-12"


def test_recurrence_monthly_month_end_clamp(client):
    item = _recurring_item(client, "monthly", due_date="2026-01-31")
    spawned = toggle(client, item["id"], True)["spawned"]
    assert spawned["due_date"] == "2026-02-28"   # Jan 31 + 1mo = Feb 28 (2026)
    # leap year
    item2 = _recurring_item(client, "monthly", due_date="2024-01-31")
    spawned2 = toggle(client, item2["id"], True)["spawned"]
    assert spawned2["due_date"] == "2024-02-29"


def test_recurrence_monthly_anchor_fallback(client):
    """No due_date -> anchored to the UTC calendar date of created_at."""
    item = _recurring_item(client, "monthly", due_date=None)
    spawned = toggle(client, item["id"], True)["spawned"]
    from app.recurrence import next_due
    expected = next_due(None, item["created_at"], "monthly", None).isoformat()
    assert spawned["due_date"] == expected


def test_recurrence_custom_interval(client):
    item = _recurring_item(client, "custom", recurrence_interval=4)
    spawned = toggle(client, item["id"], True)["spawned"]
    assert spawned["due_date"] == "2026-09-09"   # +4 days
    assert spawned["recurrence_interval"] == 4


def test_recurrence_spawn_copies_fields(client):
    lid = make_list(client, "R")["id"]
    item = make_item(client, lid, "Repeat", notes="copy me", priority="high",
                     quantity=2, recurrence="weekly", due_date="2026-09-05")
    env = toggle(client, item["id"], True)
    spawned = env["spawned"]
    for field in ("title", "notes", "priority", "quantity", "recurrence",
                  "recurrence_interval", "list_id"):
        assert spawned[field] == item[field], field
    assert spawned["done"] is False
    assert spawned["id"] != item["id"]
    assert spawned["created_at"] >= item["created_at"]
    assert TS_RE.match(spawned["created_at"])


def test_recurrence_toggle_idempotent_no_double_spawn(client):
    item = _recurring_item(client, "weekly")
    first = toggle(client, item["id"], True)
    assert first["spawned"] is not None
    items_after_first = client.get("/api/items").json()

    # second done:true on the same (already-done) item: no spawn, no bump
    second = toggle(client, item["id"], True)
    assert second["spawned"] is None
    assert second["item"]["done"] is True
    assert second["item"]["updated_at"] == first["item"]["updated_at"]
    assert len(client.get("/api/items").json()) == len(items_after_first)


def test_recurrence_undo_does_not_delete_spawn(client):
    item = _recurring_item(client, "weekly")
    first = toggle(client, item["id"], True)
    assert first["spawned"] is not None
    # un-done: spawn stays
    toggle(client, item["id"], False)
    all_items = client.get("/api/items").json()
    assert len(all_items) == 2
    assert any(i["id"] == first["spawned"]["id"] and i["done"] is False for i in all_items)
    # re-completing spawns one more (each false->true transition spawns once)
    again = toggle(client, item["id"], True)
    assert again["spawned"] is not None
    assert again["spawned"]["id"] != first["spawned"]["id"]
    assert len(client.get("/api/items").json()) == 3


def test_recurrence_validation(client):
    lid = make_list(client, "L")["id"]
    base = {"list_id": lid, "title": "T"}
    # custom without interval -> 422
    r = client.post("/api/items", json={**base, "recurrence": "custom"})
    assert r.status_code == 422
    # interval without custom -> 422 (even with recurrence omitted entirely)
    r = client.post("/api/items", json={**base, "recurrence": "daily",
                                        "recurrence_interval": 2})
    assert r.status_code == 422
    r = client.post("/api/items", json={**base, "recurrence_interval": 2})
    assert r.status_code == 422
    # interval must be int >= 1
    r = client.post("/api/items", json={**base, "recurrence": "custom",
                                        "recurrence_interval": 0})
    assert r.status_code == 422
    r = client.post("/api/items", json={**base, "recurrence": "custom",
                                        "recurrence_interval": "three"})
    assert r.status_code == 422
    # valid custom create passes
    item = make_item(client, lid, "Custom", recurrence="custom", recurrence_interval=5)
    # PATCH: leaving custom while an interval is present -> 422 (client must clear it)
    r = client.patch(f"/api/items/{item['id']}", json={"recurrence": "weekly"})
    assert r.status_code == 422
    # PATCH: interval while not custom -> 422
    plain = make_item(client, lid, "Plain")
    r = client.patch(f"/api/items/{plain['id']}", json={"recurrence_interval": 2})
    assert r.status_code == 422
    # PATCH: custom without interval on a non-custom item -> 422
    r = client.patch(f"/api/items/{plain['id']}", json={"recurrence": "custom"})
    assert r.status_code == 422
    # clearing the interval first makes the change legal
    r = client.patch(f"/api/items/{item['id']}",
                     json={"recurrence": "weekly", "recurrence_interval": None})
    assert r.status_code == 200
    assert r.json()["item"]["recurrence"] == "weekly"
    assert r.json()["item"]["recurrence_interval"] is None


# ---------------------------------------------------------------------------
# AC5 — shares
# ---------------------------------------------------------------------------

def _share(client, list_id, permission="read"):
    r = client.post(f"/api/lists/{list_id}/shares", json={"permission": permission})
    assert r.status_code == 201, r.text
    return r.json()


def test_share_create(client):
    lid = make_list(client, "Groceries")["id"]
    share = _share(client, lid, "edit")
    assert set(share.keys()) == {"token", "permission", "url", "created_at"}
    assert share["permission"] == "edit"
    assert TS_RE.match(share["created_at"])
    token = share["token"]
    assert len(token) >= 16
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token)
    assert share["url"].endswith(f"/share/{token}")
    # multiple active tokens per list are allowed
    read_share = _share(client, lid, "read")
    assert read_share["token"] != token
    assert client.post(f"/api/lists/{lid}/shares", json={"permission": "read"}).status_code == 201
    # unknown list -> 404; missing/invalid permission -> 422
    assert client.post("/api/lists/999999/shares", json={"permission": "read"}).status_code == 404
    assert client.post(f"/api/lists/{lid}/shares", json={}).status_code == 422
    assert client.post(f"/api/lists/{lid}/shares",
                       json={"permission": "write"}).status_code == 422


def test_share_get_read_and_edit(client):
    lid = make_list(client, "Groceries")["id"]
    a = make_item(client, lid, "Buy milk", priority="high", due_date="2026-09-05")
    make_item(client, lid, "Buy bread")
    for permission in ("read", "edit"):
        share = _share(client, lid, permission)
        r = client.get(f"/api/shared/{share['token']}")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"list", "items", "permission"}
        assert body["permission"] == permission
        assert set(body["list"].keys()) == LIST_KEYS
        assert body["list"]["name"] == "Groceries"
        assert body["list"]["item_count"] == 2 and body["list"]["pending_count"] == 2
        assert [i["id"] for i in body["items"]] == [a["id"] + 1, a["id"]]  # canonical, newest first
        for i in body["items"]:
            assert set(i.keys()) == ITEM_KEYS


def test_share_unknown_token_404(client):
    r = client.get("/api/shared/NoSuchToken12345678")
    assert r.status_code == 404
    assert r.json() == {"detail": "Share link not found or revoked"}


def test_share_edit_allows_write(client):
    l1 = make_list(client, "One")["id"]
    share = _share(client, l1, "edit")
    token = share["token"]

    # POST: binds to the shared list; list_id must not be sent
    r = client.post(f"/api/shared/{token}/items",
                    json={"title": "shared task", "priority": "high", "quantity": 2})
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["list_id"] == l1
    assert item["done"] is False and item["quantity"] == 2
    r = client.post(f"/api/shared/{token}/items",
                    json={"list_id": l1, "title": "nope"})
    assert r.status_code == 422   # extra field forbidden

    # GET sees it
    got = client.get(f"/api/shared/{token}").json()
    assert [i["id"] for i in got["items"]] == [item["id"]]

    # PATCH (incl. recurring spawn) + envelope
    r = client.patch(f"/api/shared/{token}/items/{item['id']}", json={"title": "renamed"})
    assert r.status_code == 200
    assert set(r.json().keys()) == {"item", "spawned"}
    assert r.json()["item"]["title"] == "renamed"
    r = client.patch(f"/api/shared/{token}/items/{item['id']}",
                     json={"recurrence": "daily", "due_date": "2026-09-05"})
    assert r.status_code == 200
    env = toggle_shared(client, token, item["id"], True)
    assert env["spawned"] is not None and env["spawned"]["due_date"] == "2026-09-06"
    # list_id in shared PATCH -> 422
    r = client.patch(f"/api/shared/{token}/items/{item['id']}", json={"list_id": l1})
    assert r.status_code == 422

    # DELETE removes the completed occurrence; the spawned one stays (history
    # rows are independent — §2.5).
    assert client.delete(f"/api/shared/{token}/items/{item['id']}").status_code == 204
    remaining = client.get(f"/api/shared/{token}").json()["items"]
    assert [i["id"] for i in remaining] == [env["spawned"]["id"]]
    assert remaining[0]["done"] is False
    assert client.delete(f"/api/shared/{token}/items/{item['id']}").status_code == 404


def toggle_shared(client, token, item_id, done=True):
    r = client.patch(f"/api/shared/{token}/items/{item_id}", json={"done": done})
    assert r.status_code == 200, r.text
    return r.json()


def test_share_read_forbids_write(client):
    lid = make_list(client, "L")["id"]
    item = make_item(client, lid, "T")
    token = _share(client, lid, "read")["token"]

    r = client.post(f"/api/shared/{token}/items", json={"title": "x"})
    assert r.status_code == 403 and r.json() == {"detail": "This shared list is read-only."}
    r = client.patch(f"/api/shared/{token}/items/{item['id']}", json={"title": "x"})
    assert r.status_code == 403 and r.json() == {"detail": "This shared list is read-only."}
    r = client.delete(f"/api/shared/{token}/items/{item['id']}")
    assert r.status_code == 403 and r.json() == {"detail": "This shared list is read-only."}
    # and nothing changed
    assert len(client.get(f"/api/shared/{token}").json()["items"]) == 1


def test_share_revoke_then_404(client):
    lid = make_list(client, "L")["id"]
    share = _share(client, lid, "read")
    token = share["token"]
    assert client.get(f"/api/shared/{token}").status_code == 200
    assert client.delete(f"/api/shares/{token}").status_code == 204
    r = client.get(f"/api/shared/{token}")
    assert r.status_code == 404 and r.json() == {"detail": "Share link not found or revoked"}
    assert client.delete(f"/api/shares/{token}").status_code == 404


def test_shared_write_item_belongs_to_list(client):
    l1 = make_list(client, "One")["id"]
    l2 = make_list(client, "Two")["id"]
    own = make_item(client, l1, "mine")
    other = make_item(client, l2, "theirs")
    token = _share(client, l1, "edit")["token"]

    # shared GET only exposes l1's items
    assert [i["id"] for i in client.get(f"/api/shared/{token}").json()["items"]] == [own["id"]]
    # PATCH/DELETE against an item that belongs to a different list -> 404
    r = client.patch(f"/api/shared/{token}/items/{other['id']}", json={"done": True})
    assert r.status_code == 404
    r = client.delete(f"/api/shared/{token}/items/{other['id']}")
    assert r.status_code == 404
    # the item is untouched
    shared_items = client.get(f"/api/shared/{token}").json()["items"]
    assert len(shared_items) == 1
    assert shared_items[0]["id"] == own["id"] and shared_items[0]["done"] is False
    all_items = client.get("/api/items").json()
    assert any(i["id"] == other["id"] and i["done"] is False for i in all_items)
    # unknown token -> 404 even for writes
    assert client.post("/api/shared/NoSuchToken1234/items",
                       json={"title": "x"}).status_code == 404


# ---------------------------------------------------------------------------
# AC6 — persistence across restart
# ---------------------------------------------------------------------------

def test_persistence_across_restart(make_client):
    with make_client() as c1:
        lst = make_list(c1, "Keep me")
        item = make_item(c1, lst["id"], "Still here", notes="persisted",
                         priority="medium", quantity=3)
        share = c1.post(f"/api/lists/{lst['id']}/shares",
                        json={"permission": "edit"}).json()
        c1.patch(f"/api/items/{item['id']}", json={"done": True})

    # "restart": brand-new app instance on the same file
    with make_client() as c2:
        lists = c2.get("/api/lists").json()
        assert len(lists) == 1 and lists[0]["name"] == "Keep me"
        assert lists[0]["item_count"] == 1 and lists[0]["pending_count"] == 0
        items = c2.get("/api/items").json()
        assert len(items) == 1
        assert items[0]["title"] == "Still here"
        assert items[0]["notes"] == "persisted"
        assert items[0]["done"] is True and items[0]["quantity"] == 3
        shared = c2.get(f"/api/shared/{share['token']}").json()
        assert shared["permission"] == "edit"
        assert [i["id"] for i in shared["items"]] == [item["id"]]


# ---------------------------------------------------------------------------
# SPA shell / static basics (server-side half of AC7/AC8)
# ---------------------------------------------------------------------------

def test_spa_shell_routes(client):
    for path in ("/", "/index.html", "/some/client/route", "/share/Ab3xY9zQwErT1uIoP2aSdF"):
        r = client.get(path)
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Taskflow" in r.text
    # unknown API path -> JSON 404, not the SPA shell
    r = client.get("/api/definitely/not/a/route")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not found"}


# ---------------------------------------------------------------------------
# DESIGN-reorder — new-on-top ordering + move-per-PATCH
# ---------------------------------------------------------------------------

def test_create_item_lands_on_top_and_shifts_others(client):
    """AC1 — each new pending item takes position 0; the same list's existing
    pending items shift down; done items and other lists are untouched."""
    l1 = make_list(client, "One")["id"]
    l2 = make_list(client, "Two")["id"]

    a = make_item(client, l1, "first")
    assert a["position"] == 0
    b = make_item(client, l1, "second")
    assert b["position"] == 0
    c = make_item(client, l1, "third")
    assert c["position"] == 0

    # canonical order: newest on top, positions compact 0..n-1
    items = client.get("/api/items", params={"list_id": l1}).json()
    assert [i["id"] for i in items] == [c["id"], b["id"], a["id"]]
    assert [i["position"] for i in items] == [0, 1, 2]

    # creating in another list does not shift l1
    x = make_item(client, l2, "other")
    assert x["position"] == 0
    items = client.get("/api/items", params={"list_id": l1}).json()
    assert [i["id"] for i in items] == [c["id"], b["id"], a["id"]]
    assert [i["position"] for i in items] == [0, 1, 2]

    # the shift targets pending (done = 0) only: done items keep their slot
    toggle(client, b["id"], True)          # l1: a@2, b@1(done), c@0
    d = make_item(client, l1, "fourth")
    items = client.get("/api/items", params={"list_id": l1}).json()
    assert [i["id"] for i in items] == [d["id"], c["id"], a["id"], b["id"]]
    assert [i["position"] for i in items] == [0, 1, 3, 1]   # gap: b done @1


def test_migration_position_idempotent_and_backfill(tmp_path):
    """AC2 — fresh schema has the column; double init_schema is fine; a
    legacy-shaped DB (no position column) gets it added with backfill
    position = id, exactly once."""
    from app.db import SCHEMA_SQL, connect, init_schema

    # fresh DB: column comes from CREATE TABLE itself
    fresh = connect(str(tmp_path / "fresh.db"))
    init_schema(fresh)
    cols = [r[1] for r in fresh.execute("PRAGMA table_info(items)").fetchall()]
    assert "position" in cols
    init_schema(fresh)                     # idempotent: no error, no dup column
    cols = [r[1] for r in fresh.execute("PRAGMA table_info(items)").fetchall()]
    assert cols.count("position") == 1
    fresh.close()

    # legacy-shaped DB: items table built WITHOUT the position column
    legacy_sql = SCHEMA_SQL.replace(
        "  position            INTEGER NOT NULL DEFAULT 0,\n", ""
    )
    conn = connect(str(tmp_path / "legacy.db"))
    conn.executescript(legacy_sql)
    conn.execute(
        "INSERT INTO lists (name, created_at, updated_at) VALUES ('L', 't', 't')"
    )
    lid = conn.execute("SELECT id FROM lists").fetchone()["id"]
    for i in range(3):
        conn.execute(
            "INSERT INTO items (list_id, title, notes, priority, due_date, quantity,"
            " done, recurrence, recurrence_interval, created_at, updated_at)"
            " VALUES (?,?,NULL,'none',NULL,1,0,'none',NULL,'t','t')",
            (lid, f"task {i}"),
        )
    rows = conn.execute("SELECT id FROM items ORDER BY id").fetchall()
    before = [(r["id"], r["id"]) for r in rows]     # backfill expectation

    init_schema(conn)                        # guarded migration fires
    cols = [r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()]
    assert "position" in cols
    got = [(r["id"], r["position"])
           for r in conn.execute("SELECT id, position FROM items ORDER BY id")]
    assert got == before

    init_schema(conn)                        # second pass: guarded, no-op
    got = [(r["id"], r["position"])
           for r in conn.execute("SELECT id, position FROM items ORDER BY id")]
    assert got == before
    conn.close()


def test_move_up_down_swaps_with_neighbor(client):
    """AC3 — move:up/down swaps position with the adjacent same-list pending
    item; response is {item, swapped}; updated_at bumps like any PATCH."""
    lid = make_list(client, "L")["id"]
    a = make_item(client, lid, "a")          # oldest -> bottom
    b = make_item(client, lid, "b")
    c = make_item(client, lid, "c")          # newest -> top

    assert [i["id"] for i in client.get("/api/items", params={"list_id": lid}).json()] \
        == [c["id"], b["id"], a["id"]]

    before = {i["id"]: i["updated_at"]
              for i in client.get("/api/items", params={"list_id": lid}).json()}

    r = client.patch(f"/api/items/{a['id']}", json={"move": "up"})
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"item", "swapped"}
    assert body["item"]["id"] == a["id"] and body["item"]["position"] == 1
    assert body["item"]["updated_at"] >= before[a["id"]]   # moved row bumped
    assert body["swapped"]["id"] == b["id"] and body["swapped"]["position"] == 2
    assert body["swapped"]["updated_at"] >= before[b["id"]]
    got = client.get("/api/items", params={"list_id": lid}).json()
    assert [i["id"] for i in got] == [c["id"], a["id"], b["id"]]
    assert [i["position"] for i in got] == [0, 1, 2]

    # and back down again
    r = client.patch(f"/api/items/{a['id']}", json={"move": "down"})
    assert r.status_code == 200
    body = r.json()
    assert body["item"]["id"] == a["id"] and body["item"]["position"] == 2
    assert body["swapped"]["id"] == b["id"] and body["swapped"]["position"] == 1
    got = client.get("/api/items", params={"list_id": lid}).json()
    assert [i["id"] for i in got] == [c["id"], b["id"], a["id"]]


def test_move_boundary_noop(client):
    """AC3 — top item move:up and bottom item move:down are 200 no-ops:
    swapped None, positions and updated_at untouched."""
    lid = make_list(client, "L")["id"]
    a = make_item(client, lid, "a")          # bottom
    b = make_item(client, lid, "b")          # top
    top, bottom = b, a

    before = client.get("/api/items", params={"list_id": lid}).json()
    assert [i["id"] for i in before] == [b["id"], a["id"]]

    for item_id, direction in ((top["id"], "up"), (bottom["id"], "down")):
        r = client.patch(f"/api/items/{item_id}", json={"move": direction})
        assert r.status_code == 200, (direction, r.text)
        body = r.json()
        assert set(body.keys()) == {"item", "swapped"}
        assert body["swapped"] is None
        row = next(i for i in before if i["id"] == item_id)
        assert body["item"]["id"] == item_id
        assert body["item"]["position"] == row["position"]      # unchanged
        assert body["item"]["updated_at"] == row["updated_at"]  # no bump

    got = client.get("/api/items", params={"list_id": lid}).json()
    assert [i["id"] for i in got] == [b["id"], a["id"]]
    assert [i["position"] for i in got] == [0, 1]


def test_move_scoped_to_list_and_done_group(client):
    """AC4 — a move only ever swaps within the same list AND the same done
    group: pending items can't drop into the done group, done items can't rise
    into pending, and other lists are never touched."""
    l1 = make_list(client, "One")["id"]
    l2 = make_list(client, "Two")["id"]
    p1 = make_item(client, l1, "p1")
    p2 = make_item(client, l1, "p2")
    d1 = make_item(client, l1, "d1")
    d2 = make_item(client, l1, "d2")
    q1 = make_item(client, l2, "q1")
    q2 = make_item(client, l2, "q2")
    toggle(client, d1["id"], True)
    toggle(client, d2["id"], True)
    # l1 canonical: p2, p1 (pending) then d2, d1 (done); l2: q2, q1
    assert [i["id"] for i in client.get("/api/items", params={"list_id": l1}).json()] \
        == [p2["id"], p1["id"], d2["id"], d1["id"]]
    assert [i["id"] for i in client.get("/api/items", params={"list_id": l2}).json()] \
        == [q2["id"], q1["id"]]

    # done item at the top of its done group: up is a no-op even though
    # pending rows sit above it in canonical order (different group)
    r = client.patch(f"/api/items/{d2['id']}", json={"move": "up"})
    assert r.status_code == 200 and r.json()["swapped"] is None

    # bottom pending item: down is a no-op — must not cross into done group
    r = client.patch(f"/api/items/{p1['id']}", json={"move": "down"})
    assert r.status_code == 200 and r.json()["swapped"] is None

    # cross-list: l2's top pending cannot rise past l1 rows either
    r = client.patch(f"/api/items/{q2['id']}", json={"move": "up"})
    assert r.status_code == 200 and r.json()["swapped"] is None

    # real swap within l1's pending group
    r = client.patch(f"/api/items/{p2['id']}", json={"move": "down"})
    assert r.json()["swapped"]["id"] == p1["id"]
    # real swap within l1's done group (up)
    r = client.patch(f"/api/items/{d1['id']}", json={"move": "up"})
    assert r.json()["swapped"]["id"] == d2["id"]
    got = client.get("/api/items", params={"list_id": l1}).json()
    assert [i["id"] for i in got] == [p1["id"], p2["id"], d1["id"], d2["id"]]
    # l2 never moved
    assert [i["id"] for i in client.get("/api/items", params={"list_id": l2}).json()] \
        == [q2["id"], q1["id"]]


def test_move_combined_with_other_fields_422(client):
    """move is an ordering op — combining it with any other PATCH field is a
    422; bad direction / null move are 422 too; nothing changes."""
    lid = make_list(client, "L")["id"]
    a = make_item(client, lid, "a")
    b = make_item(client, lid, "b")
    for payload in ({"move": "up", "done": True},
                    {"move": "down", "title": "renamed"},
                    {"move": "up", "list_id": lid},
                    {"move": "down", "recurrence": "daily"},
                    {"move": "up", "quantity": 3}):
        r = client.patch(f"/api/items/{a['id']}", json=payload)
        assert r.status_code == 422, (payload, r.text)
        assert "move cannot be combined with other fields" in r.json()["detail"]
    assert client.patch(f"/api/items/{a['id']}", json={"move": "left"}).status_code == 422
    assert client.patch(f"/api/items/{a['id']}", json={"move": None}).status_code == 422
    got = client.get("/api/items", params={"list_id": lid}).json()
    assert [i["id"] for i in got] == [b["id"], a["id"]]
    assert [i["position"] for i in got] == [0, 1]


def test_recurrence_spawn_lands_on_top(client):
    """§1.4 — a recurrence spawn is a fresh pending item: it lands at position
    0 and shifts the list's other pending items down one slot."""
    lid = make_list(client, "L")["id"]
    older = make_item(client, lid, "older")
    item = make_item(client, lid, "Repeat", recurrence="daily",
                     due_date="2026-09-05")
    env = toggle(client, item["id"], True)
    spawned = env["spawned"]
    assert spawned is not None
    assert spawned["position"] == 0
    got = client.get("/api/items", params={"list_id": lid}).json()
    assert [i["id"] for i in got] == [spawned["id"], older["id"], item["id"]]
    assert [i["position"] for i in got] == [0, 2, 1]   # item done @1; older 1->2


def test_shared_move_edit_works_readonly_403(client):
    """AC9 — the shared edit PATCH supports move ({item, swapped}); read-only
    shares still 403 and nothing changes."""
    lid = make_list(client, "L")["id"]
    a = make_item(client, lid, "a")
    b = make_item(client, lid, "b")          # top

    edit = _share(client, lid, "edit")
    r = client.patch(f"/api/shared/{edit['token']}/items/{a['id']}",
                     json={"move": "up"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"item", "swapped"}
    assert body["item"]["id"] == a["id"] and body["item"]["position"] == 0
    assert body["swapped"]["id"] == b["id"] and body["swapped"]["position"] == 1
    got = client.get(f"/api/shared/{edit['token']}").json()
    assert [i["id"] for i in got["items"]] == [a["id"], b["id"]]

    ro = _share(client, lid, "read")
    r = client.patch(f"/api/shared/{ro['token']}/items/{a['id']}",
                     json={"move": "down"})
    assert r.status_code == 403
    assert r.json() == {"detail": "This shared list is read-only."}
    got = client.get(f"/api/shared/{edit['token']}").json()
    assert [i["id"] for i in got["items"]] == [a["id"], b["id"]]   # unchanged


# ---------------------------------------------------------------------------
# DESIGN-fix-reorder — move_to single-request move
# ---------------------------------------------------------------------------

def _pending_four(client, lid):
    """Four pending items a(bottom) < b < c < d(top) — new-on-top."""
    a = make_item(client, lid, "a")
    b = make_item(client, lid, "b")
    c = make_item(client, lid, "c")
    d = make_item(client, lid, "d")
    return a, b, c, d


def _list_order(client, lid):
    items = client.get("/api/items", params={"list_id": lid}).json()
    return [i["id"] for i in items], [i["position"] for i in items]


def test_move_to_ordinal_k(client):
    """AC5 — move_to lands the item at group ordinal K; GET order matches."""
    lid = make_list(client, "L")["id"]
    a, b, c, d = _pending_four(client, lid)
    ids0, pos0 = _list_order(client, lid)
    assert ids0 == [d["id"], c["id"], b["id"], a["id"]]
    assert pos0 == [0, 1, 2, 3]

    # bottom item -> top (K=0)
    r = client.patch(f"/api/items/{a['id']}", json={"move_to": 0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"item", "spawned"} and body["spawned"] is None
    assert body["item"]["id"] == a["id"] and body["item"]["position"] == 0
    ids, pos = _list_order(client, lid)
    assert ids == [a["id"], d["id"], c["id"], b["id"]]
    assert pos == [0, 1, 2, 3]

    # middle item -> last slot (d at ordinal 1 -> K=3)
    r = client.patch(f"/api/items/{d['id']}", json={"move_to": 3})
    assert r.status_code == 200
    assert r.json()["item"]["id"] == d["id"]
    ids, pos = _list_order(client, lid)
    assert ids == [a["id"], c["id"], b["id"], d["id"]]
    assert pos == [0, 1, 2, 3]

    # and one item up one slot (c at ordinal 1 -> K=2)
    r = client.patch(f"/api/items/{c['id']}", json={"move_to": 2})
    assert r.status_code == 200
    ids, pos = _list_order(client, lid)
    assert ids == [a["id"], b["id"], c["id"], d["id"]]
    assert pos == [0, 1, 2, 3]


def test_move_to_noop_when_k_current(client):
    """AC5 — K == current ordinal is a 200 no-op: order unchanged, no
    updated_at bump anywhere."""
    lid = make_list(client, "L")["id"]
    a, b, c, d = _pending_four(client, lid)
    before = client.get("/api/items", params={"list_id": lid}).json()
    assert [i["id"] for i in before] == [d["id"], c["id"], b["id"], a["id"]]
    row_c = next(i for i in before if i["id"] == c["id"])   # ordinal 1

    r = client.patch(f"/api/items/{c['id']}", json={"move_to": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["item"]["id"] == c["id"]
    assert body["item"]["position"] == 1
    assert body["item"]["updated_at"] == row_c["updated_at"]   # no bump

    after = client.get("/api/items", params={"list_id": lid}).json()
    assert [i["id"] for i in after] == [d["id"], c["id"], b["id"], a["id"]]
    assert [i["position"] for i in after] == [0, 1, 2, 3]
    assert {i["id"]: i["updated_at"] for i in after} == \
        {i["id"]: i["updated_at"] for i in before}


def test_move_to_out_of_range_clamps(client):
    """AC5 — K beyond the group clamps to the last slot; negative K is a 422
    (schema ge=0)."""
    lid = make_list(client, "L")["id"]
    a, b, c, d = _pending_four(client, lid)
    # top item with a huge K -> clamped to the very last slot
    r = client.patch(f"/api/items/{d['id']}", json={"move_to": 99})
    assert r.status_code == 200, r.text
    ids, pos = _list_order(client, lid)
    assert ids == [c["id"], b["id"], a["id"], d["id"]]
    assert pos == [0, 1, 2, 3]

    # bottom item with a huge K -> clamps back onto its own slot: 200 no-op,
    # order AND updated_at unchanged
    before = client.get("/api/items", params={"list_id": lid}).json()
    row_d = next(i for i in before if i["id"] == d["id"])
    r = client.patch(f"/api/items/{d['id']}", json={"move_to": 42})
    assert r.status_code == 200
    ids, pos = _list_order(client, lid)
    assert ids == [c["id"], b["id"], a["id"], d["id"]]
    after = client.get("/api/items", params={"list_id": lid}).json()
    assert next(i for i in after if i["id"] == d["id"])["updated_at"] == \
        row_d["updated_at"]
    assert {i["id"]: i["updated_at"] for i in after} == \
        {i["id"]: i["updated_at"] for i in before}

    # negative / non-integer K -> 422 (nothing changes)
    for bad in (-1, -5, "x", 1.5, True):
        rr = client.patch(f"/api/items/{d['id']}", json={"move_to": bad})
        assert rr.status_code == 422, (bad, rr.text)
    ids, pos = _list_order(client, lid)
    assert ids == [c["id"], b["id"], a["id"], d["id"]]
    assert pos == [0, 1, 2, 3]


def test_move_to_scoped_to_list_and_done_group(client):
    """AC5 — move_to reorders only within the same list AND same done group:
    pending never crosses into done (or vice versa) even with a huge K, and
    other lists are untouched."""
    l1 = make_list(client, "One")["id"]
    l2 = make_list(client, "Two")["id"]
    p1 = make_item(client, l1, "p1")
    p2 = make_item(client, l1, "p2")
    d1 = make_item(client, l1, "d1")
    d2 = make_item(client, l1, "d2")
    q1 = make_item(client, l2, "q1")
    q2 = make_item(client, l2, "q2")
    toggle(client, d1["id"], True)
    toggle(client, d2["id"], True)
    # l1 canonical: p2, p1 (pending) then d2, d1 (done); l2: q2, q1
    ids1, _ = _list_order(client, l1)
    assert ids1 == [p2["id"], p1["id"], d2["id"], d1["id"]]

    # bottom pending -> top of the pending group only (K=0)
    r = client.patch(f"/api/items/{p1['id']}", json={"move_to": 0})
    assert r.status_code == 200
    ids1, _ = _list_order(client, l1)
    assert ids1 == [p1["id"], p2["id"], d2["id"], d1["id"]]

    # done item with a huge K: clamps inside the done group — cannot rise
    # into the pending group, cannot leave its list
    r = client.patch(f"/api/items/{d2['id']}", json={"move_to": 99})
    assert r.status_code == 200
    ids1, _ = _list_order(client, l1)
    assert ids1 == [p1["id"], p2["id"], d1["id"], d2["id"]]
    # done group internal order (d1 before d2) and pending untouched
    assert ids1[:2] == [p1["id"], p2["id"]]

    # other list never moved
    ids2, _ = _list_order(client, l2)
    assert ids2 == [q2["id"], q1["id"]]


def test_move_to_combined_with_other_fields_422(client):
    """AC5 — move_to is an ordering op: combined with any other field (or with
    move) -> 422; nothing changes."""
    lid = make_list(client, "L")["id"]
    a = make_item(client, lid, "a")
    b = make_item(client, lid, "b")
    for payload in ({"move_to": 1, "title": "renamed"},
                    {"move_to": 0, "done": True},
                    {"move_to": 1, "move": "up"},
                    {"move_to": 0, "list_id": lid},
                    {"move_to": 2, "quantity": 3}):
        r = client.patch(f"/api/items/{a['id']}", json=payload)
        assert r.status_code == 422, (payload, r.text)
        assert "cannot be combined with other fields" in r.json()["detail"]
    # explicit null move_to -> 422 too
    assert client.patch(f"/api/items/{a['id']}", json={"move_to": None}).status_code == 422
    ids, pos = _list_order(client, lid)
    assert ids == [b["id"], a["id"]]
    assert pos == [0, 1]


def test_shared_move_to_edit_works_readonly_403(client):
    """AC5 — the shared edit PATCH supports move_to; read-only shares still
    403 and nothing changes."""
    lid = make_list(client, "L")["id"]
    a = make_item(client, lid, "a")
    b = make_item(client, lid, "b")          # top

    edit = _share(client, lid, "edit")
    r = client.patch(f"/api/shared/{edit['token']}/items/{a['id']}",
                     json={"move_to": 0})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["id"] == a["id"] and r.json()["item"]["position"] == 0
    got = client.get(f"/api/shared/{edit['token']}").json()
    assert [i["id"] for i in got["items"]] == [a["id"], b["id"]]

    ro = _share(client, lid, "read")
    r = client.patch(f"/api/shared/{ro['token']}/items/{a['id']}",
                     json={"move_to": 1})
    assert r.status_code == 403
    assert r.json() == {"detail": "This shared list is read-only."}
    got = client.get(f"/api/shared/{edit['token']}").json()
    assert [i["id"] for i in got["items"]] == [a["id"], b["id"]]   # unchanged
