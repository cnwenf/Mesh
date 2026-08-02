"""Self-managed access log without query strings (§5.3 硬约束).

uvicorn's default access log records the FULL request line including the
query string — the raw search ``q`` would land in access logs. Production
deployments run uvicorn with ``--no-access-log`` (compose / Dockerfile) and
this middleware logs **method + path only** (``request.url.path`` — never
the query string), plus status and duration, at INFO on the dedicated
``mesh.access`` logger. nginx strips args in its own log format too
(frontend/nginx.conf mesh_access) — defense in depth.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("mesh.access")
# Dedicated channel at INFO. uvicorn's logging config attaches handlers to
# the ``uvicorn`` loggers only — the root logger has none, so INFO records
# propagating up would be dropped by the WARNING-level lastResort handler.
# Give this channel its own stderr handler and keep it self-contained.
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("mesh.access %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


async def access_log_dispatch(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Log one request line — path only, never the query string (§5.3)."""
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    logger.info(
        "%s %s %s %.1fms",
        request.method,
        request.url.path,  # PATH ONLY — never request.url.query (§5.3)
        response.status_code,
        elapsed_ms,
    )
    return response
