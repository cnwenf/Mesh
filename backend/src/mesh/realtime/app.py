"""Realtime gateway application factory — an independently deployable unit
(README §2.2: the gateway is a separate process from the API and the worker).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI

from mesh import __version__
from mesh.agent.channels import register_agent_checkers
from mesh.api.error_handlers import install_error_handlers
from mesh.api.health import router as health_router
from mesh.chat.channels import register_chat_checkers
from mesh.comment_inbox.channels import register_inbox_checkers
from mesh.config import Settings, load_settings, validate_auth_settings, validate_infra_settings
from mesh.data_jobs.channels import register_data_job_checkers
from mesh.db.engine import create_app_engine_from_settings, create_session_factory
from mesh.issue.channels import register_issue_checkers
from mesh.project.channels import register_resource_checkers
from mesh.realtime.auth import (
    ChannelAuthorizer,
    DefaultChannelAuthorizer,
    build_authenticator,
)
from mesh.realtime.gateway import realtime_ws_endpoint
from mesh.runtime.channels import register_execution_checkers
from mesh.squad.channels import register_squad_checkers


def build_authorizer(session_factory) -> ChannelAuthorizer:
    return DefaultChannelAuthorizer(session_factory)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await app.state.redis.aclose()
    await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the realtime gateway FastAPI app."""
    settings = settings or load_settings()
    # Fail-safe: the gateway verifies session JWTs on the first frame (§6.16),
    # so production must never run on the well-known dev signing key — it is
    # public in the repository and would let a forged token pass first-frame
    # auth. Shared with the API factory (the gateway deploys independently,
    # README §2.2, so its configuration is validated on its own startup path).
    validate_auth_settings(settings)
    # Fail-safe (MES-83): the gateway holds a Redis fan-out connection and an app
    # DB pool, so production credentials are validated on its own startup path —
    # it deploys independently (README §2.2) and may be misconfigured on its own.
    # require_storage=False: the gateway never touches object storage, so a
    # minimal production gateway config without storage env vars is legitimate.
    validate_infra_settings(settings, require_storage=False)
    app = FastAPI(title="Mesh Realtime Gateway", version=__version__, lifespan=lifespan)

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
    app.state.authorizer = build_authorizer(session_factory)
    # Resource-level subscription checks shared with the API factory: without
    # this the standalone gateway would let any workspace member subscribe to a
    # private project:{id} channel (CWE-862). Single registration source so the
    # two processes cannot drift (README §2.2).
    register_resource_checkers(app.state.authorizer, session_factory)
    register_issue_checkers(app.state.authorizer, session_factory)
    register_inbox_checkers(app.state.authorizer, session_factory)
    register_agent_checkers(app.state.authorizer, session_factory)
    register_execution_checkers(app.state.authorizer, session_factory)
    register_squad_checkers(app.state.authorizer, session_factory)
    register_chat_checkers(app.state.authorizer, session_factory)
    register_data_job_checkers(app.state.authorizer, session_factory)
    from mesh.integrations.channels import register_integration_checkers

    register_integration_checkers(app.state.authorizer, session_factory)

    install_error_handlers(app)
    app.include_router(health_router)
    app.add_api_websocket_route("/ws", realtime_ws_endpoint)
    return app
