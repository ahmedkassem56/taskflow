"""Pydantic v2 request models — DESIGN.md §2.

Rules enforced here (Pydantic is the primary validator; the DB CHECKs in §3
are last-resort guards):
- extra="forbid" on every body model (unknown fields => 422).
- name/title trimmed of surrounding whitespace first; empty after trim => 422.
- due_date: strict YYYY-MM-DD string that must parse via
  datetime.date.fromisoformat (rejects 2026-02-30), or null.
- quantity: number > 0, never null.
- recurrence_interval: int >= 1, only meaningful for recurrence='custom'.
  For CREATE the full-state rule is enforced here; for PATCH (partial) the
  same rule is enforced on the *merged* row state in main.py (§2.4).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal, Optional

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

Priority = Literal["none", "low", "medium", "high"]
Recurrence = Literal["none", "daily", "weekly", "monthly", "custom"]
Permission = Literal["read", "edit"]
Status = Literal["all", "pending", "done"]


def _strip(value):
    return value.strip() if isinstance(value, str) else value


def _calendar_date(value):
    """Strict YYYY-MM-DD; calendar validity via date.fromisoformat."""
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError("must be a calendar date in YYYY-MM-DD format")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("must be a calendar date in YYYY-MM-DD format") from exc
    return value


# Trimmed strings (strip before length constraints run) and strict date strings.
TrimmedStr = Annotated[str, BeforeValidator(_strip)]
DateStr = Annotated[Optional[str], AfterValidator(_calendar_date)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListCreate(_StrictModel):
    """POST /api/lists and PATCH /api/lists/{id} body (rename)."""

    name: TrimmedStr = Field(min_length=1, max_length=200)


class ShareCreate(_StrictModel):
    """POST /api/lists/{id}/shares body."""

    permission: Permission


class _ItemCreateFields(_StrictModel):
    """Fields shared by POST /api/items and POST /api/shared/{token}/items."""

    title: TrimmedStr = Field(min_length=1, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=5000)
    priority: Priority = "none"
    due_date: DateStr = None
    quantity: float = Field(default=1, gt=0)
    recurrence: Recurrence = "none"
    recurrence_interval: Optional[int] = Field(default=None, ge=1)

    @field_validator("quantity", "recurrence_interval", mode="before")
    @classmethod
    def _numeric_not_boolean(cls, value, info):
        # JSON true/false are not numbers (pydantic lax mode would coerce bool).
        if isinstance(value, bool):
            raise ValueError(f"{info.field_name} must be a JSON number, not a boolean")
        return value

    @model_validator(mode="after")
    def _interval_rule(self):
        if self.recurrence == "custom" and self.recurrence_interval is None:
            raise ValueError(
                "recurrence_interval is required when recurrence is 'custom'"
            )
        if self.recurrence != "custom" and self.recurrence_interval is not None:
            raise ValueError(
                "recurrence_interval must be null unless recurrence is 'custom'"
            )
        return self


class ItemCreate(_ItemCreateFields):
    list_id: int = Field(gt=0)

    @field_validator("list_id", mode="before")
    @classmethod
    def _list_id_not_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("list_id must be an integer, not a boolean")
        return value


class SharedItemCreate(_ItemCreateFields):
    """Same as ItemCreate but list_id is forbidden — the server binds to the
    shared list (§2.3 #13)."""

    pass


class _ItemPatchFields(_StrictModel):
    """PATCH /api/items/{id} body — partial update; every field optional.

    Absent fields are untouched; null clears due_date/notes (and may clear
    recurrence_interval); null is rejected for every non-nullable field.
    """

    title: Optional[TrimmedStr] = Field(default=None, min_length=1, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=5000)
    priority: Optional[Priority] = None
    due_date: DateStr = None
    quantity: Optional[float] = Field(default=None, gt=0)
    recurrence: Optional[Recurrence] = None
    recurrence_interval: Optional[int] = Field(default=None, ge=1)
    done: Optional[bool] = None
    move: Optional[Literal["up", "down"]] = None
    move_to: Optional[int] = Field(default=None, ge=0)

    @field_validator("title", "priority", "recurrence", "done", "move", "move_to",
                     mode="after")
    @classmethod
    def _non_nullable_not_null(cls, value, info):
        if value is None:
            raise ValueError(f"{info.field_name} cannot be null")
        return value

    @model_validator(mode="after")
    def _move_exclusive(self):
        # `move` and `move_to` are ordering ops (DESIGN-reorder §1.4,
        # DESIGN-fix-reorder §1.1) — each is mutually exclusive with every
        # other provided field (including each other).
        if len(self.model_fields_set) > 1:
            if "move_to" in self.model_fields_set:
                raise ValueError("move/move_to cannot be combined with other fields")
            if "move" in self.model_fields_set:
                raise ValueError("move cannot be combined with other fields")
        return self

    @field_validator("quantity", mode="after")
    @classmethod
    def _quantity_not_null(cls, value):
        if value is None:
            raise ValueError("quantity cannot be null")
        return value

    @field_validator("quantity", "recurrence_interval", "move_to", mode="before")
    @classmethod
    def _numeric_not_boolean(cls, value, info):
        # JSON true/false are not numbers (pydantic lax mode would coerce bool).
        if isinstance(value, bool):
            raise ValueError(f"{info.field_name} must be a JSON number, not a boolean")
        return value


class ItemPatch(_ItemPatchFields):
    list_id: Optional[int] = Field(default=None, gt=0)

    @field_validator("list_id", mode="after")
    @classmethod
    def _list_id_not_null(cls, value):
        if value is None:
            raise ValueError("list_id cannot be null")
        return value

    @field_validator("list_id", mode="before")
    @classmethod
    def _list_id_not_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("list_id must be an integer, not a boolean")
        return value


class SharedItemPatch(_ItemPatchFields):
    """PATCH /api/shared/{token}/items/{item_id} body — list_id forbidden."""

    pass
