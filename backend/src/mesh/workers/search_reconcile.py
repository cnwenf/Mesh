"""Daily ``members.search_name`` reconcile loop (search-command-palette.md §2.2).

Compares the projection against ``public.mesh_search_norm`` of the live
display-name chain and repairs mismatches. Write paths sync in-transaction;
this sweep is the drift backstop and makes divergence observable (the fixed
row count is logged). Runs as the cross-tenant worker role (owner) via the
SECURITY DEFINER resync function.
"""

from __future__ import annotations

import asyncio
import logging

from mesh.search.projection import reconcile_search_names

logger = logging.getLogger("mesh.workers.search_reconcile")

DEFAULT_INTERVAL_SECONDS = 86400.0  # daily (低频对账, §2.2)


async def search_reconcile_loop(
    session_factory,
    *,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    stop: asyncio.Event,
) -> None:
    """Reconcile search projections every ``interval`` seconds until ``stop``."""
    logger.info("search reconcile loop started (interval=%.1fs)", interval)
    while not stop.is_set():
        try:
            async with session_factory() as session, session.begin():
                fixed = await reconcile_search_names(session)
            if fixed:
                logger.warning("search reconcile repaired %d member row(s)", fixed)
            else:
                logger.debug("search reconcile: no drift")
        except Exception:
            logger.exception("search reconcile iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
