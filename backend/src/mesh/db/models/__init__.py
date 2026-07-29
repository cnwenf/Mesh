"""SQLAlchemy models for the Mesh schema."""

from mesh.db.models.agent import Agent, AgentConfigVersion
from mesh.db.models.analytics import AnalyticsSnapshot
from mesh.db.models.api_token import ApiToken
from mesh.db.models.attachment import (
    Attachment,
    AttachmentBlob,
    AttachmentLink,
    AttachmentQuota,
    UploadSession,
)
from mesh.db.models.audit import AuditLog
from mesh.db.models.autopilot import (
    Autopilot,
    AutopilotArtifact,
    AutopilotRun,
    AutopilotRunAttempt,
    WebhookEvent,
    WebhookSecret,
)
from mesh.db.models.chat import ChatMessage, ChatSession, Favorite
from mesh.db.models.comment import Comment, CommentMention, CommentReaction
from mesh.db.models.data_job import DataJob, DataJobRow
from mesh.db.models.integration import (
    ExternalIdentity,
    Integration,
    IntegrationBinding,
    IntegrationEvent,
    VcsLink,
    WebhookSubscription,
    WebhookSubscriptionDelivery,
)
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
from mesh.db.models.notification import (
    IssueSubscription,
    Notification,
    NotificationDelivery,
    NotificationPreference,
)
from mesh.db.models.onboarding import OnboardingState, OnboardingStateStep
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
from mesh.db.models.runtime import (
    Approval,
    AttemptTaskToken,
    ExecutionAttempt,
    ExecutionCredential,
    RepoCheckout,
    Runtime,
    RuntimeCredential,
    RuntimeHeartbeat,
    TaskExecution,
    TaskLogSegment,
)
from mesh.db.models.skill import (
    AgentSkill,
    Skill,
    SkillImportTask,
    SkillInstallation,
    SkillReference,
    SkillScript,
    SkillSource,
    SkillTrigger,
    SkillVersion,
)
from mesh.db.models.squad import (
    IssueSquadAssignment,
    Squad,
    SquadActivity,
    SquadMember,
    SquadMessage,
    SquadTask,
    SquadTaskDependency,
)
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
    "Agent",
    "AgentConfigVersion",
    "AgentSkill",
    "AnalyticsSnapshot",
    "ApiToken",
    "Approval",
    "AttemptTaskToken",
    "Attachment",
    "AttachmentBlob",
    "AttachmentLink",
    "AttachmentQuota",
    "AuditLog",
    "Autopilot",
    "AutopilotArtifact",
    "AutopilotRun",
    "AutopilotRunAttempt",
    "ChatMessage",
    "ChatSession",
    "Comment",
    "CommentMention",
    "CommentReaction",
    "DataJob",
    "DataJobRow",
    "CustomFieldDef",
    "CustomFieldOption",
    "Cycle",
    "EmailVerificationToken",
    "ExecutionAttempt",
    "ExecutionCredential",
    "ExternalIdentity",
    "Favorite",
    "IdentifierPrefixRegistry",
    "Integration",
    "IntegrationBinding",
    "IntegrationEvent",
    "Issue",
    "IssueActivity",
    "IssueCustomFieldValue",
    "IssueDependency",
    "IssueLabel",
    "IssueSquadAssignment",
    "IssueStatus",
    "IssueSubscription",
    "IssueTemplate",
    "Label",
    "LoginAttempt",
    "Member",
    "MemberProjectAccess",
    "Milestone",
    "Notification",
    "NotificationDelivery",
    "NotificationPreference",
    "OAuthIdentity",
    "OnboardingState",
    "OnboardingStateStep",
    "OutboxEvent",
    "PasswordResetToken",
    "Project",
    "ProjectMember",
    "ProjectTemplate",
    "ProjectUpdate",
    "RealtimeChannel",
    "RealtimeEvent",
    "RepoCheckout",
    "Runtime",
    "RuntimeCredential",
    "RuntimeHeartbeat",
    "Session",
    "Skill",
    "SkillImportTask",
    "SkillInstallation",
    "SkillReference",
    "SkillScript",
    "SkillSource",
    "SkillTrigger",
    "SkillVersion",
    "Squad",
    "SquadActivity",
    "SquadMember",
    "SquadMessage",
    "SquadTask",
    "SquadTaskDependency",
    "TaskExecution",
    "TaskLogSegment",
    "UploadSession",
    "User",
    "VcsLink",
    "View",
    "ViewIssuePosition",
    "WebhookEvent",
    "WebhookSecret",
    "WebhookSubscription",
    "WebhookSubscriptionDelivery",
    "Workspace",
    "WorkspaceInvitation",
    "WorkspaceInvitationRedemption",
    "WorkspaceSlugHistory",
]
