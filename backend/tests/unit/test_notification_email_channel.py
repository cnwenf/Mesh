"""Email channel alignment (MES-189 B3 — comment-inbox.md §4.4 / i18n.md §5.1).

Covers per-recipient locale rendering of realtime + digest mails, HTML-escaped
previews, token-gated deep links back into the inbox, and the one-time open
endpoint (signed JWT credential, expiry, recipient binding, anti-oracle 404).
Runs against real PostgreSQL; nothing on the contract path is mocked.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from mesh.comment_inbox.notifications import (
    EMAIL_OPEN_TOKEN_TTL,
    NotificationFanoutHandler,
    issue_email_open_token,
    resolve_recipient_locale,
    send_digest_emails,
    verify_email_open_token,
)
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.notification import (
    Notification,
    NotificationPreference,
)
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.db.tenant import set_tenant_context

pytestmark = pytest.mark.unit

SECRET = "b3-email-open-token-test-secret-000000"


def _settings(app_base_url: str | None = "http://app.test"):
    """Duck-typed settings surface used by the email link/token helpers."""
    return SimpleNamespace(
        jwt_secret=SECRET, jwt_algorithm="HS256", app_base_url=app_base_url
    )


# ---------------------------------------------------------------------------
# one-time open token (pure)
# ---------------------------------------------------------------------------


def test_open_token_roundtrip_binds_notification_workspace_recipient():
    settings = _settings()
    notification_id, workspace_id, member_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token = issue_email_open_token(
        settings,
        notification_id=notification_id,
        workspace_id=workspace_id,
        recipient_member_id=member_id,
    )
    assert verify_email_open_token(settings, token, notification_id=notification_id) == (
        workspace_id,
        member_id,
    )


def test_open_token_rejects_other_notification_expired_and_garbage():
    settings = _settings()
    notification_id, workspace_id, member_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token = issue_email_open_token(
        settings,
        notification_id=notification_id,
        workspace_id=workspace_id,
        recipient_member_id=member_id,
    )
    # bound to one notification only
    assert verify_email_open_token(settings, token, notification_id=uuid.uuid4()) is None
    # garbage / missing token
    assert verify_email_open_token(settings, "not-a-jwt", notification_id=notification_id) is None
    assert verify_email_open_token(settings, None, notification_id=notification_id) is None
    # expired token
    expired = issue_email_open_token(
        settings,
        notification_id=notification_id,
        workspace_id=workspace_id,
        recipient_member_id=member_id,
        now=datetime.now(UTC) - EMAIL_OPEN_TOKEN_TTL - timedelta(hours=1),
    )
    assert verify_email_open_token(settings, expired, notification_id=notification_id) is None


def test_open_token_rejects_wrong_purpose():
    import jwt as pyjwt

    settings = _settings()
    notification_id = uuid.uuid4()
    forged = pyjwt.encode(
        {
            "purpose": "password_reset",  # a different valid-looking purpose
            "sub": str(notification_id),
            "ws": str(uuid.uuid4()),
            "member": str(uuid.uuid4()),
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        SECRET,
        algorithm="HS256",
    )
    assert verify_email_open_token(settings, forged, notification_id=notification_id) is None


# ---------------------------------------------------------------------------
# DB helpers (minimal, mirror test_inbox_service.py)
# ---------------------------------------------------------------------------


async def _workspace(factory, settings: dict | None = None) -> Workspace:
    async with factory() as session, session.begin():
        workspace = Workspace(
            name="W", slug=f"ws-{uuid.uuid4().hex[:12]}", settings=settings or {}
        )
        session.add(workspace)
    return workspace


async def _human(factory, workspace, name: str, locale: str | None = None) -> Member:
    async with factory() as session, session.begin():
        user = User(
            email=f"{name.lower()}-{uuid.uuid4().hex[:6]}@x.io",
            display_name=name,
            settings={"locale": locale} if locale else {},
        )
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace.id,
            user_id=user.id,
            role="member",
            member_type="human",
        )
        session.add(member)
    return member


async def _issue(factory, workspace, reporter: Member) -> Issue:
    namespace = uuid.uuid4().hex[:8]
    async with factory() as session, session.begin():
        status = IssueStatus(workspace_id=workspace.id, name=f"S-{namespace}", category="todo")
        session.add(status)
        await session.flush()
        issue = Issue(
            workspace_id=workspace.id,
            identifier_namespace_key=namespace,
            number=1,
            identifier=f"{namespace.upper()}-1",
            title="Login broken",
            status_id=status.id,
            state_category="todo",
            reporter_id=reporter.id,
        )
        session.add(issue)
    return issue


async def _seed_digest_notification(factory, workspace, actor: Member, recipient: Member, issue):
    """One digest-policy notification + its pending email ledger row."""
    from mesh.comment_inbox.notifications import emit_notification_fanout

    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        session.add(
            NotificationPreference(
                workspace_id=workspace.id,
                member_id=recipient.id,
                event_type="comment_created",
                in_app=True,
                email="digest",
            )
        )
        event = await emit_notification_fanout(
            session,
            workspace_id=workspace.id,
            notification_type="comment_created",
            actor_member_id=actor.id,
            actor_name="Carol",
            actor_member_type="human",
            issue_id=issue.id,
            recipient_ids=[recipient.id],
            group_key=f"issue:{issue.id}:comment_created",
            title="Login broken",
            preview="<script>alert(1)</script> 50% off_now",
        )
    handler = NotificationFanoutHandler(aggregation_window_seconds=60.0, mailer=None)
    async with factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        await handler.handle(session, event)
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(Notification).where(Notification.recipient_id == recipient.id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    return rows[0]


class _BodyRecordingMailer:
    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    async def deliver(self, email, kind, token):
        self.sent.append((email, kind, token))


# ---------------------------------------------------------------------------
# locale rendering + deep links (real DB)
# ---------------------------------------------------------------------------


async def test_digest_renders_recipient_locale_zh_with_escaped_preview_and_open_link(session_factory):
    workspace = await _workspace(session_factory)
    actor = await _human(session_factory, workspace, "Carol")
    bob = await _human(session_factory, workspace, "Bob", locale="zh-CN")
    issue = await _issue(session_factory, workspace, actor)
    await _seed_digest_notification(session_factory, workspace, actor, bob, issue)

    mailer = _BodyRecordingMailer()
    async with session_factory() as session, session.begin():
        sent = await send_digest_emails(session, mailer=mailer, settings=_settings())
    assert sent == 1
    _email, kind, body = mailer.sent[0]
    assert kind == "notification_digest"
    assert body.startswith("Mesh 通知摘要（共 1 条）")
    # preview is HTML-escaped (§4.4 injection guard), wildcards stay literal
    assert "<script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt; 50% off_now" in body
    # token-gated deep link back into the inbox
    assert "/api/v1/inbox/" in body and "/open?token=" in body


async def test_digest_defaults_to_en_and_links_resolve_locale_from_workspace(session_factory):
    # no user locale, workspace default zh-CN → zh digest; second workspace
    # with no default at all → en fallback.
    ws_default = await _workspace(session_factory, settings={"default_locale": "zh-CN"})
    ws_none = await _workspace(session_factory)
    actor1 = await _human(session_factory, ws_default, "Carol")
    bob1 = await _human(session_factory, ws_default, "Bob")
    issue1 = await _issue(session_factory, ws_default, actor1)
    await _seed_digest_notification(session_factory, ws_default, actor1, bob1, issue1)
    actor2 = await _human(session_factory, ws_none, "Dave")
    bob2 = await _human(session_factory, ws_none, "Erin")
    issue2 = await _issue(session_factory, ws_none, actor2)
    await _seed_digest_notification(session_factory, ws_none, actor2, bob2, issue2)

    mailer = _BodyRecordingMailer()
    async with session_factory() as session, session.begin():
        sent = await send_digest_emails(session, mailer=mailer, settings=_settings())
    assert sent == 2
    bodies = {body for _e, _k, body in mailer.sent}
    assert any(body.startswith("Mesh 通知摘要（共 1 条）") for body in bodies)
    assert any(body.startswith("Mesh notification digest (1 items)") for body in bodies)


async def test_digest_without_settings_keeps_bodies_linkless(session_factory):
    workspace = await _workspace(session_factory)
    actor = await _human(session_factory, workspace, "Carol")
    bob = await _human(session_factory, workspace, "Bob")
    issue = await _issue(session_factory, workspace, actor)
    await _seed_digest_notification(session_factory, workspace, actor, bob, issue)

    mailer = _BodyRecordingMailer()
    async with session_factory() as session, session.begin():
        sent = await send_digest_emails(session, mailer=mailer)  # settings=None
    assert sent == 1
    _email, _kind, body = mailer.sent[0]
    assert "/open?token=" not in body
    assert body.startswith("Mesh notification digest (1 items)")


async def test_realtime_email_carries_recipient_locale_and_open_link(session_factory):
    from mesh.comment_inbox.notifications import emit_notification_fanout

    workspace = await _workspace(session_factory)
    actor = await _human(session_factory, workspace, "Carol")
    bob = await _human(session_factory, workspace, "Bob", locale="zh-CN")
    issue = await _issue(session_factory, workspace, actor)
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        session.add(
            NotificationPreference(
                workspace_id=workspace.id,
                member_id=bob.id,
                event_type="mentioned",
                in_app=True,
                email="realtime",
            )
        )
        event = await emit_notification_fanout(
            session,
            workspace_id=workspace.id,
            notification_type="mentioned",
            actor_member_id=actor.id,
            actor_name="Carol",
            actor_member_type="human",
            issue_id=issue.id,
            recipient_ids=[bob.id],
            group_key=f"issue:{issue.id}:mentioned",
            title="Login broken",
            preview="please look",
        )
    mailer = _BodyRecordingMailer()
    handler = NotificationFanoutHandler(
        aggregation_window_seconds=60.0, mailer=mailer, settings=_settings()
    )
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        await handler.handle(session, event)
    assert len(mailer.sent) == 1
    _email, kind, body = mailer.sent[0]
    assert kind == "notification_realtime"
    assert body.startswith("Mesh 通知：mentioned")
    assert "打开该通知（打开即标记已读）" in body
    assert "/api/v1/inbox/" in body and "/open?token=" in body


async def test_resolve_recipient_locale_chain(session_factory):
    workspace = await _workspace(session_factory, settings={"default_locale": "zh-CN"})
    user_locale = await _human(session_factory, workspace, "U", locale="en")
    ws_default = await _human(session_factory, workspace, "W")
    async with session_factory() as session:
        assert (
            await resolve_recipient_locale(
                session, recipient_member_id=user_locale.id, workspace_id=workspace.id
            )
            == "en"
        )
        assert (
            await resolve_recipient_locale(
                session, recipient_member_id=ws_default.id, workspace_id=workspace.id
            )
            == "zh-CN"
        )


# ---------------------------------------------------------------------------
# open endpoint (real app + real DB)
# ---------------------------------------------------------------------------


@pytest.fixture
def email_app(db_url, redis_url):
    from mesh.api.app import create_app
    from mesh.config import load_settings

    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret=SECRET,
        app_base_url="http://app.test",
    )
    return create_app(settings)


@pytest.fixture
async def email_client(email_app):
    transport = httpx.ASGITransport(app=email_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
    await email_app.state.redis.aclose()
    await email_app.state.engine.dispose()


async def _seed_via_app(email_app, *, locale: str | None):
    factory = email_app.state.session_factory
    workspace = await _workspace(factory)
    actor = await _human(factory, workspace, "Carol")
    bob = await _human(factory, workspace, "Bob", locale=locale)
    issue = await _issue(factory, workspace, actor)
    notification = await _seed_digest_notification(factory, workspace, actor, bob, issue)
    return workspace, bob, notification


async def test_open_endpoint_marks_read_and_redirects_to_inbox_anchor(email_client, email_app):
    workspace, bob, notification = await _seed_via_app(email_app, locale="zh-CN")
    token = issue_email_open_token(
        _settings(),
        notification_id=notification.id,
        workspace_id=workspace.id,
        recipient_member_id=bob.id,
    )
    response = await email_client.get(
        f"/api/v1/inbox/{notification.id}/open",
        params={"token": token},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == (
        f"http://app.test/w/{workspace.slug}/inbox/{notification.id}"
    )
    async with email_app.state.session_factory() as session:
        await set_tenant_context(session, workspace.id)
        row = await session.scalar(
            select(Notification).where(Notification.id == notification.id)
        )
    assert row.read_at is not None


async def test_open_endpoint_rejects_invalid_tokens_with_uniform_404(email_client, email_app):
    workspace, bob, notification = await _seed_via_app(email_app, locale=None)
    good_other_notification = issue_email_open_token(
        _settings(),
        notification_id=uuid.uuid4(),  # token for a different notification
        workspace_id=workspace.id,
        recipient_member_id=bob.id,
    )
    foreign_member = issue_email_open_token(
        _settings(),
        notification_id=notification.id,
        workspace_id=workspace.id,
        recipient_member_id=uuid.uuid4(),  # recipient never existed
    )
    for token in (None, "garbage", good_other_notification, foreign_member):
        response = await email_client.get(
            f"/api/v1/inbox/{notification.id}/open",
            params={"token": token} if token else {},
            follow_redirects=False,
        )
        assert response.status_code == 404, token
    # and the notification stayed unread
    async with email_app.state.session_factory() as session:
        await set_tenant_context(session, workspace.id)
        row = await session.scalar(
            select(Notification).where(Notification.id == notification.id)
        )
    assert row.read_at is None


async def test_open_endpoint_idempotent_second_open_still_redirects(email_client, email_app):
    workspace, bob, notification = await _seed_via_app(email_app, locale=None)
    token = issue_email_open_token(
        _settings(),
        notification_id=notification.id,
        workspace_id=workspace.id,
        recipient_member_id=bob.id,
    )
    first = await email_client.get(
        f"/api/v1/inbox/{notification.id}/open",
        params={"token": token},
        follow_redirects=False,
    )
    second = await email_client.get(
        f"/api/v1/inbox/{notification.id}/open",
        params={"token": token},
        follow_redirects=False,
    )
    assert first.status_code == 302
    assert second.status_code == 302  # read-mark is idempotent; link still resolves


async def test_open_endpoint_returns_frame_when_no_base_url(db_url, redis_url):
    # A deployment without MESH_APP_BASE_URL degrades to a JSON frame
    # instead of a redirect (the read-mark still lands). Settings is frozen,
    # so this builds its own app without the base URL.
    from mesh.api.app import create_app
    from mesh.config import load_settings

    bare = create_app(
        load_settings(
            database_url=db_url,
            redis_url=redis_url,
            auth_mode="dev",
            jwt_secret=SECRET,
        )
    )
    transport = httpx.ASGITransport(app=bare, raise_app_exceptions=False)
    try:
        workspace, bob, notification = await _seed_via_app(bare, locale=None)
        token = issue_email_open_token(
            _settings(),  # issuance side still has a base URL — the mail did
            notification_id=notification.id,
            workspace_id=workspace.id,
            recipient_member_id=bob.id,
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get(
                f"/api/v1/inbox/{notification.id}/open",
                params={"token": token},
                follow_redirects=False,
            )
        assert response.status_code == 200
        assert response.json()["data"]["id"] == str(notification.id)
    finally:
        await bare.state.redis.aclose()
        await bare.state.engine.dispose()
