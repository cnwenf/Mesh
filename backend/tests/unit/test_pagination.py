"""Keyset cursor pagination (§6.14)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mesh.api.pagination import decode_cursor, encode_cursor, paginate
from mesh.db.models.workspace import Workspace
from mesh.errors import ValidationError


def test_cursor_roundtrip_datetime():
    moment = datetime(2026, 7, 25, 12, 30, 5, 123456, tzinfo=UTC)
    row_id = uuid.uuid4()
    decoded = decode_cursor(encode_cursor(moment, row_id))
    assert decoded.sort_value == moment
    assert decoded.id == row_id


def test_cursor_roundtrip_int_and_str_and_float():
    assert decode_cursor(encode_cursor(42, 7)).sort_value == 42
    assert decode_cursor(encode_cursor("abc", 1)).sort_value == "abc"
    assert decode_cursor(encode_cursor(1.5, 2)).sort_value == 1.5


def test_encode_rejects_bool_sort_value():
    with pytest.raises(ValueError):
        encode_cursor(True, 1)


@pytest.mark.parametrize("raw", ["", "!!!", "eyJ0IjogIngiLCAicyI6IDEsICJpIjogIm5vdC11dWlkIn0", "aGVsbG8="])
def test_decode_invalid_cursor_raises_validation_error(raw):
    with pytest.raises(ValidationError) as excinfo:
        decode_cursor(raw)
    assert excinfo.value.code == "invalid_cursor"
    assert excinfo.value.status_code == 400


async def test_paginate_walks_all_rows_without_duplicates(db_session, session_factory):
    # Seed 5 committed workspaces via a separate session.
    async with session_factory() as session, session.begin():
        for i in range(5):
            session.add(Workspace(name=f"W{i}", slug=f"page-{uuid.uuid4().hex[:10]}"))

    seen: list[uuid.UUID] = []
    cursor = None
    pages = 0
    stmt = select(Workspace)
    while True:
        page = await paginate(
            db_session,
            stmt,
            sort_column=Workspace.created_at,
            id_column=Workspace.id,
            sort_value_of=lambda row: row.created_at,
            id_of=lambda row: row.id,
            cursor=cursor,
            limit=2,
        )
        pages += 1
        seen.extend(row.id for row in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(seen) == 5
    assert len(set(seen)) == 5  # no duplicates
    assert pages == 3  # 2 + 2 + 1


async def test_paginate_last_page_has_null_cursor(db_session, session_factory):
    async with session_factory() as session, session.begin():
        session.add(Workspace(name="Solo", slug=f"solo-{uuid.uuid4().hex[:8]}"))
    page = await paginate(
        db_session,
        select(Workspace),
        sort_column=Workspace.created_at,
        id_column=Workspace.id,
        sort_value_of=lambda row: row.created_at,
        id_of=lambda row: row.id,
        limit=10,
    )
    assert page.next_cursor is None
    assert len(page.items) == 1


async def test_paginate_descending_is_reverse_of_ascending(db_session, session_factory):
    async with session_factory() as session, session.begin():
        for i in range(3):
            session.add(Workspace(name=f"D{i}", slug=f"desc-{uuid.uuid4().hex[:8]}"))

    async def _walk(descending: bool) -> list[uuid.UUID]:
        ids: list[uuid.UUID] = []
        cursor = None
        while True:
            page = await paginate(
                db_session,
                select(Workspace),
                sort_column=Workspace.created_at,
                id_column=Workspace.id,
                sort_value_of=lambda row: row.created_at,
                id_of=lambda row: row.id,
                cursor=cursor,
                limit=2,
                descending=descending,
            )
            ids.extend(row.id for row in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return ids

    ascending = await _walk(descending=False)
    descending = await _walk(descending=True)
    assert len(ascending) == 3
    assert descending == list(reversed(ascending))


async def test_paginate_rejects_bad_limit(db_session):
    with pytest.raises(ValidationError) as excinfo:
        await paginate(
            db_session,
            select(Workspace),
            sort_column=Workspace.created_at,
            id_column=Workspace.id,
            sort_value_of=lambda row: row.created_at,
            id_of=lambda row: row.id,
            limit=0,
        )
    assert excinfo.value.code == "invalid_limit"
