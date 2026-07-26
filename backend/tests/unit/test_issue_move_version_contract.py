"""M-1 (MES-54): ``MoveRequest.version`` is mandatory for confirmed moves.

Spec §3.8 step 2 requires the current ``version`` on a confirmed move
(乐观锁); omitting it must fail at the SCHEMA boundary with 422
``move_version_required`` — never reach the service silently version-less.
The unconfirmed (``confirm`` defaulted away) path must stay version-free:
it is the §3.8 422-preview fallback that HANDS the version out.
"""

from __future__ import annotations

import pytest

from mesh.errors import BusinessRuleError
from mesh.issue.schemas import MoveRequest

pytestmark = pytest.mark.unit


def test_confirmed_move_without_version_rejected_at_schema() -> None:
    with pytest.raises(BusinessRuleError) as exc_info:
        MoveRequest(target_project_id="prj-1", confirm=True)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "move_version_required"
    assert exc_info.value.details == {
        "field": "version",
        "hint": "echo preview.version back",
    }


def test_confirmed_move_to_inbox_without_version_rejected_at_schema() -> None:
    with pytest.raises(BusinessRuleError) as exc_info:
        MoveRequest(target_project_id=None, confirm=True)
    assert exc_info.value.code == "move_version_required"


def test_unconfirmed_move_stays_version_free() -> None:
    # The 422-preview fallback (clients that skipped move-preview) must
    # still construct — its envelope carries the version to echo back.
    body = MoveRequest(target_project_id="prj-1")
    assert body.confirm is False
    assert body.version is None


def test_confirmed_move_with_version_constructs() -> None:
    body = MoveRequest(target_project_id="prj-1", confirm=True, version=3)
    assert body.version == 3
