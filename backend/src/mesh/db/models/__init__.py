"""SQLAlchemy models for the Mesh schema."""

from mesh.db.models.api_token import ApiToken
from mesh.db.models.audit import AuditLog
from mesh.db.models.issue import (
    Issue,
    IssueActivity,
    IssueDependency,
    IssueStatus,
    IssueTemplate,
)
from mesh.db.models.label import (
    CustomFieldDef,
    CustomFieldOption,
    IssueCustomFieldValue,
    IssueLabel,
    Label,
)
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import (
    Cycle,
    Milestone,
    Project,
    ProjectMember,
    ProjectTemplate,
    ProjectUpdate,
)
from mesh.db.models.realtime import RealtimeChannel, RealtimeEvent
from mesh.db.models.user import (
    EmailVerificationToken,
    LoginAttempt,
    OAuthIdentity,
    PasswordResetToken,
    Session,
    User,
)
from mesh.db.models.view import View
from mesh.db.models.view_position import ViewIssuePosition
from mesh.db.models.workspace import (
    IdentifierPrefixRegistry,
    Workspace,
    WorkspaceInvitation,
    WorkspaceInvitationRedemption,
    WorkspaceSlugHistory,
)

__all__ = [
    "ApiToken",
    "AuditLog",
    "CustomFieldDef",
    "CustomFieldOption",
    "Cycle",
    "EmailVerificationToken",
    "IdentifierPrefixRegistry",
    "Issue",
    "IssueActivity",
    "IssueCustomFieldValue",
    "IssueDependency",
    "IssueLabel",
    "IssueStatus",
    "IssueTemplate",
    "Label",
    "LoginAttempt",
    "Member",
    "MemberProjectAccess",
    "Milestone",
    "OAuthIdentity",
    "OutboxEvent",
    "PasswordResetToken",
    "Project",
    "ProjectMember",
    "ProjectTemplate",
    "ProjectUpdate",
    "RealtimeChannel",
    "RealtimeEvent",
    "Session",
    "User",
    "View",
    "ViewIssuePosition",
    "Workspace",
    "WorkspaceInvitation",
    "WorkspaceInvitationRedemption",
    "WorkspaceSlugHistory",
]
