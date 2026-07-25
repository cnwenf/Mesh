"""Mesh authentication & authorization module (docs/specs/features/auth.md).

Public surface: :class:`~mesh.auth.service.AuthService`, the FastAPI ``router``
and the ``get_current_user`` dependency. See auth.md for the authoritative
behavioural contract.
"""

from __future__ import annotations

from mesh.auth.deps import get_current_user
from mesh.auth.routes import router
from mesh.auth.service import AuthService

__all__ = ["AuthService", "get_current_user", "router"]
