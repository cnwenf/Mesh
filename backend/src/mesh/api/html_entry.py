"""SPA entry document probe (search-command-palette.md §3.4 execution layer).

The reverse proxy gates ``/w/{slug}/…`` document requests through this
endpoint via ``auth_request``. It resolves the workspace slug WITHOUT auth
(it is a public entry probe that returns no data):

* **current slug** → ``200`` empty body with ``X-Mesh-Entry: ok`` — the proxy
  serves the SPA index and the client router takes over;
* **historical slug** (released by a rename, workspace.md §2.5 / W6) → a real
  ``HTTP 301`` with ``Location`` rebuilt on the current slug, preserving the
  query string. URL fragments never reach the server; the browser inherits
  them per redirect semantics (RFC 7231 / WHATWG fetch);
* **unknown slug** → ``200`` ok — the SPA renders its not-found state.

Slug history is read through ``mesh_workspace_id_by_old_slug`` (SECURITY
DEFINER, migration 0004): the caller has no tenant context yet, and the RLS
policy on ``workspace_slug_history`` is fail-closed without the GUC.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session
from mesh.db.models.workspace import Workspace

router = APIRouter(tags=["entry"])

_ENTRY_OK_HEADER = {"X-Mesh-Entry": "ok"}


async def _probe(request: Request, session: AsyncSession, slug: str, subpath: str) -> Response:
    """Shared probe logic for the with-path and without-path route variants."""
    current = await session.scalar(
        select(Workspace.slug).where(Workspace.slug == slug, Workspace.deleted_at.is_(None))
    )
    if current is not None:
        return Response(status_code=200, headers=dict(_ENTRY_OK_HEADER))

    workspace_id = (
        await session.execute(
            text("SELECT mesh_workspace_id_by_old_slug(:slug)"), {"slug": slug}
        )
    ).scalar()
    new_slug: str | None = None
    if workspace_id is not None:
        new_slug = await session.scalar(
            select(Workspace.slug).where(
                Workspace.id == workspace_id, Workspace.deleted_at.is_(None)
            )
        )
    if new_slug is None:
        # Unknown slug: let the SPA render its not-found state.
        return Response(status_code=200, headers=dict(_ENTRY_OK_HEADER))

    rebuilt = f"/w/{new_slug}"
    if subpath != "":
        rebuilt = f"{rebuilt}/{subpath}"
    query = request.url.query
    if query != "":
        rebuilt = f"{rebuilt}?{query}"
    return Response(status_code=301, headers={"Location": rebuilt})


@router.get("/__mesh_entry/w/{slug}/{subpath:path}")
async def mesh_entry_with_path(
    request: Request,
    slug: str,
    subpath: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Entry probe for ``/w/{slug}/{path}`` document requests (auth_request)."""
    return await _probe(request, session, slug, subpath)


@router.get("/__mesh_entry/w/{slug}")
async def mesh_entry_bare(
    request: Request,
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Entry probe for the workspace root ``/w/{slug}`` document request."""
    return await _probe(request, session, slug, "")
