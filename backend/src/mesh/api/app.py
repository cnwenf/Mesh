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
from mesh.config import Settings, load_settings
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
from mesh.realtime.auth import (
    DefaultChannelAuthorizer,
    DevTokenAuthenticator,
    NullAuthenticator,
)

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
    app = FastAPI(title="Mesh API", version=__version__, lifespan=lifespan)

    engine = create_app_engine_from_settings(settings)
    session_factory = create_session_factory(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    app.state.authenticator = (
        DevTokenAuthenticator() if settings.auth_mode == "dev" else NullAuthenticator()
    )
    app.state.authorizer = DefaultChannelAuthorizer(session_factory)

    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(realtime_router)

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
