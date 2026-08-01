"""API application factory (README §2.2: the API is a stateless deployable unit).

Middleware chain per §6.14 — parse Bearer → workspace membership → RBAC →
rate limiting — is wired incrementally as modules land; the skeleton parses
the Bearer token into a principal via a pluggable authenticator (deps.py).
"""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, Query

from mesh import __version__
from mesh.agent.channels import register_agent_checkers
from mesh.agent.routes import router as agent_router
from mesh.agent.service import AgentService
from mesh.agent.tools import AgentToolService
from mesh.agent.triggers import (
    register_skill_context_resolver,
    register_skill_matching_resolver,
)
from mesh.analytics.routes import router as analytics_router
from mesh.analytics.service import AnalyticsService
from mesh.api.deps import current_principal
from mesh.api.envelope import DataEnvelope
from mesh.api.error_handlers import install_error_handlers
from mesh.api.health import router as health_router
from mesh.api.realtime_routes import router as realtime_router
from mesh.attachment.routes import router as attachment_router
from mesh.attachment.service import AttachmentService
from mesh.attachment.storage import ObjectStorage, StorageConfig
from mesh.auth.device_codes import DeviceCodeService
from mesh.auth.device_routes import router as device_auth_router
from mesh.auth.mailer import build_mailer
from mesh.auth.oauth import MockOAuthProvider, OAuthService
from mesh.auth.oauth_routes import router as oauth_router
from mesh.auth.ratelimit import RateLimiter
from mesh.auth.rbac import role_satisfies
from mesh.auth.routes import router as auth_router
from mesh.auth.service import AuthService
from mesh.auth.token_routes import router as token_router
from mesh.auth.tokens import TokenService
from mesh.autopilot.channels import register_autopilot_checkers
from mesh.autopilot.routes import router as autopilot_router
from mesh.autopilot.service import AutopilotService
from mesh.chat.channels import register_chat_checkers
from mesh.chat.engine import ChatGenerationEngine, ScriptedGenerationProvider
from mesh.chat.routes import router as chat_router
from mesh.chat.service import ChatService
from mesh.comment_inbox.channels import register_inbox_checkers
from mesh.comment_inbox.inbox import InboxService
from mesh.comment_inbox.routes import router as comment_inbox_router
from mesh.comment_inbox.service import CommentService
from mesh.config import Settings, load_settings, validate_auth_settings, validate_infra_settings
from mesh.data_jobs.channels import register_data_job_checkers
from mesh.data_jobs.routes import router as data_jobs_router
from mesh.data_jobs.service import DataJobService
from mesh.db.engine import create_app_engine_from_settings, create_session_factory
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    GoneError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
    ValidationError,
)
from mesh.favorites.routes import router as favorites_router
from mesh.favorites.service import FavoritesService
from mesh.integrations.channels import register_integration_checkers
from mesh.integrations.identities import RedisDevCodeDelivery
from mesh.integrations.inbound_routes import router as integrations_inbound_router
from mesh.integrations.routes import router as integrations_router
from mesh.integrations.service import IntegrationService
from mesh.issue.bulk import BulkService
from mesh.issue.channels import register_issue_checkers
from mesh.issue.dependencies import DependencyService
from mesh.issue.move import MoveService
from mesh.issue.routes import router as issue_router
from mesh.issue.service import IssueService
from mesh.issue.statuses import StatusService
from mesh.issue.templates import TemplateService
from mesh.labels.association import FieldValueService, IssueLabelService
from mesh.labels.association_routes import router as label_association_router
from mesh.labels.routes import router as label_router
from mesh.labels.service import LabelService
from mesh.member.routes import router as member_router
from mesh.member.service import MemberService
from mesh.onboarding.routes import router as onboarding_router
from mesh.onboarding.service import OnboardingService
from mesh.project.channels import register_resource_checkers
from mesh.project.routes import router as project_router
from mesh.project.service import ProjectService
from mesh.realtime.auth import (
    DefaultChannelAuthorizer,
    build_authenticator,
)
from mesh.runtime.channels import register_execution_checkers
from mesh.runtime.daemon_routes import router as runtime_daemon_router
from mesh.runtime.routes import router as runtime_router
from mesh.runtime.service import RuntimeService
from mesh.runtime.task_routes import router as task_router
from mesh.search.routes import router as search_router
from mesh.search.service import SearchService
from mesh.skill.bindings import BindingService
from mesh.skill.content_store import ObjectStorageContentStore
from mesh.skill.importer import ImportService, ImportSettings
from mesh.skill.installations import InstallationService
from mesh.skill.resolvers import make_matching_resolver
from mesh.skill.routes import router as skill_router
from mesh.skill.service import SkillService
from mesh.squad.channels import register_squad_checkers
from mesh.squad.routes import router as squad_router
from mesh.squad.service import SquadService
from mesh.squad.tasks import SquadTaskService, make_issue_assignee_watcher
from mesh.views.moves import BoardMoveService
from mesh.views.projection import ProjectionService
from mesh.views.routes import router as view_router
from mesh.views.service import ViewService
from mesh.web.entry import build_html_entry_router
from mesh.workspace.invitations import InvitationService
from mesh.workspace.routes import router as workspace_router
from mesh.workspace.service import WorkspaceService

