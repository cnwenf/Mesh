"""Onboarding API schemas (onboarding.md §3)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mesh.db.models.onboarding import ACTIVATION_CHECKLIST


class OnboardingResetRequest(BaseModel):
    """Admin reset request body (§3.1)."""

    member_id: str = Field(min_length=1)
    checklist: str = Field(default=ACTIVATION_CHECKLIST, max_length=40)
