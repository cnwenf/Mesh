"""Success envelopes (README §6.14 — 唯一权威).

Single object: ``{"data": {...}}`` — list: ``{"data": [...], "next_cursor": <opaque|null>}``.
``next_cursor=null`` means the last page.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class DataEnvelope(BaseModel, Generic[T]):
    """Single-object response envelope."""

    data: T


class ListEnvelope(BaseModel, Generic[T]):
    """List response envelope with opaque cursor pagination."""

    data: list[T]
    next_cursor: str | None = None