_DEV_ERROR_RAISERS = {
    400: lambda: ValidationError("debug bad request"),
    401: lambda: UnauthorizedError("debug unauthorized"),
    403: lambda: ForbiddenError("debug forbidden"),
    404: lambda: NotFoundError("debug not found"),
    409: lambda: ConflictError("debug conflict"),
    410: lambda: GoneError("debug gone"),
    422: lambda: BusinessRuleError("debug business rule"),
    429: lambda: RateLimitedError("debug rate limited", retry_after=30),
}


def _raise_debug_error(status: int) -> None:
    if status == 500:
        raise RuntimeError("debug internal failure: secret=must-not-leak")
    raiser = _DEV_ERROR_RAISERS.get(status)
    if raiser is None:
        raise NotFoundError(f"no debug error for status {status}")
    raise raiser()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Attachment bucket bootstrap (private bucket; idempotent). Failures are
    # logged, not fatal — the API still boots for non-attachment traffic and
    # storage calls surface 502 storage_error until MinIO is reachable.
    with suppress(Exception):
        await app.state.storage.ensure_bucket()
    yield
    await app.state.redis.aclose()
    await app.state.engine.dispose()


def build_object_storage(settings: Settings) -> ObjectStorage:
    """S3-compatible client pair (internal I/O + public presign endpoint)."""
    return ObjectStorage(
        StorageConfig(
            endpoint=settings.storage_endpoint,
            public_endpoint=settings.storage_public_endpoint or settings.storage_endpoint,
            region=settings.storage_region,
            access_key=settings.storage_access_key,
            secret_key=settings.storage_secret_key,
            bucket=settings.storage_bucket,
        )
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the Mesh API FastAPI app."""
    settings = settings or load_settings()
    # Fail-safe: the API signs/verifies tokens, so production must never run on
    # the well-known dev signing key (auth.md §5.5 — keys not in code/repo).
    # Shared with the realtime gateway factory so the two cannot drift apart.
    validate_auth_settings(settings)
    # Fail-safe (MES-83): production must connect to PostgreSQL / Redis / object
    # storage with strong, unique credentials — refuse an under-configured deploy
    # at startup instead of coming up on a guessable password.
    validate_infra_settings(settings)
    app = FastAPI(title="Mesh API", version=__version__, lifespan=lifespan)

    engine = create_app_engine_from_settings(settings)
    session_factory = create_session_factory(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    app.state.authenticator = build_authenticator(
        auth_mode=settings.auth_mode,
        jwt_secret=settings.jwt_secret,
        jwt_algorithm=settings.jwt_algorithm,
        session_factory=session_factory,
    )
    app.state.authorizer = DefaultChannelAuthorizer(session_factory)

    # Auth: service + rate limiter + email delivery. Dev mode stashes one-time
    # tokens in a Redis dev-mailbox (real e2e fetches them); production sends via
    # SMTP when MESH_SMTP_HOST is set, else a logged no-op (see auth/mailer.py).
    mailer = build_mailer(settings, app.state.redis)
    app.state.mailer = mailer
    app.state.auth_service = AuthService(session_factory, settings, deliver=mailer.deliver)
    # Device-code authorization (auth.md §2.4.2/§3.1.1, cli.md §3.2): shares
    # the auth service so the token exchange mints cli sessions atomically.
    app.state.device_code_service = DeviceCodeService(
        session_factory, settings, app.state.auth_service
    )
    app.state.rate_limiter = RateLimiter(app.state.redis)
    # OAuth: vendor-neutral provider registry. Dev registers an in-process mock
    # provider so the full code+PKCE round-trip is testable without a vendor;
    # production providers are operator-configured (none hardcoded).
    oauth_service = OAuthService(session_factory, app.state.auth_service, app.state.redis)
    if settings.auth_mode == "dev":
        allowed = frozenset(
            uri.strip() for uri in (settings.oauth_mock_redirect_uris or "").split(",") if uri.strip()
        )
        oauth_service.register_provider(MockOAuthProvider(allowed_redirect_uris=allowed))
    app.state.oauth_service = oauth_service
    app.state.workspace_service = WorkspaceService(session_factory)
    app.state.invitation_service = InvitationService(session_factory)
    app.state.member_service = MemberService(session_factory)
    app.state.token_service = TokenService(session_factory)
    app.state.project_service = ProjectService(session_factory)
    # Issue module (issue.md): core entity services. Stateless orchestrators
    # sharing the session factory; resource-level authorization lives in the
    # service layer, route plumbing stays thin.
    app.state.issue_service = IssueService(
        session_factory, squad_assignee_watcher=make_issue_assignee_watcher()
    )
    app.state.status_service = StatusService(
        session_factory,
        is_workspace_manager=lambda member: role_satisfies(member.role, "project:manage"),
    )
    app.state.dependency_service = DependencyService(app.state.issue_service)
    app.state.move_service = MoveService(app.state.issue_service)
    app.state.bulk_service = BulkService(app.state.issue_service, app.state.move_service)
    app.state.template_service = TemplateService(app.state.issue_service)
    app.state.label_service = LabelService(session_factory)
    # label-property issue-association layer (MES-32 remainder): composed on
    # IssueService for the factory + issue-level authorization gates.
    app.state.issue_label_service = IssueLabelService(app.state.issue_service)
    app.state.field_value_service = FieldValueService(app.state.issue_service)
    app.state.view_service = ViewService(session_factory)
    app.state.projection_service = ProjectionService(
        session_factory, app.state.issue_service, app.state.view_service
    )
    app.state.board_move_service = BoardMoveService(
        session_factory,
        app.state.issue_service,
        app.state.move_service,
        app.state.view_service,
    )
    # Attachment module (attachment.md): private bucket + presigned direct
    # upload; the processing worker shares the same storage settings.
    app.state.storage = build_object_storage(settings)
    app.state.attachment_service = AttachmentService(session_factory, settings, app.state.storage)
    # Data jobs module (import-export.md): CSV/JSON import (dry-run →
    # partial-success run) and async export; execution via outbox → worker.
    app.state.data_job_service = DataJobService(session_factory, settings, app.state.storage)
    # Comment & inbox module (comment-inbox.md): comments, §6.9 mention
    # triggers, §6.13 notification fan-out (relay side), inbox operations.
    comment_service = CommentService(
        session_factory,
        max_agent_chain_depth=settings.max_agent_chain_depth,
        # §6.16 / runtime.md R12: comment write path secret scanning key.
        signing_secret=settings.jwt_secret,
    )
    app.state.comment_service = comment_service
    app.state.inbox_service = InboxService(session_factory)
    # Chat module (chat-session.md): sessions / candidate messages / §6.8
    # streaming. The generation engine runs in-process (asyncio tasks) and
    # buffers deltas in Redis; chat-triggered executions are enqueued via the
    # transactional outbox with trigger='chat' (README §6.9 / §6.5).
    app.state.chat_engine = ChatGenerationEngine(
        app.state.redis,
        session_factory,
        provider=ScriptedGenerationProvider(
            chunk_delay_seconds=settings.chat_generation_chunk_delay_seconds
        ),
        buffer_ttl_seconds=settings.chat_generation_buffer_ttl_seconds,
    )
    app.state.favorites_service = FavoritesService(session_factory)
    app.state.onboarding_service = OnboardingService(session_factory)
    app.state.chat_service = ChatService(
        session_factory,
        comment_service=app.state.comment_service,
        attachment_service=app.state.attachment_service,
        favorites_service=app.state.favorites_service,
        streaming_stale_seconds=settings.chat_streaming_stale_seconds,
    )
    app.state.chat_service.engine = app.state.chat_engine
    app.state.agent_service = AgentService(session_factory)
    app.state.agent_tool_service = AgentToolService(session_factory)
    # skill.md: four-layer skill module. Script/reference bodies live in the
    # shared object bucket via content_ref; the import service is SSRF-guarded
    # (host allowlist from settings) and the §6.11 / §4.5 resolvers feed the
    # enqueue handler so the snapshot freezes bound skill versions.
    app.state.skill_content_store = ObjectStorageContentStore(app.state.storage)
    app.state.skill_service = SkillService(session_factory)
    app.state.skill_installation_service = InstallationService(session_factory)
    app.state.skill_binding_service = BindingService(session_factory)
    app.state.skill_import_service = ImportService(
        session_factory,
        content_store=app.state.skill_content_store,
        settings=ImportSettings(
            host_allowlist=frozenset(
                h.strip().lower()
                for h in (settings.skill_source_host_allowlist or "").split(",")
                if h.strip()
            ),
            marketplace_url=settings.skill_marketplace_url,
        ),
        installation_service=app.state.skill_installation_service,
    )
    register_skill_context_resolver(
        app.state.skill_binding_service.collect_enqueue_context
    )
    register_skill_matching_resolver(make_matching_resolver())
    app.state.runtime_service = RuntimeService(session_factory, settings)
    # Squad module (squad.md): orchestration unit — leader decompose / dispatch /
    # aggregate. SquadTaskService owns assignment (§2.5), the decomposition DAG,
    # plan approval (§6.10) and execution observation (§4.4).
    app.state.squad_service = SquadService(session_factory)
    # comment_service injection: §S8/§4.3-7 parent-issue summary writeback on
    # the synchronous (human-leader) aggregation path (shared idempotency key
    # with the relay writeback — no duplicates across the two paths).
    app.state.squad_task_service = SquadTaskService(
        session_factory, comment_service=comment_service
    )
    # Autopilot module (autopilot.md): rules / runs / inbound webhooks. The
    # signing secret derives from jwt_secret (same ciphertext-only contract
    # as runtime_credentials, README §6.16).
    app.state.autopilot_service = AutopilotService(session_factory, settings.jwt_secret)
    # Analytics module (analytics.md): read-only aggregates + materialized
    # cache; never writes source tables.
    app.state.analytics_service = AnalyticsService(session_factory, settings)
    # Search module (search-command-palette.md §3): six-type object search
    # with in-query visibility + signed keyset cursor; read-only.
    app.state.search_service = SearchService(session_factory, settings)
    app.state.integration_service = IntegrationService(session_factory, settings.jwt_secret)
    # Verification codes for external-identity linking are delivered to the
    # claimed external account's DM (dev: Redis dev-outbox, tests read it).
    app.state.identity_code_delivery = RedisDevCodeDelivery(app.state.redis)
    # Resource-level subscription authorization (README §6.7): shared with the
    # realtime gateway so the standalone /ws process enforces the same
    # private-project visibility (CWE-862). Visibility re-checked per subscribe.
    register_resource_checkers(app.state.authorizer, session_factory)
    register_issue_checkers(app.state.authorizer, session_factory)
    register_inbox_checkers(app.state.authorizer, session_factory)
    register_agent_checkers(app.state.authorizer, session_factory)
    register_execution_checkers(app.state.authorizer, session_factory)
    register_squad_checkers(app.state.authorizer, session_factory)
    register_autopilot_checkers(app.state.authorizer, session_factory)
    register_integration_checkers(app.state.authorizer, session_factory)
    register_chat_checkers(app.state.authorizer, session_factory)
    register_data_job_checkers(app.state.authorizer, session_factory)

    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(realtime_router)
    app.include_router(auth_router)
    app.include_router(device_auth_router)
    app.include_router(oauth_router)
    app.include_router(workspace_router)
    app.include_router(member_router)
    app.include_router(token_router)
    app.include_router(project_router)
    app.include_router(issue_router)
    app.include_router(label_router)
    app.include_router(label_association_router)
    app.include_router(view_router)
    app.include_router(attachment_router)
    app.include_router(data_jobs_router)
    app.include_router(comment_inbox_router)
    app.include_router(chat_router)
    app.include_router(favorites_router)
    app.include_router(onboarding_router)
    app.include_router(agent_router)
    app.include_router(runtime_router)
    app.include_router(runtime_daemon_router)
    # §2.2 S-05: task principal routes (mesh_task_ tokens).
    app.include_router(task_router)
    app.include_router(skill_router)
    app.include_router(squad_router)
    app.include_router(autopilot_router)
    app.include_router(analytics_router)
    app.include_router(search_router)
    app.include_router(integrations_router)
    app.include_router(integrations_inbound_router)

    @app.get("/api/v1/ping", response_model=DataEnvelope[dict], tags=["meta"])
    async def ping() -> DataEnvelope[dict]:
        """Envelope smoke endpoint."""
        return DataEnvelope(data={"pong": True})

    if settings.auth_mode == "dev":

        @app.get("/_debug/error", include_in_schema=False)
        async def debug_error(status: int = Query(...)) -> dict:
            """Dev-only: exercise every §6.14 error code through the handlers."""
            _raise_debug_error(status)
            return {}  # unreachable

        @app.get("/_debug/principal", include_in_schema=False)
        async def debug_principal(principal=Depends(current_principal)) -> DataEnvelope[dict]:
            return DataEnvelope(
                data={"subject": principal.subject, "workspaces": sorted(map(str, principal.workspace_ids))}
            )

    # Personalized HTML entry (theme.md §2.3 ①): mounted LAST so every API
    # route keeps priority over the shell catch-all. Serves the built SPA with
    # a per-request __MESH_APPEARANCE__ injection resolved from the HttpOnly
    # mesh_session cookie / route workspace identity; degrades to 404 when the
    # frontend build is absent.
    app.include_router(build_html_entry_router())

    return app
