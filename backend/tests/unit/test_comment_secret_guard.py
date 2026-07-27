"""Comment-channel full-channel redaction (§6.16 / runtime.md R12 / §5 red line).

A comment carrying a workspace secret is rejected 422 ``secret_detected``
BEFORE persistence/broadcast, with a critical audit trail — same posture as
the attachment channel. Covers create and edit write paths.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.comment_inbox.service import CommentService
from mesh.db.models.audit import AuditLog
from mesh.db.models.comment import Comment
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.runtime import RuntimeCredential
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.errors import BusinessRuleError
from mesh.runtime.credentials import encrypt_credential_value
from tests.unit.runtime_support import TEST_JWT_SECRET

pytestmark = pytest.mark.unit

SECRET = "comment-leak-777"


async def _env(session_factory) -> dict:
    async with session_factory() as session, session.begin():
        workspace = Workspace(name="W", slug=f"ws-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
        await session.flush()
        user = User(email=f"alice-{uuid.uuid4().hex[:6]}@x.io", display_name="Alice")
        session.add(user)
        await session.flush()
        author = Member(
            workspace_id=workspace.id, member_type="human", user_id=user.id, role="member"
        )
        session.add(author)
        await session.flush()
        status = IssueStatus(
            workspace_id=workspace.id, name="Todo", category="todo", is_default=False
        )
        session.add(status)
        await session.flush()
        namespace = f"sec{uuid.uuid4().hex[:6]}"
        issue = Issue(
            workspace_id=workspace.id,
            identifier_namespace_key=namespace,
            number=1,
            identifier=f"{namespace.upper()}-1",
            title="sec issue",
            status_id=status.id,
            state_category="todo",
            reporter_id=author.id,
        )
        session.add(issue)
        await session.flush()
        session.add(
            RuntimeCredential(
                workspace_id=workspace.id,
                name="LEAKY",
                encrypted_value=encrypt_credential_value(SECRET, TEST_JWT_SECRET),
                redact_in_logs=True,
            )
        )
    service = CommentService(session_factory, signing_secret=TEST_JWT_SECRET)
    return {
        "factory": session_factory,
        "workspace": workspace,
        "author": author,
        "issue": issue,
        "service": service,
    }


async def test_create_with_secret_rejected_not_persisted_and_audited(session_factory):
    env = await _env(session_factory)
    with pytest.raises(BusinessRuleError) as exc:
        await env["service"].create_comment(
            workspace_id=env["workspace"].id,
            issue_id=env["issue"].id,
            author_member=env["author"],
            body_markdown=f"here is the key {SECRET} do not share",
        )
    assert exc.value.code == "secret_detected"
    assert exc.value.details["hits"] == 1
    async with session_factory() as session:
        comments = (
            await session.execute(
                select(Comment).where(Comment.workspace_id == env["workspace"].id)
            )
        ).scalars().all()
        audit = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "comment.secret_detected")
            )
        ).scalars().all()
    assert comments == []  # nothing persisted (and thus nothing broadcast)
    assert len(audit) == 1
    assert audit[0].metadata_["severity"] == "critical"
    assert audit[0].metadata_["channel"] == "comment"


async def test_create_clean_body_passes(session_factory):
    env = await _env(session_factory)
    data = await env["service"].create_comment(
        workspace_id=env["workspace"].id,
        issue_id=env["issue"].id,
        author_member=env["author"],
        body_markdown="a perfectly normal comment",
    )
    assert data["body_markdown"] == "a perfectly normal comment"


async def test_edit_with_secret_rejected_body_unchanged(session_factory):
    env = await _env(session_factory)
    created = await env["service"].create_comment(
        workspace_id=env["workspace"].id,
        issue_id=env["issue"].id,
        author_member=env["author"],
        body_markdown="original clean body",
    )
    with pytest.raises(BusinessRuleError) as exc:
        await env["service"].update_comment(
            workspace_id=env["workspace"].id,
            comment_id=uuid.UUID(created["id"]),
            editor_member=env["author"],
            is_manager=False,
            body_markdown=f"edited to leak {SECRET}",
        )
    assert exc.value.code == "secret_detected"
    async with session_factory() as session:
        stored = await session.get(Comment, uuid.UUID(created["id"]))
    assert stored.body_markdown == "original clean body"  # edit not persisted


async def test_guard_inert_without_signing_key(session_factory):
    """Services constructed without a key (unit scope) skip the scan — the
    app always wires settings.jwt_secret (wiring asserted separately)."""
    env = await _env(session_factory)
    inert = CommentService(session_factory)  # no signing_secret
    data = await inert.create_comment(
        workspace_id=env["workspace"].id,
        issue_id=env["issue"].id,
        author_member=env["author"],
        body_markdown=f"body mentioning {SECRET} without a wired key",
    )
    assert SECRET in data["body_markdown"]
