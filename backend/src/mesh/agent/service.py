"""Agent service — CRUD, configuration versions, lifecycle, visibility.

Implements agent.md §3.1/§3.2/§3.4/§3.5 (REST surface) and §4.8 (lifecycle
state machine). Creation writes ``agents`` + ``members`` (member_type=
'agent') + the first ``agent_config_versions`` snapshot in ONE transaction
(§5.1 — a partial write rolls back entirely) and emits ``agent.created``
plus the roster ``member.added`` event (README §6.6/§6.7: business writes
and derived events commit atomically through the outbox).

Visibility (§3.5): ``workspace`` agents are readable by every workspace
member; ``private`` agents only by their owner and workspace admins. Writes
require ``agent:manage`` (owner/admin roles, auth.md §2.7 matrix); the
ownership transfer additionally allows the current agent owner.

``model_config`` (agent.md §2.4) is validated at the application layer
before save — out-of-range values return 422 ``validation_error`` with
field-level details (§3.0 error example). Every ``PATCH /config`` mints a
new immutable ``agent_config_versions`` row and moves the active pointer;
rollback COPIES an old snapshot into a new version (immutable history).
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.expression import tuple_

from mesh.auth.audit import write_audit
from mesh.auth.rbac import role_satisfies
from mesh.db.models.agent import (
    AGENT_LIFECYCLE_TRANSITIONS,
    AGENT_VISIBILITY_VALUES,
    Agent,
    AgentConfigVersion,
)
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from mesh.outbox.service import emit_realtime
from mesh.workspace.service import WORKSPACE_CHANNEL

WORKSPACE_AGENTS_CHANNEL = "workspace:{workspace_id}:agents"

_NOT_FOUND = "agent not found"

MODEL_TIER_VALUES = ("strong_reasoning", "balanced", "lightweight_fast")
REASONING_EFFORT_VALUES = ("low", "medium", "high")
TEMPERATURE_RANGE = (0.0, 2.0)
TOP_P_RANGE = (0.0, 1.0)


class _Unset:
    """Sentinel distinguishing 'field omitted' from 'field set to null'."""

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<unset>"


UNSET = _Unset()


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


def _agents_channel(workspace_id: uuid.UUID) -> str:
    return WORKSPACE_AGENTS_CHANNEL.format(workspace_id=workspace_id)


def _workspace_channel(workspace_id: uuid.UUID) -> str:
    return WORKSPACE_CHANNEL.format(workspace_id=workspace_id)


def validate_model_config(config: dict) -> None:
    """Reject out-of-range model / inference parameters (agent.md §2.4).

    Known keys are range/type checked; unknown keys pass through (JSONB
    carries forward-compatible parameters). Raises 422 ``validation_error``
    with field-level details (§3.0 error envelope example).
    """
    problems: list[dict] = []

    def _check(field: str, issue: str) -> None:
        problems.append({"field": f"model_config.{field}", "issue": issue})

    if (tier := config.get("model_tier")) is not None and tier not in MODEL_TIER_VALUES:
        _check("model_tier", "invalid_enum")
    if (temp := config.get("temperature")) is not None:
        if not isinstance(temp, (int, float)) or isinstance(temp, bool):
            _check("temperature", "invalid_type")
        elif not (TEMPERATURE_RANGE[0] <= temp <= TEMPERATURE_RANGE[1]):
            _check("temperature", "out_of_range")
    if (top_p := config.get("top_p")) is not None:
        if not isinstance(top_p, (int, float)) or isinstance(top_p, bool):
            _check("top_p", "invalid_type")
        elif not (TOP_P_RANGE[0] <= top_p <= TOP_P_RANGE[1]):
            _check("top_p", "out_of_range")
    if (max_tokens := config.get("max_tokens")) is not None:
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
            _check("max_tokens", "out_of_range")
    if (effort := config.get("reasoning_effort")) is not None and effort not in (
        REASONING_EFFORT_VALUES
    ):
        _check("reasoning_effort", "invalid_enum")
    if (stops := config.get("stop_sequences")) is not None and (
        not isinstance(stops, list) or not all(isinstance(s, str) for s in stops)
    ):
        _check("stop_sequences", "invalid_type")
    if (preset := config.get("preset")) is not None and not isinstance(preset, str):
        _check("preset", "invalid_type")
    if (model := config.get("model")) is not None and not isinstance(model, str):
        _check("model", "invalid_type")
    if (advanced := config.get("advanced")) is not None and not isinstance(advanced, dict):
        _check("advanced", "invalid_type")
    if (budget := config.get("budget")) is not None and not isinstance(budget, dict):
        _check("budget", "invalid_type")
    if (netpol := config.get("network_policy")) is not None and not isinstance(netpol, dict):
        _check("network_policy", "invalid_type")
    if problems:
        raise BusinessRuleError(
            "model_config failed validation",
            code="validation_error",
            details={"fields": problems},
        )


def _validate_avatar_url(avatar_url: str) -> None:
    """§6.16 https-only avatar; §3.4 maps this business check to 422 (not 400).

    Mirrors the shared ``validate_https_url`` rule set but raises a 422
    ``validation_error`` so avatar validation is consistent with the other
    agent field validations (e.g. ``model_config.temperature``), per §3.4.
    """
    if not avatar_url.startswith("https://"):
        raise BusinessRuleError(
            "avatar_url must be an https URL",
            code="validation_error",
            details={"fields": [{"field": "avatar_url", "issue": "invalid_scheme"}]},
        )


def _resolve_display_name(agent: Agent, member: Member | None) -> str:
    """§2.1 / §6.1 display resolution: display_override → agents.name."""
    if member is not None and member.display_override:
        return member.display_override
    return agent.name


def _encode_list_cursor(*, status: str, created_at: datetime, agent_id: uuid.UUID) -> str:
    payload = {"s": status, "t": created_at.isoformat(), "i": str(agent_id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_list_cursor(raw: str) -> tuple[str, datetime, uuid.UUID]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
        return payload["s"], datetime.fromisoformat(payload["t"]), uuid.UUID(payload["i"])
    except Exception as exc:
        raise ValidationError(
            "invalid pagination cursor",
            code="invalid_cursor",
            details={"cursor": raw[:64]},
        ) from exc


@dataclass(frozen=True)
class AgentProfilePatch:
    """A PATCH agents/{id} profile request — unset fields keep their value."""

    name: str | _Unset = UNSET
    avatar_url: str | None | _Unset = UNSET
    role_tag: str | None | _Unset = UNSET
    slug: str | None | _Unset = UNSET
    bio: str | None | _Unset = UNSET
    visibility: str | _Unset = UNSET
    trigger_on_assign: bool | _Unset = UNSET


class AgentService:
    """Stateless orchestrator over the agents + configuration tables."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._factory = session_factory
        self._clock = clock

    # -- authorization ----------------------------------------------------------

    @staticmethod
    def _is_admin(actor: Member) -> bool:
        return role_satisfies(actor.role, "agent:manage")

    @staticmethod
    def _require_create(actor: Member) -> None:
        """Creating an agent is member self-service (§4.4/§4.5/F7); guests read-only."""
        if actor.role == "guest":
            raise ForbiddenError("guests cannot create agents")

    @staticmethod
    def _assert_can_manage(actor: Member, agent: Agent) -> None:
        """§3.5: write config / binding / lifecycle / delete = owner OR admin."""
        if actor.user_id == agent.owner_user_id:
            return
        if role_satisfies(actor.role, "agent:manage"):
            return
        raise ForbiddenError("only the agent owner or an admin can manage this agent")

    @staticmethod
    def _assert_visible(actor: Member, agent: Agent) -> None:
        """§3.5: private agents are readable by their owner and admins only."""
        if agent.visibility == "workspace":
            return
        if agent.owner_user_id == actor.user_id:
            return
        if role_satisfies(actor.role, "agent:manage"):
            return
        raise NotFoundError(_NOT_FOUND)

    # -- loading ----------------------------------------------------------------

    async def _load_agent(
        self, session: AsyncSession, workspace_id: uuid.UUID, agent_id: uuid.UUID
    ) -> Agent:
        agent = await session.scalar(
            select(Agent).where(
                Agent.workspace_id == workspace_id,
                Agent.id == agent_id,
                Agent.deleted_at.is_(None),
            )
        )
        if agent is None:
            raise NotFoundError(_NOT_FOUND)
        return agent

    async def _agent_member(
        self, session: AsyncSession, workspace_id: uuid.UUID, agent: Agent
    ) -> Member | None:
        return await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id,
                Member.agent_id == agent.id,
            )
        )

    # -- serialization ------------------------------------------------------------

    def render_agent(self, agent: Agent, member: Member | None) -> dict:
        """Render the §3.2 list/summary shape.

        IDs render as strings and timestamps as RFC3339 (§3.0) so the same
        dict is safe for BOTH the JSON response and the JSONB realtime
        payload (the outbox serializes with the stdlib JSON encoder).
        """
        display_name = _resolve_display_name(agent, member)
        return {
            "id": str(agent.id),
            "member": (
                {
                    "id": str(member.id),
                    "member_type": "agent",
                    "display_name": display_name,
                    "avatar_url": agent.avatar_url,
                    "role_tag": agent.role_tag,
                    "role": member.role,
                    "status": member.status,
                }
                if member is not None
                else None
            ),
            "display_name": display_name,
            "name": agent.name,
            "avatar_url": agent.avatar_url,
            "role_tag": agent.role_tag,
            "badge_kind": agent.badge_kind,
            "lifecycle_status": agent.lifecycle_status,
            "visibility": agent.visibility,
            "trigger_on_assign": agent.trigger_on_assign,
            "owner_user_id": str(agent.owner_user_id),
            "created_at": agent.created_at.isoformat(),
            "updated_at": agent.updated_at.isoformat(),
        }

    def render_detail(
        self, agent: Agent, member: Member | None, version: AgentConfigVersion | None
    ) -> dict:
        """Render the detail shape (profile + configuration + version)."""
        rendered = self.render_agent(agent, member)
        rendered.update(
            {
                "slug": agent.slug,
                "bio": agent.bio,
                "system_instructions": agent.system_instructions,
                "model_config": agent.model_config,
                "default_runtime_id": (
                    str(agent.default_runtime_id)
                    if agent.default_runtime_id is not None
                    else None
                ),
                "active_config_version_id": (
                    str(agent.active_config_version_id)
                    if agent.active_config_version_id is not None
                    else None
                ),
                "current_version": (
                    {
                        "id": str(version.id),
                        "change_summary": version.change_summary,
                        "changed_by": str(version.changed_by),
                        "created_at": version.created_at.isoformat(),
                    }
                    if version is not None
                    else None
                ),
            }
        )
        return rendered

    # -- create -------------------------------------------------------------------

    async def create_agent(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        name: str,
        avatar_url: str | None = None,
        role_tag: str | None = None,
        slug: str | None = None,
        bio: str | None = None,
        visibility: str = "workspace",
        system_instructions: str | None = None,
        model_config: dict | None = None,
        trigger_on_assign: bool = True,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Create agents + members + the first config version atomically."""
        if visibility not in AGENT_VISIBILITY_VALUES:
            raise ValidationError("invalid visibility", details={"visibility": visibility})
        if avatar_url is not None:
            _validate_avatar_url(avatar_url)
        config = dict(model_config or {})
        validate_model_config(config)

        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            self._require_create(actor)
            now = _now(self._clock)

            agent = Agent(
                workspace_id=workspace_id,
                name=name,
                avatar_url=avatar_url,
                role_tag=role_tag,
                slug=slug,
                bio=bio,
                owner_user_id=actor.user_id,
                visibility=visibility,
                system_instructions=system_instructions,
                model_config=config,
                trigger_on_assign=trigger_on_assign,
                created_at=now,
                updated_at=now,
            )
            session.add(agent)
            await session.flush()  # populate agent.id for the roster row

            # Roster entry — agent and human members share ONE roster (§6.1).
            member = Member(
                workspace_id=workspace_id,
                member_type="agent",
                agent_id=agent.id,
                role="member",
                status="active",
                joined_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(member)

            # First immutable configuration snapshot (§2.7).
            version = AgentConfigVersion(
                workspace_id=workspace_id,
                agent_id=agent.id,
                snapshot={
                    "system_instructions": system_instructions,
                    "model_config": config,
                    "skill_versions": {},
                    "capability_grants": [],
                },
                change_summary="initial configuration",
                changed_by=actor.id,
                created_at=now,
            )
            session.add(version)
            await session.flush()

            # Overlapping composite FK guarantees the pointer targets THIS
            # agent's own version (README §6.2 rule 7, T27).
            agent.active_config_version_id = version.id

            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_agents_channel(workspace_id),
                event="agent.created",
                data=self.render_detail(agent, member, version),
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_workspace_channel(workspace_id),
                event="member.added",
                data={
                    "member_id": str(member.id),
                    "member_type": "agent",
                    "role": member.role,
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="agent.created",
                resource_type="agent",
                resource_id=agent.id,
                metadata={"name": name, "visibility": visibility},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return self.render_detail(agent, member, version)

    # -- list / detail ------------------------------------------------------------

    async def list_agents(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        status: str = "all",
        visibility: str = "all",
        owner_id: uuid.UUID | None = None,
        q: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        """§3.5: keyset pagination over (lifecycle_status, created_at, id)."""
        from mesh.agent.schemas import (
            AGENT_LIFECYCLE_FILTERS,
            AGENT_VISIBILITY_FILTERS,
        )

        if status not in AGENT_LIFECYCLE_FILTERS:
            raise ValidationError(
                "invalid status filter",
                details={"status": status, "allowed": list(AGENT_LIFECYCLE_FILTERS)},
            )
        if visibility not in AGENT_VISIBILITY_FILTERS:
            raise ValidationError(
                "invalid visibility filter",
                details={"visibility": visibility, "allowed": list(AGENT_VISIBILITY_FILTERS)},
            )
        limit = max(1, min(limit, 100))

        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = (
                select(Agent, Member)
                .outerjoin(
                    Member,
                    (Member.workspace_id == Agent.workspace_id)
                    & (Member.agent_id == Agent.id),
                )
                .where(Agent.workspace_id == workspace_id, Agent.deleted_at.is_(None))
            )
            if status != "all":
                stmt = stmt.where(Agent.lifecycle_status == status)
            # §3.5 visibility gate applies to EVERY branch (incl. an explicit
            # ``visibility=private`` filter): a non-admin may only see
            # workspace-visible agents plus their OWN private agents — never
            # another member's private agent, regardless of the filter value.
            if not self._is_admin(actor):
                stmt = stmt.where(
                    or_(Agent.visibility == "workspace", Agent.owner_user_id == actor.user_id)
                )
            if visibility != "all":
                stmt = stmt.where(Agent.visibility == visibility)
            if owner_id is not None:
                stmt = stmt.where(Agent.owner_user_id == owner_id)
            if q:
                pattern = f"%{q.strip()}%"
                stmt = stmt.where(or_(Agent.name.ilike(pattern), Agent.role_tag.ilike(pattern)))
            if cursor is not None:
                c_status, c_created, c_id = _decode_list_cursor(cursor)
                stmt = stmt.where(
                    tuple_(Agent.lifecycle_status, Agent.created_at, Agent.id)
                    > (c_status, c_created, c_id)
                )
            stmt = stmt.order_by(
                Agent.lifecycle_status.asc(), Agent.created_at.asc(), Agent.id.asc()
            ).limit(limit + 1)
            rows = (await session.execute(stmt)).all()

        items = [self.render_agent(agent, member) for agent, member in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last_agent = rows[limit - 1][0]
            next_cursor = _encode_list_cursor(
                status=last_agent.lifecycle_status,
                created_at=last_agent.created_at,
                agent_id=last_agent.id,
            )
        return items, next_cursor

    async def get_agent(
        self, *, actor: Member, workspace_id: uuid.UUID, agent_id: uuid.UUID
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            agent = await self._load_agent(session, workspace_id, agent_id)
            self._assert_visible(actor, agent)
            member = await self._agent_member(session, workspace_id, agent)
            version = None
            if agent.active_config_version_id is not None:
                version = await session.scalar(
                    select(AgentConfigVersion).where(
                        AgentConfigVersion.workspace_id == workspace_id,
                        AgentConfigVersion.id == agent.active_config_version_id,
                    )
                )
            return self.render_detail(agent, member, version)

    # -- profile update -------------------------------------------------------------

    async def update_agent(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        patch: AgentProfilePatch,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            agent = await self._load_agent(session, workspace_id, agent_id)
            self._assert_can_manage(actor, agent)
            now = _now(self._clock)
            changed: dict = {}

            if not isinstance(patch.name, _Unset) and patch.name != agent.name:
                agent.name = patch.name
                changed["name"] = patch.name
            if not isinstance(patch.avatar_url, _Unset) and patch.avatar_url != agent.avatar_url:
                if patch.avatar_url is not None:
                    _validate_avatar_url(patch.avatar_url)
                agent.avatar_url = patch.avatar_url
                changed["avatar_url"] = patch.avatar_url
            if not isinstance(patch.role_tag, _Unset) and patch.role_tag != agent.role_tag:
                agent.role_tag = patch.role_tag
                changed["role_tag"] = patch.role_tag
            if not isinstance(patch.slug, _Unset) and patch.slug != agent.slug:
                agent.slug = patch.slug
                changed["slug"] = patch.slug
            if not isinstance(patch.bio, _Unset) and patch.bio != agent.bio:
                agent.bio = patch.bio
                changed["bio"] = patch.bio
            if not isinstance(patch.visibility, _Unset) and patch.visibility != agent.visibility:
                agent.visibility = patch.visibility
                changed["visibility"] = patch.visibility
            if not isinstance(patch.trigger_on_assign, _Unset) and (
                patch.trigger_on_assign != agent.trigger_on_assign
            ):
                agent.trigger_on_assign = patch.trigger_on_assign
                changed["trigger_on_assign"] = patch.trigger_on_assign

            # §6.9: empty diff → no event, no audit (no-op save).
            if not changed:
                member = await self._agent_member(session, workspace_id, agent)
                return self.render_agent(agent, member)

            agent.updated_at = now
            await session.flush()
            member = await self._agent_member(session, workspace_id, agent)
            rendered = self.render_agent(agent, member)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_agents_channel(workspace_id),
                event="agent.updated",
                data=rendered,
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="agent.updated",
                resource_type="agent",
                resource_id=agent.id,
                metadata={"changed": sorted(changed)},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    # -- configuration versions ---------------------------------------------------

    async def update_config(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        model_config: dict | None = None,
        system_instructions: str | None | _Unset = UNSET,
        change_summary: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Mint a new immutable configuration version (§2.4 / §2.7)."""
        if model_config is None and isinstance(system_instructions, _Unset):
            raise ValidationError(
                "nothing to update",
                details={"fields": ["model_config", "system_instructions"]},
            )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            agent = await self._load_agent(session, workspace_id, agent_id)
            self._assert_can_manage(actor, agent)
            now = _now(self._clock)

            # model_config merges over the active configuration (partial patch).
            merged_config = dict(agent.model_config or {})
            if model_config is not None:
                merged_config.update(model_config)
            validate_model_config(merged_config)
            instructions = (
                agent.system_instructions
                if isinstance(system_instructions, _Unset)
                else system_instructions
            )

            agent.model_config = merged_config
            agent.system_instructions = instructions
            agent.updated_at = now

            version = AgentConfigVersion(
                workspace_id=workspace_id,
                agent_id=agent.id,
                snapshot={
                    "system_instructions": instructions,
                    "model_config": merged_config,
                    "skill_versions": {},
                    "capability_grants": [],
                },
                change_summary=change_summary or "configuration updated",
                changed_by=actor.id,
                created_at=now,
            )
            session.add(version)
            await session.flush()
            agent.active_config_version_id = version.id
            await session.flush()

            member = await self._agent_member(session, workspace_id, agent)
            rendered = self.render_detail(agent, member, version)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_agents_channel(workspace_id),
                event="agent.updated",
                data=rendered,
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="agent.config_updated",
                resource_type="agent",
                resource_id=agent.id,
                metadata={"version_id": str(version.id)},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    async def list_config_versions(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        limit = max(1, min(limit, 100))
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            agent = await self._load_agent(session, workspace_id, agent_id)
            self._assert_visible(actor, agent)
            stmt = select(AgentConfigVersion).where(
                AgentConfigVersion.workspace_id == workspace_id,
                AgentConfigVersion.agent_id == agent.id,
            )
            if cursor is not None:
                try:
                    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
                    payload = json.loads(raw)
                    c_created = datetime.fromisoformat(payload["t"])
                    c_id = uuid.UUID(payload["i"])
                except Exception as exc:
                    raise ValidationError(
                        "invalid pagination cursor",
                        code="invalid_cursor",
                        details={"cursor": cursor[:64]},
                    ) from exc
                stmt = stmt.where(
                    tuple_(AgentConfigVersion.created_at, AgentConfigVersion.id)
                    < (c_created, c_id)
                )
            stmt = stmt.order_by(
                AgentConfigVersion.created_at.desc(), AgentConfigVersion.id.desc()
            ).limit(limit + 1)
            rows = (await session.execute(stmt)).scalars().all()

        items = [
            {
                "id": row.id,
                "agent_id": row.agent_id,
                "snapshot": row.snapshot,
                "change_summary": row.change_summary,
                "changed_by": row.changed_by,
                "created_at": row.created_at,
            }
            for row in rows[:limit]
        ]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = base64.urlsafe_b64encode(
                json.dumps({"t": last.created_at.isoformat(), "i": str(last.id)}).encode()
            ).decode()
        return items, next_cursor

    async def rollback_config(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        version_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Roll back = COPY the old snapshot into a NEW version (§2.7).

        Immutable history is never rewritten; the active pointer moves to
        the fresh copy. The overlapping composite FK guarantees the target
        version belongs to this agent (else the DB rejects, T27).
        """
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            agent = await self._load_agent(session, workspace_id, agent_id)
            self._assert_can_manage(actor, agent)
            target = await session.scalar(
                select(AgentConfigVersion).where(
                    AgentConfigVersion.workspace_id == workspace_id,
                    AgentConfigVersion.agent_id == agent.id,
                    AgentConfigVersion.id == version_id,
                )
            )
            if target is None:
                raise NotFoundError("config version not found")
            now = _now(self._clock)
            snapshot = dict(target.snapshot or {})

            agent.system_instructions = snapshot.get("system_instructions")
            agent.model_config = snapshot.get("model_config") or {}
            agent.updated_at = now

            version = AgentConfigVersion(
                workspace_id=workspace_id,
                agent_id=agent.id,
                snapshot=snapshot,
                change_summary=f"rollback to version {version_id}",
                changed_by=actor.id,
                created_at=now,
            )
            session.add(version)
            await session.flush()
            agent.active_config_version_id = version.id
            await session.flush()

            member = await self._agent_member(session, workspace_id, agent)
            rendered = self.render_detail(agent, member, version)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_agents_channel(workspace_id),
                event="agent.updated",
                data=rendered,
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="agent.config_rollback",
                resource_type="agent",
                resource_id=agent.id,
                metadata={"from_version_id": str(version_id), "version_id": str(version.id)},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    # -- lifecycle (§4.8) -----------------------------------------------------------

    async def transition_lifecycle(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        action: str,
        reason: str | None = None,
        in_flight_policy: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            agent = await self._load_agent(session, workspace_id, agent_id)
            self._assert_can_manage(actor, agent)

            allowed = AGENT_LIFECYCLE_TRANSITIONS.get(agent.lifecycle_status, {})
            if action not in allowed:
                # §3.4: illegal state transition is a 409 ``conflict``.
                raise ConflictError(
                    f"cannot {action} an agent in '{agent.lifecycle_status}' state",
                    code="conflict",
                    details={
                        "from": agent.lifecycle_status,
                        "action": action,
                        "allowed_actions": sorted(allowed),
                    },
                )
            previous = agent.lifecycle_status
            target = allowed[action]
            now = _now(self._clock)
            agent.lifecycle_status = target
            agent.updated_at = now

            # Roster linkage (§4.8): disable → members.status='disabled';
            # enable/restore → back to 'active'.
            member = await self._agent_member(session, workspace_id, agent)
            if member is not None:
                if action == "disable":
                    member.status = "disabled"
                    member.disabled_at = now
                    member.updated_at = now
                elif action in ("enable", "restore") and member.status == "disabled":
                    member.status = "active"
                    member.disabled_at = None
                    member.updated_at = now

            # In-flight policy (pause): runtime consumes it once executions
            # land — no executions exist yet, so the affected count is 0.
            affected_executions = 0
            rendered = self.render_agent(agent, member)
            rendered["previous_lifecycle_status"] = previous
            rendered["affected_executions"] = affected_executions
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_agents_channel(workspace_id),
                event="agent.lifecycle_changed",
                data={
                    "id": str(agent.id),
                    "agent_id": str(agent.id),
                    "from": previous,
                    "to": target,
                    "action": action,
                    "reason": reason,
                    "in_flight_policy": in_flight_policy if action == "pause" else None,
                    "actor_member_id": str(actor.id),
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="agent.lifecycle_changed",
                resource_type="agent",
                resource_id=agent.id,
                metadata={
                    "from": previous,
                    "to": target,
                    "reason": reason,
                    "in_flight_policy": in_flight_policy if action == "pause" else None,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    # -- soft delete ------------------------------------------------------------------

    async def delete_agent(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            agent = await self._load_agent(session, workspace_id, agent_id)
            self._assert_can_manage(actor, agent)
            now = _now(self._clock)
            agent.deleted_at = now
            agent.updated_at = now

            # Roster soft terminal (§4.8): the member row survives for audit
            # but leaves the active roster (status='removed').
            member = await self._agent_member(session, workspace_id, agent)
            if member is not None:
                member.status = "removed"
                member.disabled_at = now
                member.updated_at = now

            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_agents_channel(workspace_id),
                event="agent.deleted",
                data={"id": str(agent.id), "agent_id": str(agent.id)},
            )
            if member is not None:
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=_workspace_channel(workspace_id),
                    event="member.removed",
                    data={"member_id": str(member.id), "member_type": "agent"},
                )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="agent.deleted",
                resource_type="agent",
                resource_id=agent.id,
                metadata={},
                ip_address=ip_address,
                user_agent=user_agent,
            )

    # -- ownership transfer -------------------------------------------------------

    async def transfer_ownership(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        new_owner_user_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            agent = await self._load_agent(session, workspace_id, agent_id)
            # Only the current owner or a workspace admin may transfer (§3.5).
            is_owner = agent.owner_user_id == actor.user_id
            if not is_owner and not role_satisfies(actor.role, "agent:manage"):
                raise ForbiddenError("only the owner or an admin can transfer an agent")

            # users is a global table (RLS-exempt, no workspace_id).
            target_user = await session.scalar(select(User).where(User.id == new_owner_user_id))
            if target_user is None or target_user.status != "active":
                raise BusinessRuleError(
                    "new owner must be an active user",
                    code="transfer_target_invalid",
                )
            target_member = await session.scalar(
                select(Member).where(
                    Member.workspace_id == workspace_id,
                    Member.user_id == new_owner_user_id,
                    Member.status == "active",
                )
            )
            if target_member is None or target_member.member_type != "human":
                raise BusinessRuleError(
                    "new owner must be an active human member of this workspace",
                    code="transfer_target_invalid",
                )
            previous_owner = agent.owner_user_id
            agent.owner_user_id = new_owner_user_id
            agent.updated_at = _now(self._clock)
            await session.flush()

            member = await self._agent_member(session, workspace_id, agent)
            rendered = self.render_agent(agent, member)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_agents_channel(workspace_id),
                event="agent.updated",
                data=rendered,
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="agent.transferred",
                resource_type="agent",
                resource_id=agent.id,
                metadata={
                    "previous_owner_user_id": str(previous_owner),
                    "new_owner_user_id": str(new_owner_user_id),
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    # -- roster integration (member module) ----------------------------------------

    async def list_available_agents(
        self, *, actor: Member, workspace_id: uuid.UUID
    ) -> tuple[list[dict], str | None]:
        """Active agents a manage-level actor may bind/assign."""
        self._require_manage(actor)
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            rows = (
                await session.execute(
                    select(Agent, Member)
                    .outerjoin(
                        Member,
                        (Member.workspace_id == Agent.workspace_id)
                        & (Member.agent_id == Agent.id),
                    )
                    .where(
                        Agent.workspace_id == workspace_id,
                        Agent.deleted_at.is_(None),
                        Agent.lifecycle_status == "active",
                    )
                    .order_by(Agent.created_at.asc(), Agent.id.asc())
                )
            ).all()
        return [self.render_agent(agent, member) for agent, member in rows], None
