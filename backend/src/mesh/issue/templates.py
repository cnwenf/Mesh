"""Issue templates (issue.md §3.9).

CRUD over ``issue_templates`` plus instantiation: ``template_body`` is the
baseline, request ``overrides`` win, and the SAME creation path as
``POST /workspaces/{ws}/issues`` runs (numbering §2.4, triggers §6.9).
References that have gone stale (deleted status/label/field) degrade into
``details.skipped_fields`` instead of failing the whole instantiation.
Labels / custom fields are owned by label-property.md (MES-32): prefill
entries for them are reported skipped with ``*_module_pending`` reasons until
that increment lands.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.pagination import decode_cursor, encode_cursor
from mesh.db.constraints import violates as _violates
from mesh.db.models.issue import IssueTemplate
from mesh.db.models.member import Member
from mesh.db.tenant import set_tenant_context
from mesh.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from mesh.issue.schemas import (
    CreateIssueRequest,
    CreateIssueTemplateRequest,
    InstantiateIssueTemplateRequest,
    UpdateIssueTemplateRequest,
)
from mesh.issue.service import IssueService

TEMPLATE_NOT_FOUND = "issue template not found"

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100


def _limit_page(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_LIMIT
    if limit < 1:
        raise ValidationError("limit must be >= 1", code="invalid_limit")
    return min(limit, MAX_PAGE_LIMIT)


class TemplateService:
    """Issue template CRUD + instantiation (issue.md §3.9)."""

    def __init__(self, issue_service: IssueService) -> None:
        self._issues = issue_service

    @staticmethod
    def render_template(template: IssueTemplate, creator: dict | None = None) -> dict:
        return {
            "id": str(template.id),
            "project_id": str(template.project_id)
            if template.project_id is not None
            else None,
            "name": template.name,
            "description": template.description,
            "template_body": template.template_body,
            "created_by": str(template.created_by),
            "creator": creator,
            "created_at": template.created_at,
            "updated_at": template.updated_at,
        }

    async def _creator_summary(
        self, session: AsyncSession, template: IssueTemplate
    ) -> dict | None:
        return await self._issues._member_summary(
            session, workspace_id=template.workspace_id, member_id=template.created_by
        )

    async def create_template(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        body: CreateIssueTemplateRequest,
    ) -> dict:
        if actor.role == "guest":
            raise ForbiddenError("guests cannot manage issue templates")
        project_id = uuid.UUID(body.project_id) if body.project_id else None
        factory = self._issues._factory
        async with factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            if project_id is not None:
                project = await self._issues._projects._load_project(
                    session, workspace_id=workspace_id, project_id=project_id
                )
                await self._issues._projects.assert_can_write(
                    session, viewer=actor, project=project
                )
            template = IssueTemplate(
                workspace_id=workspace_id,
                project_id=project_id,
                name=body.name,
                description=body.description,
                template_body=body.template_body,
                created_by=actor.id,
            )
            session.add(template)
            try:
                await session.flush()
            except IntegrityError as exc:
                if _violates(exc, "uq_issue_templates_name"):
                    raise ConflictError(
                        "a template with this name already exists in this scope",
                        code="template_name_taken",
                        details={"name": body.name},
                    ) from exc
                raise
            creator = await self._creator_summary(session, template)
            return self.render_template(template, creator)

    async def list_templates(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        page_limit = _limit_page(limit)
        factory = self._issues._factory
        async with factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = (
                select(IssueTemplate)
                .where(
                    IssueTemplate.workspace_id == workspace_id,
                    IssueTemplate.project_id.is_(None)
                    if project_id is None
                    else IssueTemplate.project_id.in_((project_id,))
                    | IssueTemplate.project_id.is_(None),
                )
                .order_by(IssueTemplate.created_at.desc(), IssueTemplate.id.desc())
            )
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    func.row(IssueTemplate.created_at, IssueTemplate.id)
                    < func.row(position.sort_value, position.id)
                )
            rows = list((await session.execute(stmt.limit(page_limit + 1))).scalars().all())
            next_cursor = None
            if len(rows) > page_limit:
                rows = rows[:page_limit]
                last = rows[-1]
                next_cursor = encode_cursor(last.created_at, last.id)
            rendered = []
            for template in rows:
                creator = await self._creator_summary(session, template)
                rendered.append(self.render_template(template, creator))
            return rendered, next_cursor

    async def _load_template(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, template_id: uuid.UUID
    ) -> IssueTemplate:
        template = await session.scalar(
            select(IssueTemplate).where(
                IssueTemplate.id == template_id,
                IssueTemplate.workspace_id == workspace_id,
            )
        )
        if template is None:
            raise NotFoundError(TEMPLATE_NOT_FOUND)
        return template

    async def _assert_can_manage(self, *, actor: Member, template: IssueTemplate) -> None:
        from mesh.auth.rbac import role_satisfies

        if role_satisfies(actor.role, "project:manage") or template.created_by == actor.id:
            return
        raise ForbiddenError("template creator or workspace admin required")

    async def update_template(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        template_id: uuid.UUID,
        body: UpdateIssueTemplateRequest,
    ) -> dict:
        factory = self._issues._factory
        async with factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            template = await self._load_template(
                session, workspace_id=workspace_id, template_id=template_id
            )
            await self._assert_can_manage(actor=actor, template=template)
            from mesh.issue.service import _now

            if body.name is not None and body.name != template.name:
                template.name = body.name
                template.updated_at = _now(self._issues._clock)
            if body.description is not None and body.description != template.description:
                template.description = body.description
                template.updated_at = _now(self._issues._clock)
            if body.template_body is not None and body.template_body != template.template_body:
                template.template_body = body.template_body
                template.updated_at = _now(self._issues._clock)
            requested_name = body.name  # capture: a failed flush expires instances
            try:
                await session.flush()
            except IntegrityError as exc:
                if _violates(exc, "uq_issue_templates_name"):
                    raise ConflictError(
                        "a template with this name already exists in this scope",
                        code="template_name_taken",
                        details={"name": requested_name or template.name},
                    ) from exc
                raise
            creator = await self._creator_summary(session, template)
            return self.render_template(template, creator)

    async def delete_template(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        template_id: uuid.UUID,
    ) -> dict:
        factory = self._issues._factory
        async with factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            template = await self._load_template(
                session, workspace_id=workspace_id, template_id=template_id
            )
            await self._assert_can_manage(actor=actor, template=template)
            await session.delete(template)
            await session.flush()
            return {"id": str(template_id), "deleted": True}

    async def instantiate(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        template_id: uuid.UUID,
        body: InstantiateIssueTemplateRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Create an issue from a template in ONE transaction (§3.9).

        Stale template references degrade into ``skipped_fields`` — the issue
        is still created; only the dead prefill is dropped.
        """
        factory = self._issues._factory
        async with factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            template = await self._load_template(
                session, workspace_id=workspace_id, template_id=template_id
            )
            blueprint = dict(template.template_body or {})
            blueprint.update(body.overrides or {})
            skipped: list[dict] = []

            create_kwargs: dict = {"title": body.title}
            for field in (
                "description",
                "priority",
                "estimate",
                "estimate_unit",
                "due_date",
                "start_date",
            ):
                if blueprint.get(field) is not None:
                    create_kwargs[field] = blueprint[field]
            if blueprint.get("project_id"):
                create_kwargs["project_id"] = str(blueprint["project_id"])
            if blueprint.get("assignee_id"):
                create_kwargs["assignee_id"] = str(blueprint["assignee_id"])
            # Status prefill: a deleted/out-of-scope status falls back to the
            # scope default (graceful degradation, §3.9).
            if blueprint.get("status_id"):
                from mesh.errors import NotFoundError as _NF
                from mesh.issue.statuses import resolve_status_in_scope

                try:
                    status = await resolve_status_in_scope(
                        session,
                        workspace_id=workspace_id,
                        project_id=(
                            uuid.UUID(str(blueprint["project_id"]))
                            if blueprint.get("project_id")
                            else None
                        ),
                        status_id=uuid.UUID(str(blueprint["status_id"])),
                    )
                    create_kwargs["status_id"] = str(status.id)
                except (_NF, ValueError):
                    skipped.append({"field": "status_id", "reason": "reference_stale"})
            elif blueprint.get("state_category"):
                from mesh.issue.statuses import resolve_default_status

                status = await resolve_default_status(
                    session,
                    workspace_id=workspace_id,
                    project_id=(
                        uuid.UUID(str(blueprint["project_id"]))
                        if blueprint.get("project_id")
                        else None
                    ),
                    category=str(blueprint["state_category"]),
                )
                create_kwargs["status_id"] = str(status.id)
            # Labels / custom fields degrade until label-property.md lands.
            if blueprint.get("label_ids"):
                skipped.append({"field": "label_ids", "reason": "label_module_pending"})
            if blueprint.get("custom_field_values"):
                skipped.append(
                    {"field": "custom_field_values", "reason": "custom_field_module_pending"}
                )

            request = CreateIssueRequest(**create_kwargs)
            created = await self._issues._create_issue_tx(
                session,
                actor=actor,
                workspace_id=workspace_id,
                body=request,
                ip_address=ip_address,
                user_agent=user_agent,
                template_skipped=skipped or None,
            )
            created["template_id"] = str(template.id)
            return created


__all__ = ["TemplateService"]
