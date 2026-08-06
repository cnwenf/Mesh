"""M-4 (MES-54): byte ceilings on long-text / JSON issue fields.

Storage-DoS guard: ``description`` (issue create/update, template
create/update) and ``template_body`` (JSONB, measured by canonical
serialized size) are capped at the schema boundary — 422
``field_too_large``, envelope carries ONLY the field + limit, never the
offending content. The instantiate chain inherits the create-side cap
(§3.9: instantiation runs the identical creation path).
"""

from __future__ import annotations

import uuid

import pytest

from mesh.db.models.member import Member
from mesh.errors import BusinessRuleError
from mesh.issue.schemas import (
    LONG_TEXT_MAX_BYTES,
    TEMPLATE_BODY_MAX_BYTES,
    CreateIssueRequest,
    CreateIssueTemplateRequest,
    InstantiateIssueTemplateRequest,
    UpdateIssueRequest,
    UpdateIssueTemplateRequest,
)
from mesh.issue.service import IssueService
from mesh.issue.templates import TemplateService

pytestmark = pytest.mark.unit


@pytest.fixture
def issue_service(session_factory) -> IssueService:
    return IssueService(session_factory)


@pytest.fixture
def template_service(issue_service) -> TemplateService:
    return TemplateService(issue_service)


async def _make_workspace(session_factory):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        workspace = Workspace(name="Limits WS", slug=f"ws-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
    return workspace


async def _make_member(session_factory, workspace, *, role="owner") -> Member:
    from mesh.db.models.user import User

    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@corp.com",
            password_hash="x",
            display_name="Lim",
        )
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace.id, member_type="human", user_id=user.id, role=role
        )
        session.add(member)
    return member


def test_create_issue_description_over_limit_rejected() -> None:
    with pytest.raises(BusinessRuleError) as exc_info:
        CreateIssueRequest(title="t", description="x" * (LONG_TEXT_MAX_BYTES + 1))
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "field_too_large"
    # the envelope must NOT echo the oversize content back
    assert exc_info.value.details == {
        "field": "description",
        "max_bytes": LONG_TEXT_MAX_BYTES,
    }


def test_create_issue_description_at_limit_accepted() -> None:
    body = CreateIssueRequest(title="t", description="x" * LONG_TEXT_MAX_BYTES)
    assert body.description is not None


def test_create_issue_multibyte_description_measured_in_bytes() -> None:
    # 400k '€' chars are well under any character limit but 1.2 MB UTF-8 —
    # the byte ceiling must catch them.
    with pytest.raises(BusinessRuleError) as exc_info:
        CreateIssueRequest(title="t", description="€" * 400_000)
    assert exc_info.value.code == "field_too_large"


def test_update_issue_description_over_limit_rejected() -> None:
    with pytest.raises(BusinessRuleError) as exc_info:
        UpdateIssueRequest(description="x" * (LONG_TEXT_MAX_BYTES + 1))
    assert exc_info.value.code == "field_too_large"
    assert exc_info.value.details["field"] == "description"


def test_execution_output_review_request_requires_a_valid_pair() -> None:
    execution_id = str(uuid.uuid4())
    with pytest.raises(BusinessRuleError) as missing:
        UpdateIssueRequest(review_execution_id=execution_id)
    assert missing.value.code == "invalid_execution_output_review"

    with pytest.raises(BusinessRuleError) as invalid:
        UpdateIssueRequest(
            review_execution_id=execution_id,
            review_decision="maybe",
        )
    assert invalid.value.code == "invalid_execution_output_review"

    valid = UpdateIssueRequest(
        review_execution_id=execution_id,
        review_decision="approved",
    )
    assert valid.review_execution_id == execution_id


def test_template_description_over_limit_rejected() -> None:
    with pytest.raises(BusinessRuleError) as exc_info:
        CreateIssueTemplateRequest(
            name="tpl", description="x" * (LONG_TEXT_MAX_BYTES + 1)
        )
    assert exc_info.value.code == "field_too_large"


def test_template_body_over_limit_rejected() -> None:
    with pytest.raises(BusinessRuleError) as exc_info:
        CreateIssueTemplateRequest(
            name="tpl", template_body={"blob": "z" * TEMPLATE_BODY_MAX_BYTES}
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "field_too_large"
    assert exc_info.value.details == {
        "field": "template_body",
        "max_bytes": TEMPLATE_BODY_MAX_BYTES,
    }


def test_template_body_at_limit_accepted() -> None:
    # JSON framing adds bytes — stay a safe margin under the ceiling.
    body = CreateIssueTemplateRequest(
        name="tpl", template_body={"blob": "z" * (TEMPLATE_BODY_MAX_BYTES - 64)}
    )
    assert body.template_body


def test_update_template_limits_enforced() -> None:
    with pytest.raises(BusinessRuleError) as exc_info:
        UpdateIssueTemplateRequest(description="x" * (LONG_TEXT_MAX_BYTES + 1))
    assert exc_info.value.code == "field_too_large"
    with pytest.raises(BusinessRuleError) as exc_info:
        UpdateIssueTemplateRequest(
            template_body={"blob": "z" * TEMPLATE_BODY_MAX_BYTES}
        )
    assert exc_info.value.code == "field_too_large"


@pytest.mark.unit
async def test_instantiate_oversize_override_description_rejected(
    session_factory, issue_service, template_service
) -> None:
    # §3.9 instantiation runs the same creation chain → the create-side
    # description cap applies to overrides too.
    workspace = await _make_workspace(session_factory)
    owner = await _make_member(session_factory, workspace)
    template = await template_service.create_template(
        actor=owner,
        workspace_id=workspace.id,
        body=CreateIssueTemplateRequest(name="base", template_body={"priority": "low"}),
    )
    with pytest.raises(BusinessRuleError) as exc_info:
        await template_service.instantiate(
            actor=owner,
            workspace_id=workspace.id,
            template_id=uuid.UUID(template["id"]),
            body=InstantiateIssueTemplateRequest(
                title="from template",
                overrides={"description": "x" * (LONG_TEXT_MAX_BYTES + 1)},
            ),
        )
    assert exc_info.value.code == "field_too_large"
