"""Shared deterministic world seeder for analytics tests (real rows, no mocks).

The world is built for hand-computable expectations (T33 matrix):

- members: ``admin`` (role admin → full workspace), ``m1`` (plain member,
  no private-project membership, no private agent), ``m2`` (member of the
  private project), ``m3`` (owns private agent ``pa``);
- projects: ``pub`` (public), ``priv`` (private, only m2 + managers see it);
- agents: ``wa`` (workspace-visible), ``pa`` (private, owner = m3.user_id),
  both with roster (members) rows;
- executions: ``exec_wa_priv`` (wa on a priv-project issue, completed),
  ``exec_wa_pub`` (wa on a pub-project issue, completed),
  ``exec_pa_manual`` (pa, no issue, trigger=manual, completed), plus
  in-flight rows for workload (running/queued/awaiting_approval);
- one autopilot + runs carrying token data linked to wa executions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from mesh.db.models.agent import Agent
from mesh.db.models.autopilot import Autopilot, AutopilotRun
from mesh.db.models.issue import Issue, IssueActivity, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.project import Cycle, Milestone, Project, ProjectMember
from mesh.db.models.runtime import ExecutionAttempt, TaskExecution

TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
CYCLE_START = date(2026, 7, 6)
CYCLE_END = date(2026, 7, 12)


@dataclass
class World:
    ws: object
    admin: object
    m1: object
    m2: object
    m3: object
    pub: object
    priv: object
    wa: object
    pa: object
    wa_member: object
    pa_member: object
    status_todo: object
    status_done: object
    issue_priv: object
    issue_pub_open: object
    cycle_pub: object
    cycle_priv: object
    milestone_pub: object
    autopilot: object
    exec_wa_priv: object
    exec_wa_pub: object
    exec_pa_manual: object
    exec_wa_running: object
    exec_wa_queued: object
    exec_wa_approval: object


def make_issue(
    *,
    ws,
    title: str,
    status,
    number: int,
    project=None,
    cycle=None,
    milestone=None,
    assignee=None,
    state_category: str = "todo",
    estimate=None,
    completed_at=None,
    created_at=None,
) -> Issue:
    key = project.key if project is not None else "inbox"
    kwargs: dict = {}
    if created_at is not None:
        kwargs["created_at"] = created_at
    return Issue(
        workspace_id=ws.id,
        title=title,
        identifier_namespace_key=key,
        number=number,
        identifier=f"{key}-{number}",
        status_id=status.id,
        state_category=state_category,
        project_id=project.id if project is not None else None,
        cycle_id=cycle.id if cycle is not None else None,
        milestone_id=milestone.id if milestone is not None else None,
        assignee_id=assignee.id if assignee is not None else None,
        estimate=estimate,
        completed_at=completed_at,
        **kwargs,
    )


def make_execution(
    *,
    ws,
    agent,
    issue=None,
    trigger: str = "assign",
    status: str = "completed",
    queued_at=TS,
    finished_at=TS,
) -> TaskExecution:
    return TaskExecution(
        workspace_id=ws.id,
        agent_id=agent.id,
        issue_id=issue.id if issue is not None else None,
        trigger=trigger,
        status=status,
        queued_at=queued_at,
        finished_at=finished_at,
    )


def activity_category(issue, *, actor, old: str, new: str, at: datetime) -> IssueActivity:
    return IssueActivity(
        workspace_id=issue.workspace_id,
        issue_id=issue.id,
        actor_member_id=actor.id,
        field="state_category",
        old_value=old,
        new_value=new,
        created_at=at,
    )


async def seed_world(session_factory, workspace_factory, member_factory) -> World:
    ws = await workspace_factory()
    admin = await member_factory(ws, role="admin", name="Admin")
    m1 = await member_factory(ws, role="member", name="M1")
    m2 = await member_factory(ws, role="member", name="M2")
    m3 = await member_factory(ws, role="member", name="M3")

    async with session_factory() as session, session.begin():
        pub = Project(workspace_id=ws.id, name="Pub", key=f"pub{uuid.uuid4().hex[:6]}",
                      visibility="public")
        priv = Project(workspace_id=ws.id, name="Priv", key=f"pri{uuid.uuid4().hex[:6]}",
                       visibility="private")
        session.add_all([pub, priv])
        await session.flush()

        session.add(ProjectMember(workspace_id=ws.id, project_id=priv.id,
                                  member_id=m2.id, role="member"))

        wa = Agent(workspace_id=ws.id, name="WA", owner_user_id=admin.user_id,
                   visibility="workspace")
        pa = Agent(workspace_id=ws.id, name="PA", owner_user_id=m3.user_id,
                   visibility="private")
        session.add_all([wa, pa])
        await session.flush()

        wa_member = Member(workspace_id=ws.id, member_type="agent", agent_id=wa.id,
                           role="member", status="active")
        pa_member = Member(workspace_id=ws.id, member_type="agent", agent_id=pa.id,
                           role="member", status="active")
        session.add_all([wa_member, pa_member])

        status_todo = IssueStatus(workspace_id=ws.id, name="Todo", category="todo",
                                  is_default=True)
        status_done = IssueStatus(workspace_id=ws.id, name="Done", category="done",
                                  is_default=False)
        session.add_all([status_todo, status_done])
        await session.flush()

        issue_priv = make_issue(ws=ws, title="priv issue", status=status_todo,
                                number=1, project=priv, assignee=m1)
        issue_pub_open = make_issue(ws=ws, title="pub open issue", status=status_todo,
                                    number=2, project=pub, assignee=m1)
        session.add_all([issue_priv, issue_pub_open])

        cycle_pub = Cycle(workspace_id=ws.id, name="C-pub", project_id=pub.id,
                          starts_at=CYCLE_START, ends_at=CYCLE_END)
        cycle_priv = Cycle(workspace_id=ws.id, name="C-priv", project_id=priv.id,
                           starts_at=CYCLE_START, ends_at=CYCLE_END)
        milestone_pub = Milestone(workspace_id=ws.id, title="M-pub", project_id=pub.id,
                                  target_date=date(2026, 7, 20))
        session.add_all([cycle_pub, cycle_priv, milestone_pub])
        await session.flush()

        autopilot = Autopilot(workspace_id=ws.id, name="AP", trigger_type="issue_created",
                              created_by=admin.id)
        session.add(autopilot)
        await session.flush()

        exec_wa_priv = make_execution(ws=ws, agent=wa, issue=issue_priv,
                                      status="completed")
        exec_wa_pub = make_execution(ws=ws, agent=wa, issue=issue_pub_open,
                                     status="completed")
        exec_pa_manual = make_execution(ws=ws, agent=pa, issue=None, trigger="manual",
                                        status="completed")
        exec_wa_running = make_execution(ws=ws, agent=wa, issue=issue_pub_open,
                                         status="running", finished_at=None)
        exec_wa_queued = make_execution(ws=ws, agent=wa, issue=None, status="queued",
                                        finished_at=None)
        exec_wa_approval = make_execution(ws=ws, agent=wa, issue=None,
                                          status="awaiting_approval", finished_at=None)
        session.add_all([exec_wa_priv, exec_wa_pub, exec_pa_manual, exec_wa_running,
                         exec_wa_queued, exec_wa_approval])
        await session.flush()

        session.add(ExecutionAttempt(workspace_id=ws.id, execution_id=exec_wa_priv.id,
                                     attempt_number=1, status="completed",
                                     started_at=TS, finished_at=TS))

        session.add(AutopilotRun(workspace_id=ws.id, autopilot_id=autopilot.id,
                                 trigger_type="issue_created",
                                 execution_id=exec_wa_pub.id, status="succeeded",
                                 started_at=TS, prompt_tokens=100, completion_tokens=50))

    return World(
        ws=ws, admin=admin, m1=m1, m2=m2, m3=m3, pub=pub, priv=priv, wa=wa, pa=pa,
        wa_member=wa_member, pa_member=pa_member, status_todo=status_todo,
        status_done=status_done, issue_priv=issue_priv, issue_pub_open=issue_pub_open,
        cycle_pub=cycle_pub, cycle_priv=cycle_priv, milestone_pub=milestone_pub,
        autopilot=autopilot, exec_wa_priv=exec_wa_priv, exec_wa_pub=exec_wa_pub,
        exec_pa_manual=exec_pa_manual, exec_wa_running=exec_wa_running,
        exec_wa_queued=exec_wa_queued, exec_wa_approval=exec_wa_approval,
    )
