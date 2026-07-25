"""Error envelope shape (§6.14) and handler rendering."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from mesh.api.error_handlers import install_error_handlers
from mesh.errors import (
    INTERNAL_ERROR_MESSAGE,
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    GoneError,
    LockedError,
    MeshError,
    NotFoundError,
    PayloadTooLargeError,
    RateLimitedError,
    ServiceUnavailableError,
    StorageError,
    UnauthorizedError,
    UnsupportedMediaTypeError,
    ValidationError,
)

_EXPECTED = [
    (ValidationError, 400, "validation_error"),
    (UnauthorizedError, 401, "unauthorized"),
    (ForbiddenError, 403, "forbidden"),
    (NotFoundError, 404, "not_found"),
    (ConflictError, 409, "conflict"),
    (GoneError, 410, "gone"),
    (PayloadTooLargeError, 413, "payload_too_large"),
    (UnsupportedMediaTypeError, 415, "unsupported_media_type"),
    (BusinessRuleError, 422, "business_rule_violation"),
    (LockedError, 423, "locked"),
    (RateLimitedError, 429, "rate_limited"),
    (StorageError, 502, "storage_error"),
    (ServiceUnavailableError, 503, "service_unavailable"),
]


@pytest.mark.parametrize(("cls", "status", "code"), _EXPECTED)
def test_envelope_shape_matches_spec(cls, status, code):
    envelope = cls().to_envelope()
    assert set(envelope.keys()) == {"error"}
    assert envelope["error"]["code"] == code
    assert isinstance(envelope["error"]["message"], str) and envelope["error"]["message"]
    assert "details" not in envelope["error"]  # omitted when absent
    assert cls().status_code == status


def test_details_are_included_when_present():
    error = BusinessRuleError("nope", details={"field": "x"}, code="move_confirmation_required")
    assert error.to_envelope() == {
        "error": {
            "code": "move_confirmation_required",
            "message": "nope",
            "details": {"field": "x"},
        }
    }


def test_rate_limited_carries_retry_after_header():
    error = RateLimitedError(retry_after=42)
    assert error.headers == {"Retry-After": "42"}
    assert error.retry_after == 42
    assert RateLimitedError().headers is None


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("db=postgresql://secret-host:5432 — must not leak")

    @app.get("/mesh-error")
    async def mesh_error() -> dict:
        raise NotFoundError("issue not found", details={"id": "abc"})

    @app.get("/query")
    async def query(limit: int) -> dict:  # noqa: ARG001
        return {}

    return app


@pytest.fixture
async def client() -> httpx.AsyncClient:
    # raise_app_exceptions=False matches real server behavior: handled
    # exceptions produce responses; they are not re-raised into the client.
    transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


async def test_unhandled_exception_is_sanitized(client):
    response = await client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body == {"error": {"code": "internal_error", "message": INTERNAL_ERROR_MESSAGE}}
    # Nothing about the real exception leaks.
    assert "secret-host" not in response.text
    assert "RuntimeError" not in response.text


async def test_mesh_error_renders_envelope(client):
    response = await client.get("/mesh-error")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "issue not found", "details": {"id": "abc"}}
    }


async def test_request_validation_maps_to_400_validation_error(client):
    response = await client.get("/query", params={"limit": "not-a-number"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]["fields"]


async def test_plain_http_exception_maps_to_canonical_code():
    from fastapi import HTTPException

    app = _app()

    @app.get("/teapot")
    async def teapot() -> dict:
        raise HTTPException(status_code=404, detail="custom detail")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as plain_client:
        response = await plain_client.get("/teapot")
    assert response.json() == {"error": {"code": "not_found", "message": "custom detail"}}


def test_base_mesh_error_defaults_to_500_internal():
    error = MeshError("anything")
    assert error.status_code == 500
    assert error.to_envelope()["error"]["code"] == "internal_error"
