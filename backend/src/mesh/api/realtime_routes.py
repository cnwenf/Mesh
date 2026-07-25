"""Realtime reconciliation REST endpoint (README §6.7).

After a ``resync_required`` frame the client full-pulls the channel here with
``since=<resume_from>`` and merges, then resumes seamlessly. Authorization uses
the same resource-level authorizer as the WebSocket gateway.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import current_principal, get_session
from mesh.api.envelope import ListEnvelope
from mesh.api.pagination import Page, paginate
from mesh.db.models.realtime import RealtimeEvent
from mesh.errors import ForbiddenError, ValidationError
from mesh.realtime.auth import Principal
from mesh.realtime.channels import is_valid_channel

router = APIRouter(prefix="/api/v1/realtime", tags=["realtime"])


class ReconciledEvent(BaseModel):
    """One stored realtime event returned by reconciliation."""

    channel: str
    seq: int
    event: str
    payload: dict


@router.get("/events", response_model=ListEnvelope[ReconciledEvent])
async def reconcile_events(
    request: Request,
    channel: str = Query(..., min_length=1),
    since: int = Query(0, ge=0),
    cursor: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> ListEnvelope[ReconciledEvent]:
    """Return stored events for ``channel`` with ``seq > since`` (keyset paged)."""
    if not is_valid_channel(channel):
        raise ValidationError("invalid channel name", code="invalid_channel")
    authorizer = request.app.state.authorizer
    if not await authorizer.authorize(principal, channel):
        raise ForbiddenError("not authorized for channel")

    stmt = select(RealtimeEvent).where(
        RealtimeEvent.channel == channel, RealtimeEvent.seq > since
    )
    page: Page = await paginate(
        session,
        stmt,
        sort_column=RealtimeEvent.seq,
        id_column=RealtimeEvent.id,
        sort_value_of=lambda row: row.seq,
        id_of=lambda row: row.id,
        cursor=cursor,
        limit=limit,
    )
    data = [
        ReconciledEvent(channel=row.channel, seq=row.seq, event=row.event, payload=row.payload)
        for row in page.items
    ]
    return ListEnvelope(data=data, next_cursor=page.next_cursor)
