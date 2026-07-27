"""Shared helpers for attachment tests (not collected — no test_ prefix)."""

from __future__ import annotations

import hashlib
import io
import uuid

from mesh.attachment.service import AttachmentService
from mesh.config import load_settings

PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def make_png(width: int = 64, height: int = 48, color=(200, 30, 30)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_service(session_factory, object_storage, settings_kwargs: dict) -> AttachmentService:
    settings = load_settings(**settings_kwargs)
    return AttachmentService(session_factory, settings, object_storage)


async def seed_issue(session_factory, workspace, *, title: str = "Host issue"):
    """A minimal issue row for link_to / listing tests.

    Reuses an existing workspace status when the API already bootstrapped the
    default board; only raw-seeded workspaces need a fresh status row.
    """
    from sqlalchemy import select

    from mesh.db.models.issue import Issue, IssueStatus

    async with session_factory() as session, session.begin():
        status = await session.scalar(
            select(IssueStatus).where(IssueStatus.workspace_id == workspace.id).limit(1)
        )
        if status is None:
            status = IssueStatus(
                workspace_id=workspace.id,
                name=f"status-{uuid.uuid4().hex[:8]}",
                category="todo",
                is_default=True,
            )
            session.add(status)
            await session.flush()
        suffix = uuid.uuid4().hex[:6]
        issue = Issue(
            workspace_id=workspace.id,
            identifier_namespace_key=f"ws:{workspace.id}",
            number=abs(hash(suffix)) % 100000,
            identifier=f"WS-{suffix}",
            title=title,
            status_id=status.id,
            state_category="todo",
        )
        session.add(issue)
    return issue
