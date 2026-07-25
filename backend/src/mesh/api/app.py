"""API application factory (README §2.2: the API is a stateless deployable unit).

Middleware chain per §6.14 — parse Bearer → workspace membership → RBAC →
rate limiting — is wired incrementally as modules land; the skeleton parses
the Bearer token into a principal via a pluggable authenticator (deps.py).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, Query

from mesh import __version__
from mesh.api.deps import current_principal
from mesh.api.envelope import DataEnvelope
from mesh.api.error_handlers import install_error_handlers
from mesh.api.health import router as health_router
from mesh.api.realtime_routes import router as realtime_router
from mesh.auth.mailer import build_mailer
from mesh.auth.oauth import MockOAuthProvider, OAuthService
from mesh.auth.oauth_routes import router as oauth_router
from mesh.auth.ratelimit import RateLimiter
from mesh.auth.routes import router as auth_router
from mesh.auth.service import AuthService
from mesh.auth.token_routes import router as token_router
from mesh.auth.tokens import TokenService
from mesh.config import DEV_JWT_SECRET, ConfigError, Settings, load_settings
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
from mesh.member.routes import router as member_router
from mesh.member.service import MemberService
from mesh.realtime.auth import (
    DefaultChannelAuthorizer,
    build_authenticator,
)
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
    yield
    await app.state.redis.aclose()
    await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the Mesh API FastAPI app."""
    settings = settings or load_settings()
    # Fail-safe: the API signs/verifies tokens, so production must never run on
    # the well-known dev signing key (auth.md §5.5 — keys not in code/repo).
    if settings.auth_mode == "production" and settings.jwt_secret == DEV_JWT_SECRET:
        raise ConfigError(
            ("jwt_secret",),
            "MESH_JWT_SECRET must be set to a strong secret in production",
        )
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
    app.state.rate_limiter = RateLimiter(app.state.redis)
    # OAuth: vendor-neutral provider registry. Dev registers an in-process mock
    # provider so the full code+PKCE round-trip is testable without a vendor;
    # production providers are operator-configured (none hardcoded).
    oauth_service = OAuthService(session_factory, app.state.auth_service, app.state.redis)
    if settings.auth_mode == "dev":
        oauth_service.register_provider(MockOAuthProvider())
    app.state.oauth_service = oauth_service
    app.state.workspace_service = WorkspaceService(session_factory)
    app.state.invitation_service = InvitationService(session_factory)
    app.state.member_service = MemberService(session_factory)
    app.state.token_service = TokenService(session_factory)

    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(realtime_router)
    app.include_router(auth_router)
    app.include_router(oauth_router)
    app.include_router(workspace_router)
    app.include_router(member_router)
    app.include_router(token_router)

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

    return app
