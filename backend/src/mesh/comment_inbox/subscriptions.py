"""Issue subscription helpers (comment-inbox.md §2.5 / README §6.13).

Default subscriptions: creators (``reason='creator'``), assignees
(``assignee``), participants (``participated`` — posted a comment) and
mentioned members (``mentioned``) subscribe automatically; members may
subscribe/unsubscribe manually (``manual``) and mute/unmute per issue
(``muted`` keeps the row but suppresses notification generation).

Existing reasons are never downgraded by an automatic re-subscription
(manual stays manual), and automatic flows never UN-mute a muted issue —
only the explicit unmute endpoint does.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.notification import IssueSubscription

# Upgrade order: an automatic reason only replaces a weaker one.
_REASON_STRENGTH = {
    "creator": 4,
    "assignee": 3,
    "mentioned": 2,
    "participated": 1,
    "manual": 0,
}


async def ensure_subscription(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID,
    subscriber_id: uuid.UUID,
    reason: str,
) -> IssueSubscription:
    """Create the subscription row or upgrade its reason; never unmutes."""
    existing = await session.scalar(
        select(IssueSubscription).where(
            IssueSubscription.workspace_id == workspace_id,
            IssueSubscription.issue_id == issue_id,
            IssueSubscription.subscriber_id == subscriber_id,
        )
    )
    now = datetime.now(UTC)
    if existing is not None:
        if _REASON_STRENGTH.get(reason, 0) > _REASON_STRENGTH.get(existing.reason, 0):
            existing.reason = reason
            existing.updated_at = now
        await session.flush()
        return existing
    subscription = IssueSubscription(
        workspace_id=workspace_id,
        issue_id=issue_id,
        subscriber_id=subscriber_id,
        reason=reason,
    )
    session.add(subscription)
    await session.flush()
    return subscription


async def set_muted(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID,
    subscriber_id: uuid.UUID,
    muted: bool,
) -> IssueSubscription:
    """Mute / unmute an issue for a member (creates a manual row if absent)."""
    subscription = await ensure_subscription(
        session,
        workspace_id=workspace_id,
        issue_id=issue_id,
        subscriber_id=subscriber_id,
        reason="manual",
    )
    if subscription.muted != muted:
        subscription.muted = muted
        subscription.updated_at = datetime.now(UTC)
        await session.flush()
    return subscription


__all__ = ["ensure_subscription", "set_muted"]
