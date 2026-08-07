"""Branch-coverage supplements for the comment-inbox module.

Targets the paths the matrix tests miss: the member-inbox channel checker
(direct invocation — the gateway subprocess coverage is not counted), the
digest sweep loop, sanitizer whitelist branches (fed pre-rendered HTML),
remaining route 404/400 paths, and subscription reason upgrades.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from mesh.comment_inbox import subscriptions
from mesh.comment_inbox.channels import make_inbox_channel_checker
from mesh.comment_inbox.inbox import _decode_group_cursor
from mesh.comment_inbox.markdown import sanitize_html
from mesh.comment_inbox.service import CommentService
from mesh.db.models.member import Member
from mesh.db.models.notification import IssueSubscription
from mesh.errors import BusinessRuleError, ValidationError
from mesh.workers.notification_digest import notification_digest_loop
from tests.unit.test_comment_service import (
    _human,
    _issue,
    _workspace,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# member inbox channel checker
# ---------------------------------------------------------------------------


class _Principal:
    def __init__(self, subject: str, workspace_ids):
        self.subject = subject
        self.workspace_ids = set(workspace_ids)


async def test_inbox_checker_allows_owner_denies_others(session_factory):
    workspace = await _workspace(session_factory)
    alice = await _human(session_factory, workspace, "Alice")
    checker = make_inbox_channel_checker(session_factory)

    async with session_factory() as session:
        member = await session.scalar(select(Member).where(Member.id == alice.id))
    owner_principal = _Principal(str(member.user_id), {workspace.id})

    assert await checker(owner_principal, f"member:{alice.id}:inbox") is True
    # another member of the workspace cannot subscribe to alice's inbox
    stranger_user = uuid.uuid4()
    assert await checker(_Principal(str(stranger_user), {workspace.id}),
                         f"member:{alice.id}:inbox") is False
    # wrong workspace membership
    assert await checker(_Principal(str(member.user_id), {uuid.uuid4()}),
                         f"member:{alice.id}:inbox") is False
    # malformed channel shapes
    assert await checker(owner_principal, f"member:{alice.id}:other") is False
    assert await checker(owner_principal, "member:not-a-uuid:inbox") is False
    assert await checker(owner_principal, "garbage") is False
    # dev principal (non-UUID subject) is allowed by definition
    assert await checker(_Principal("mesh-dev", {workspace.id}),
                         f"member:{alice.id}:inbox") is True


# ---------------------------------------------------------------------------
# digest sweep loop
# ---------------------------------------------------------------------------


class _QuietMailer:
    async def deliver(self, email, kind, token):
        return None


async def test_digest_loop_processes_and_stops(session_factory):
    stop = asyncio.Event()

    async def _stop_soon():
        await asyncio.sleep(0.2)
        stop.set()

    stopper = asyncio.create_task(_stop_soon())
    # no pending rows → sweep runs once, finds nothing, waits, stops cleanly
    await asyncio.wait_for(
        notification_digest_loop(
            session_factory, mailer=_QuietMailer(), interval=0.1, stop=stop
        ),
        timeout=10,
    )
    await stopper


# ---------------------------------------------------------------------------
# sanitizer branches (fed pre-rendered HTML directly)
# ---------------------------------------------------------------------------


def test_sanitizer_drops_nested_dangerous_subtrees():
    # iframe contents are parsed as markup (unlike script CDATA), so the
    # inner <script> exercises the nested skip-depth branch
    out, _ = sanitize_html("a<iframe>x<script>y</script>z</iframe>b")
    assert out == "ab"


def test_sanitizer_self_closing_dangerous_dropped():
    out, _ = sanitize_html("keep<script src=x />tail")
    assert "<script" not in out and "keep" in out and "tail" in out


def test_sanitizer_img_unsafe_src_dropped_but_alt_kept():
    out, _ = sanitize_html('<img src="javascript:alert(1)" alt="x" />')
    assert "<img" not in out


def test_sanitizer_safe_img_and_relative_link():
    out, _ = sanitize_html('<a href="/issues/1">rel</a><img src="https://i.example/a.png"/>')
    assert 'href="/issues/1"' in out
    assert 'src="https://i.example/a.png"' in out


def test_sanitizer_span_mention_malformed_id_dropped():
    out, mentions = sanitize_html('<span class="mesh-mention" data-member-id="zzz">x</span>')
    assert "<span" not in out
    assert mentions == ()


def test_sanitizer_code_class_non_language_stripped():
    out, _ = sanitize_html('<code class="evil">c</code>')
    assert out == "<code>c</code>"


def test_sanitizer_align_whitelist():
    out, _ = sanitize_html('<table><tr><th align="left">h</th><td align="bogus">d</td></tr></table>')
    assert 'th align="left"' in out
    assert 'td align="bogus"' not in out and "<td>" in out


def test_sanitizer_input_non_checkbox_dropped():
    out, _ = sanitize_html('<input type="text" value="x" />')
    assert "<input" not in out


def test_safe_url_control_chars_rejected():
    # a REAL control byte inside the URL (classic bypass vector) — rejected
    out, _ = sanitize_html('<a href="https://a.com/\x07evil">x</a>')
    assert "<a" not in out
    assert "x" in out


# ---------------------------------------------------------------------------
# inbox cursor validation + service edges
# ---------------------------------------------------------------------------


def test_group_cursor_malformed_is_invalid():
    with pytest.raises(ValidationError) as exc:
        _decode_group_cursor("!!!not-base64-json!!!")
    assert exc.value.code == "invalid_cursor"


async def test_list_replies_on_a_reply_is_rejected(session_factory):
    workspace = await _workspace(session_factory)
    author = await _human(session_factory, workspace, "Author")
    issue = await _issue(session_factory, workspace, author)
    service = CommentService(session_factory)
    root = await service.create_comment(
        workspace_id=workspace.id, issue_id=issue.id, author_member=author,
        body_markdown="root",
    )
    reply = await service.create_comment(
        workspace_id=workspace.id, issue_id=issue.id, author_member=author,
        body_markdown="reply", parent_id=uuid.UUID(root["id"]),
    )
    with pytest.raises(BusinessRuleError) as exc:
        await service.list_replies(
            workspace_id=workspace.id, comment_id=uuid.UUID(reply["id"]),
            viewer_member_id=author.id, member=author,
        )
    assert exc.value.code == "not_thread_root"


async def test_list_include_none_skips_preview(session_factory):
    workspace = await _workspace(session_factory)
    author = await _human(session_factory, workspace, "Author")
    issue = await _issue(session_factory, workspace, author)
    service = CommentService(session_factory)
    root = await service.create_comment(
        workspace_id=workspace.id, issue_id=issue.id, author_member=author,
        body_markdown="root",
    )
    await service.create_comment(
        workspace_id=workspace.id, issue_id=issue.id, author_member=author,
        body_markdown="reply", parent_id=uuid.UUID(root["id"]),
    )
    items, _ = await service.list_comments(
        workspace_id=workspace.id, issue_id=issue.id,
        viewer_member_id=author.id, member=author, include="none",
    )
    assert "preview_replies" not in items[0]
    assert items[0]["reply_count"] == 1


async def test_manager_can_edit_others_comment(session_factory):
    workspace = await _workspace(session_factory)
    author = await _human(session_factory, workspace, "Author")
    manager = await _human(session_factory, workspace, "Mgr")
    async with session_factory() as session, session.begin():
        member = await session.scalar(select(Member).where(Member.id == manager.id))
        member.role = "admin"
    issue = await _issue(session_factory, workspace, author)
    service = CommentService(session_factory)
    created = await service.create_comment(
        workspace_id=workspace.id, issue_id=issue.id, author_member=author,
        body_markdown="original",
    )
    edited = await service.update_comment(
        workspace_id=workspace.id, comment_id=uuid.UUID(created["id"]),
        editor_member=manager, is_manager=True, body_markdown="moderated",
    )
    assert edited["body_markdown"] == "moderated"


async def test_version_match_accepts_plain_isoformat(session_factory):
    workspace = await _workspace(session_factory)
    author = await _human(session_factory, workspace, "Author")
    issue = await _issue(session_factory, workspace, author)
    service = CommentService(session_factory)
    created = await service.create_comment(
        workspace_id=workspace.id, issue_id=issue.id, author_member=author,
        body_markdown="v1",
    )
    # the raw isoformat spelling (with +00:00) is also accepted
    async with session_factory() as session:
        from mesh.db.models.comment import Comment

        row = await session.scalar(
            select(Comment).where(Comment.id == uuid.UUID(created["id"]))
        )
        raw_iso = row.updated_at.isoformat()
    updated = await service.update_comment(
        workspace_id=workspace.id, comment_id=uuid.UUID(created["id"]),
        editor_member=author, is_manager=False, body_markdown="v2",
        expected_updated_at=raw_iso,
    )
    assert updated["body_markdown"] == "v2"


# ---------------------------------------------------------------------------
# subscription reason upgrade rules
# ---------------------------------------------------------------------------


async def test_subscription_reason_upgrade_and_mute_persistence(session_factory):
    workspace = await _workspace(session_factory)
    member = await _human(session_factory, workspace, "Sub")
    issue = await _issue(session_factory, workspace, member)
    async with session_factory() as session, session.begin():
        sub = await subscriptions.ensure_subscription(
            session, workspace_id=workspace.id, issue_id=issue.id,
            subscriber_id=member.id, reason="participated",
        )
        assert sub.reason == "participated"
    # weaker reason does not downgrade
    async with session_factory() as session, session.begin():
        sub = await subscriptions.ensure_subscription(
            session, workspace_id=workspace.id, issue_id=issue.id,
            subscriber_id=member.id, reason="manual",
        )
        assert sub.reason == "participated"
    # stronger reason upgrades
    async with session_factory() as session, session.begin():
        sub = await subscriptions.ensure_subscription(
            session, workspace_id=workspace.id, issue_id=issue.id,
            subscriber_id=member.id, reason="creator",
        )
        assert sub.reason == "creator"
    # mute is sticky across automatic re-subscription
    async with session_factory() as session, session.begin():
        await subscriptions.set_muted(
            session, workspace_id=workspace.id, issue_id=issue.id,
            subscriber_id=member.id, muted=True,
        )
    async with session_factory() as session, session.begin():
        await subscriptions.ensure_subscription(
            session, workspace_id=workspace.id, issue_id=issue.id,
            subscriber_id=member.id, reason="mentioned",
        )
    async with session_factory() as session:
        row = await session.scalar(
            select(IssueSubscription).where(
                IssueSubscription.issue_id == issue.id,
                IssueSubscription.subscriber_id == member.id,
            )
        )
        assert row.muted is True  # automatic flow never unmutes
