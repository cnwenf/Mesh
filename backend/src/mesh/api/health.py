"""Health endpoints: liveness ``/healthz`` and readiness ``/readyz``."""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

from mesh.api.envelope import DataEnvelope
from mesh.errors import ServiceUnavailableError

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=DataEnvelope[dict])
async def healthz() -> DataEnvelope[dict]:
    """Liveness: the process is up."""
    return DataEnvelope(data={"status": "ok"})


@router.get("/readyz", response_model=DataEnvelope[dict])
async def readyz(request: Request) -> DataEnvelope[dict]:
    """Readiness: database and Redis are reachable."""
    checks: dict[str, str] = {}
    unavailable: list[str] = []

    session_factory = request.app.state.session_factory
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
        unavailable.append("database")

    redis = request.app.state.redis
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"
        unavailable.append("redis")

    if unavailable:
        raise ServiceUnavailableError(
            "readiness check failed", details={"unavailable": unavailable, "checks": checks}
        )
    return DataEnvelope(data={"status": "ready", "checks": checks})
