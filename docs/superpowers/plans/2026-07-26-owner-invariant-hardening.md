# Owner 不变式安全硬化(MB-M1 / MB-M2)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 owner 不变式两处漏洞 —— 停用(disable)最后一个 active owner 无保护(MB-M1)、last-owner 计数的 TOCTOU 竞态(MB-M2)—— 使工作区在任何并发交错下都恒有 ≥1 个 active owner。

**Architecture:** 新增叶子模块 `mesh/member/owner_guard.py`,提供唯一的 owner 不变式强制点 `ensure_not_last_active_owner`:先以 `SELECT ... FOR UPDATE`(按 `id` 升序,保证锁获取序一致、无死锁)锁定本工作区全部 active owner 行再计数,≤1 即 409 `last_owner`。三条会削减 active owner 的路径(降级 `workspace/members.py::change_member_role`、移除与停用 `member/service.py::remove_member` / `update_member` 状态分支)统一走该守卫。READ COMMITTED 下 FOR UPDATE 语句取新快照 + 锁串行化,第二个并发事务在胜者提交后重读,必然看到削减后的计数而被拒。

**Tech Stack:** Python 3.12 / SQLAlchemy 2.0 async(asyncpg)/ PostgreSQL 16(真实测试库)/ pytest + pytest-asyncio + pytest-cov(≥90% 门禁)/ FastAPI + uvicorn 子进程真实 e2e / ruff。

## Global Constraints

- **不变式语义**:工作区任何时刻必须存在 ≥1 个 `role='owner' AND status='active'` 的成员(成员门控要求 `status='active'` 才能进入工作区,零 active owner 只能 DB 介入恢复)。
- **错误码**:三条路径统一 409 `last_owner`(member.md §3.3),消息按操作命名(demote/remove/disable)。
- **服务端强校验**:不依赖前端禁用(member.md §5.3)。
- **覆盖率**:pytest-cov `fail_under = 90`,整体与新增代码双达标(`backend/pyproject.toml [tool.coverage]`)。
- **测试纪律**:全部测试打真实 PostgreSQL 16 + Redis(conftest 既有设施),契约路径无 mock;e2e 真实 uvicorn 子进程 + 真实 HTTP + DB 落库校验。
- **提交纪律**:git 身份 `cnwenf <cnwenf@outlook.com>`;提交信息无任何 `Co-Authored-By`;仓库内 `core.hooksPath=/dev/null`;匿名化;ruff 全绿。
- **测试运行隔离**(本机环境特有):共享 site-packages 的 `mesh` 指向另一 worktree,运行测试必须经 `workdir/run_tests.sh`(PYTHONPATH 锁定本 checkout + 独立测试库 `mesh_test_mes35` + redis db 3),勿直接裸跑 `pytest`。
- **Spec 同步**:member.md §3.3 / §5.1 / §5.3 文案补齐"停用"路径与串行化说明(属文档对齐,不改需求;在完工评论中向 Leader 说明)。

---

## File Structure

- **Create** `backend/src/mesh/member/owner_guard.py` — owner 不变式唯一强制点:`ensure_not_last_active_owner(session, *, workspace_id, error_message) -> int`。叶子模块,仅依赖 `mesh.db.models.member.Member` 与 `mesh.errors.ConflictError`,无环。
- **Modify** `backend/src/mesh/workspace/members.py:79-91` — 降级路径:plain count → 守卫调用(消息 "cannot demote the last owner of the workspace")。
- **Modify** `backend/src/mesh/member/service.py:490-502` — 移除路径:plain count → 守卫调用(消息 "cannot remove the last owner of the workspace")。
- **Modify** `backend/src/mesh/member/service.py:419-425`(update_member 状态分支)— MB-M1 新增:`new_status == "disabled" and member.role == "owner"` 时调用守卫(消息 "cannot disable the last owner of the workspace")。
- **Create** `backend/tests/unit/test_owner_guard.py` — 守卫单元测试(计数语义、disabled/removed 不计入、消息与错误码)。
- **Create** `backend/tests/unit/test_owner_invariant_concurrency.py` — 并发回归:remove×2 / demote×2 / disable×2 / 混合 竞态,其一被拒、终态恰剩 1 个 active owner。
- **Modify** `backend/tests/unit/test_member_service.py` — MB-M1 停用保护用例 + 双 owner 放行 + re-enable 不受阻。
- **Modify** `backend/tests/e2e/test_member_e2e.py` — 真实 HTTP:停用唯一 owner 409 + 落库未变;双 owner 停用放行;并发停用+移除 e2e(其一 409、DB 恒 ≥1)。
- **Modify** `docs/specs/features/member.md` — §3.3 错误表、§5.1 / §5.3 验收项补"停用"与串行化措辞。
- **Modify** `CHANGELOG.md` — 新增 `[0.10.1]` Security / Quality 段。

---

### Task 1: owner 不变式守卫模块(TDD)

**Files:**
- Create: `backend/src/mesh/member/owner_guard.py`
- Create: `backend/tests/unit/test_owner_guard.py`

**Interfaces:**
- Produces: `async def ensure_not_last_active_owner(session: AsyncSession, *, workspace_id: uuid.UUID, error_message: str) -> int` — 在传入事务内锁定 active owner 行(FOR UPDATE,id 升序)并计数;≤1 抛 `ConflictError(code="last_owner")`(消息为 `error_message`),成功返回计数(≥2)。`LAST_OWNER_CODE = "last_owner"` 常量。

- [ ] **Step 1: 写失败测试**

Create `backend/tests/unit/test_owner_guard.py`:

```python
"""Owner invariant guard tests (member.md §3.3/§5.3, MES-35 MB-M1/MB-M2).

The workspace must always retain at least one active owner (role='owner' AND
status='active'); the guard is the single enforcement point shared by the
demote / remove / disable paths. Real PostgreSQL: the guard's row locking is
exercised here against a live database, and its concurrency behavior in
test_owner_invariant_concurrency.py.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from mesh.db.models.member import Member
from mesh.errors import ConflictError
from mesh.member.owner_guard import LAST_OWNER_CODE, ensure_not_last_active_owner

pytestmark = pytest.mark.unit


async def _workspace(session_factory) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        workspace_id = (
            await session.execute(
                text(
                    "INSERT INTO workspaces (name, slug) "
                    "VALUES ('Guard WS', :s) RETURNING id"
                ),
                {"s": f"guard-{uuid.uuid4().hex[:12]}"},
            )
        ).scalar_one()
    return workspace_id


async def _add_owner(session_factory, workspace_id, *, status="active") -> uuid.UUID:
    async with session_factory() as session, session.begin():
        user_id = (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name) "
                    "VALUES (:e, 'Owner') RETURNING id"
                ),
                {"e": f"owner-{uuid.uuid4().hex[:8]}@corp.com"},
            )
        ).scalar_one()
        member_id = (
            await session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, user_id, role, status) "
                    "VALUES (:ws, 'human', :u, 'owner', :st) RETURNING id"
                ),
                {"ws": workspace_id, "u": user_id, "st": status},
            )
        ).scalar_one()
    return member_id


async def test_guard_rejects_when_single_active_owner(session_factory):
    workspace_id = await _workspace(session_factory)
    await _add_owner(session_factory, workspace_id)
    with pytest.raises(ConflictError) as excinfo:
        async with session_factory() as session, session.begin():
            await ensure_not_last_active_owner(
                session,
                workspace_id=workspace_id,
                error_message="cannot demote the last owner of the workspace",
            )
    assert excinfo.value.code == LAST_OWNER_CODE == "last_owner"
    assert "demote" in str(excinfo.value)


async def test_guard_rejects_when_zero_active_owners(session_factory):
    workspace_id = await _workspace(session_factory)
    await _add_owner(session_factory, workspace_id, status="disabled")
    with pytest.raises(ConflictError) as excinfo:
        async with session_factory() as session, session.begin():
            await ensure_not_last_active_owner(
                session,
                workspace_id=workspace_id,
                error_message="cannot remove the last owner of the workspace",
            )
    assert excinfo.value.code == "last_owner"


async def test_guard_passes_with_two_active_owners(session_factory):
    workspace_id = await _workspace(session_factory)
    await _add_owner(session_factory, workspace_id)
    await _add_owner(session_factory, workspace_id)
    async with session_factory() as session, session.begin():
        count = await ensure_not_last_active_owner(
            session,
            workspace_id=workspace_id,
            error_message="cannot disable the last owner of the workspace",
        )
    assert count == 2


async def test_guard_ignores_disabled_and_removed_owners(session_factory):
    workspace_id = await _workspace(session_factory)
    await _add_owner(session_factory, workspace_id)  # the only ACTIVE owner
    await _add_owner(session_factory, workspace_id, status="disabled")
    async with session_factory() as session, session.begin():
        removed_user = (
            await session.execute(
                text(
                    "INSERT INTO users (email, display_name) "
                    "VALUES (:e, 'Gone') RETURNING id"
                ),
                {"e": f"gone-{uuid.uuid4().hex[:8]}@corp.com"},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO members (workspace_id, member_type, user_id, role, status) "
                "VALUES (:ws, 'human', :u, 'owner', 'removed')"
            ),
            {"ws": workspace_id, "u": removed_user},
        )
    with pytest.raises(ConflictError):
        async with session_factory() as session, session.begin():
            await ensure_not_last_active_owner(
                session,
                workspace_id=workspace_id,
                error_message="cannot demote the last owner of the workspace",
            )


async def test_guard_scopes_to_workspace(session_factory):
    workspace_id = await _workspace(session_factory)
    other_id = await _workspace(session_factory)
    await _add_owner(session_factory, workspace_id)
    await _add_owner(session_factory, other_id)
    await _add_owner(session_factory, other_id)
    # other workspace has two active owners; this one still only one.
    with pytest.raises(ConflictError):
        async with session_factory() as session, session.begin():
            await ensure_not_last_active_owner(
                session,
                workspace_id=workspace_id,
                error_message="cannot remove the last owner of the workspace",
            )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `workdir/run_tests.sh tests/unit/test_owner_guard.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mesh.member.owner_guard'`

- [ ] **Step 3: 实现守卫模块**

Create `backend/src/mesh/member/owner_guard.py`:

```python
"""Owner invariant guard — a workspace must never lose its last active owner.

The roster must always contain at least one member that is both
``role='owner'`` and ``status='active'``: workspace entry is gated on
``status='active'`` (auth/rbac.py resolve_workspace_context), so a workspace
with zero active owners is unreachable and can only be repaired by direct
database intervention. This module is the single enforcement point shared by
the three paths that can strip active-owner status — role demotion
(workspace/members.py), removal and disable (member/service.py); protections
are server-enforced and never rely on UI disabling (member.md §3.3/§5.3).

TOCTOU serialization: the count is taken AFTER locking every active owner row
``FOR UPDATE`` in ascending ``id`` order. Concurrent guard calls in the same
workspace therefore queue on the first row; the loser re-reads after the
winner commits and sees the reduced count, so exactly one of two racing
"leave exactly one owner behind" operations can succeed. The shared ascending
lock order makes cross-transaction deadlocks impossible; locks release with
the enclosing transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.member import Member
from mesh.errors import ConflictError

LAST_OWNER_CODE = "last_owner"


async def ensure_not_last_active_owner(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    error_message: str,
) -> int:
    """Raise 409 ``last_owner`` if the workspace has ≤1 active owner.

    Runs inside the caller's transaction (locks held until its end).
    ``error_message`` names the rejected operation (demote / remove /
    disable). Returns the locked active-owner count (≥ 2) on success.
    """
    owner_ids = (
        await session.execute(
            select(Member.id)
            .where(
                Member.workspace_id == workspace_id,
                Member.role == "owner",
                Member.status == "active",
            )
            .order_by(Member.id.asc())
            .with_for_update()
        )
    ).scalars().all()
    if len(owner_ids) <= 1:
        raise ConflictError(error_message, code=LAST_OWNER_CODE)
    return len(owner_ids)


__all__ = ["LAST_OWNER_CODE", "ensure_not_last_active_owner"]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `workdir/run_tests.sh tests/unit/test_owner_guard.py -q`
Expected: PASS(5 项)

- [ ] **Step 5: 提交**

```bash
git add backend/src/mesh/member/owner_guard.py backend/tests/unit/test_owner_guard.py
git commit -m "feat: owner 不变式守卫模块 —— FOR UPDATE 串行化 active owner 计数(MES-35, MB-M2 基座)"
```

---

### Task 2: MB-M1 停用最后 active owner 保护(TDD)

**Files:**
- Modify: `backend/src/mesh/member/service.py`(update_member 状态分支,约 419 行)
- Modify: `backend/tests/unit/test_member_service.py`(新增停用保护用例)

**Interfaces:**
- Consumes: Task 1 的 `ensure_not_last_active_owner`。
- Produces: `update_member` 在 `active → disabled` 且目标 `role='owner'` 时抛 409 `last_owner`(消息 "cannot disable the last owner of the workspace"),与降级/移除一致;re-enable(disabled → active)不受阻。

- [ ] **Step 1: 写失败测试**

Append to `backend/tests/unit/test_member_service.py`(复用该文件既有的 workspace/成员构造 helper;若 helper 名不同,按文件现状适配 —— 该文件已有 `_setup` 风格工厂与 `session_factory` fixture):

```python
async def test_disable_last_active_owner_conflicts(session_factory):
    """MB-M1: disabling the only active owner is a 409 last_owner (member.md §5.3)."""
    from mesh.member.service import MemberPatch

    workspace_id, owner, _member, service, actor = await _two_member_setup(session_factory)
    with pytest.raises(ConflictError) as excinfo:
        await service.update_member(
            actor=actor,
            workspace_id=workspace_id,
            member_id=owner.id,
            patch=MemberPatch(status="disabled"),
        )
    assert excinfo.value.code == "last_owner"

    # 落库未变:仍是 active owner,无 disabled_at,无 status_changed 审计。
    async with session_factory() as session:
        fresh = await session.scalar(select(Member).where(Member.id == owner.id))
    assert fresh.status == "active"
    assert fresh.role == "owner"
    assert fresh.disabled_at is None
    async with session_factory() as session:
        audits = (await session.execute(select(AuditLog))).scalars().all()
    assert [a for a in audits if a.action == "member.status_changed"] == []


async def test_disable_owner_allowed_with_second_active_owner(session_factory):
    from mesh.member.service import MemberPatch
    from mesh.workspace.members import change_member_role

    workspace_id, owner, member, service, actor = await _two_member_setup(session_factory)
    # 提升第二人为 owner,再停用原 owner:允许。
    await change_member_role(
        session_factory, actor=actor, workspace_id=workspace_id,
        member_id=member.id, new_role="owner",
    )
    result = await service.update_member(
        actor=actor,
        workspace_id=workspace_id,
        member_id=owner.id,
        patch=MemberPatch(status="disabled"),
    )
    assert result["status"] == "disabled"
    async with session_factory() as session:
        fresh = await session.scalar(select(Member).where(Member.id == owner.id))
    assert fresh.status == "disabled"
    assert fresh.disabled_at is not None


async def test_reenable_disabled_owner_not_blocked_by_guard(session_factory):
    """Guard fires only on active→disabled; re-enabling increases the count."""
    from mesh.member.service import MemberPatch
    from mesh.workspace.members import change_member_role

    workspace_id, owner, member, service, actor = await _two_member_setup(session_factory)
    await change_member_role(
        session_factory, actor=actor, workspace_id=workspace_id,
        member_id=member.id, new_role="owner",
    )
    await service.update_member(
        actor=actor, workspace_id=workspace_id, member_id=owner.id,
        patch=MemberPatch(status="disabled"),
    )
    # 重新启用:守卫不得拦截。
    result = await service.update_member(
        actor=actor, workspace_id=workspace_id, member_id=owner.id,
        patch=MemberPatch(status="active"),
    )
    assert result["status"] == "active"
```

> 注:`_two_member_setup` 为本任务新增的共享 helper(置于该测试文件顶部 helper 区):返回 `(workspace_id, owner_member, plain_member, MemberService, actor)`,`actor` 即 owner(具备 manage 权限)。实现时参照文件内既有 `_setup` 的用户/工作区/成员构造 SQL 与 `WorkspaceService.create_workspace` 用法,保持同文件风格一致。

- [ ] **Step 2: 运行测试确认失败**

Run: `workdir/run_tests.sh tests/unit/test_member_service.py -q -k "disable_last_active_owner or disable_owner_allowed or reenable_disabled_owner"`
Expected: FAIL — `test_disable_last_active_owner_conflicts` 不抛 ConflictError(DID NOT RAISE);后两项因依赖停用成功与否表现不同。

- [ ] **Step 3: 实现 MB-M1 状态分支守卫**

In `backend/src/mesh/member/service.py`,模块顶部 imports 区加入:

```python
from mesh.member.owner_guard import ensure_not_last_active_owner
```

在 `update_member` 状态分支,将:

```python
                if new_status != member.status:
                    now = _now(self._clock)
                    member.status = new_status
```

替换为:

```python
                if new_status != member.status:
                    if new_status == "disabled" and member.role == "owner":
                        # Owner invariant (member.md §3.3/§5.3): disabling the
                        # last ACTIVE owner orphans the workspace — entry is
                        # gated on status='active', so zero active owners can
                        # only be fixed by DB intervention. Same 409 as the
                        # demote/remove paths; the guard's FOR UPDATE lock
                        # serializes concurrent attempts (owner_guard.py).
                        await ensure_not_last_active_owner(
                            session,
                            workspace_id=workspace_id,
                            error_message="cannot disable the last owner of the workspace",
                        )
                    now = _now(self._clock)
                    member.status = new_status
```

- [ ] **Step 4: 运行测试确认通过**

Run: `workdir/run_tests.sh tests/unit/test_member_service.py -q`
Expected: PASS(含新增 3 项,既有用例保持绿)

- [ ] **Step 5: 提交**

```bash
git add backend/src/mesh/member/service.py backend/tests/unit/test_member_service.py
git commit -m "feat: MB-M1 停用最后 active owner 触发 409 last_owner(MES-35,member.md §3.3/§5.3)"
```

---

### Task 3: MB-M2 降级/移除路径串行化 + 并发回归测试(TDD)

**Files:**
- Modify: `backend/src/mesh/workspace/members.py:79-91`(降级路径 plain count → 守卫)
- Modify: `backend/src/mesh/member/service.py:490-502`(移除路径 plain count → 守卫)
- Create: `backend/tests/unit/test_owner_invariant_concurrency.py`

**Interfaces:**
- Consumes: Task 1 守卫。
- Produces: 降级/移除路径行为不变(409 `last_owner`,消息不变),但计数在行锁保护下进行;并发下同工作区任一时刻 ≥1 active owner。

- [ ] **Step 1: 写并发回归测试(先红:验证其能捕获旧实现的竞态,再随实现转绿)**

Create `backend/tests/unit/test_owner_invariant_concurrency.py`:

```python
"""Concurrency regression for the owner invariant (MES-35 MB-M2).

Pre-fix, the active-owner count was a plain READ COMMITTED SELECT: two
concurrent "leave one owner behind" operations could both read count=2 and
both commit, leaving zero active owners. These tests fire real concurrent
transactions at real PostgreSQL and assert exactly one operation is rejected
and the invariant (>= 1 active owner) holds afterwards — for every path that
reduces the active-owner count (demote / remove / disable) and a mixed pair.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, text

from mesh.db.models.member import Member
from mesh.errors import ConflictError
from mesh.member.service import MemberPatch, MemberService
from mesh.workspace.members import change_member_role

pytestmark = pytest.mark.unit

BARRIER_PARTIES = 2


async def _two_owner_workspace(session_factory):
    """Workspace with exactly two active human owners; returns ids + actor."""
    async with session_factory() as session, session.begin():
        workspace_id = (
            await session.execute(
                text(
                    "INSERT INTO workspaces (name, slug) "
                    "VALUES ('Race WS', :s) RETURNING id"
                ),
                {"s": f"race-{uuid.uuid4().hex[:12]}"},
            )
        ).scalar_one()
        owner_ids = []
        for i in range(2):
            user_id = (
                await session.execute(
                    text(
                        "INSERT INTO users (email, display_name) "
                        "VALUES (:e, :n) RETURNING id"
                    ),
                    {"e": f"race-owner{i}-{uuid.uuid4().hex[:8]}@corp.com", "n": f"O{i}"},
                )
            ).scalar_one()
            owner_ids.append(
                (
                    await session.execute(
                        text(
                            "INSERT INTO members (workspace_id, member_type, user_id, role) "
                            "VALUES (:ws, 'human', :u, 'owner') RETURNING id"
                        ),
                        {"ws": workspace_id, "u": user_id},
                    )
                ).scalar_one()
            )
    async with session_factory() as session:
        actor = await session.scalar(select(Member).where(Member.id == owner_ids[0]))
    return workspace_id, owner_ids, actor


async def _active_owner_count(session_factory, workspace_id) -> int:
    async with session_factory() as session:
        return await session.scalar(
            select(func.count(Member.id)).where(
                Member.workspace_id == workspace_id,
                Member.role == "owner",
                Member.status == "active",
            )
        )


async def _race(op_a, op_b):
    """Run two coroutines concurrently behind a barrier; classify outcomes."""
    barrier = asyncio.Barrier(BARRIER_PARTIES)

    async def guarded(op):
        await barrier.wait()
        try:
            await op()
            return "ok"
        except ConflictError as exc:
            assert exc.code == "last_owner"
            return "conflict"

    return await asyncio.gather(guarded(op_a), guarded(op_b))


async def test_concurrent_removals_keep_one_active_owner(session_factory):
    workspace_id, (o1, o2), actor = await _two_owner_workspace(session_factory)
    service = MemberService(session_factory)
    results = await _race(
        lambda: service.remove_member(
            actor=actor, workspace_id=workspace_id, member_id=o1
        ),
        lambda: service.remove_member(
            actor=actor, workspace_id=workspace_id, member_id=o2
        ),
    )
    assert sorted(results) == ["conflict", "ok"]
    assert await _active_owner_count(session_factory, workspace_id) == 1


async def test_concurrent_demotions_keep_one_active_owner(session_factory):
    workspace_id, (o1, o2), actor = await _two_owner_workspace(session_factory)
    results = await _race(
        lambda: change_member_role(
            session_factory, actor=actor, workspace_id=workspace_id,
            member_id=o1, new_role="member",
        ),
        lambda: change_member_role(
            session_factory, actor=actor, workspace_id=workspace_id,
            member_id=o2, new_role="admin",
        ),
    )
    assert sorted(results) == ["conflict", "ok"]
    assert await _active_owner_count(session_factory, workspace_id) == 1


async def test_concurrent_disables_keep_one_active_owner(session_factory):
    workspace_id, (o1, o2), actor = await _two_owner_workspace(session_factory)
    service = MemberService(session_factory)
    results = await _race(
        lambda: service.update_member(
            actor=actor, workspace_id=workspace_id, member_id=o1,
            patch=MemberPatch(status="disabled"),
        ),
        lambda: service.update_member(
            actor=actor, workspace_id=workspace_id, member_id=o2,
            patch=MemberPatch(status="disabled"),
        ),
    )
    assert sorted(results) == ["conflict", "ok"]
    assert await _active_owner_count(session_factory, workspace_id) == 1


async def test_concurrent_mixed_remove_and_disable_keep_one_active_owner(session_factory):
    workspace_id, (o1, o2), actor = await _two_owner_workspace(session_factory)
    service = MemberService(session_factory)
    results = await _race(
        lambda: service.remove_member(
            actor=actor, workspace_id=workspace_id, member_id=o1
        ),
        lambda: service.update_member(
            actor=actor, workspace_id=workspace_id, member_id=o2,
            patch=MemberPatch(status="disabled"),
        ),
    )
    assert sorted(results) == ["conflict", "ok"]
    assert await _active_owner_count(session_factory, workspace_id) == 1


async def test_concurrent_owner_ops_across_workspaces_do_not_interfere(session_factory):
    """Locking is per-workspace: races in separate workspaces both succeed."""
    ws_a, (a1, a2), actor_a = await _two_owner_workspace(session_factory)
    ws_b, (b1, _b2), actor_b = await _two_owner_workspace(session_factory)
    service = MemberService(session_factory)
    # Different workspaces: removing one owner where two remain is fine in both.
    results = await asyncio.gather(
        service.remove_member(actor=actor_a, workspace_id=ws_a, member_id=a1),
        service.remove_member(actor=actor_b, workspace_id=ws_b, member_id=b1),
    )
    assert all(r["removed"] for r in results)
    assert await _active_owner_count(session_factory, ws_a) == 1
    assert await _active_owner_count(session_factory, ws_b) == 1
```

- [ ] **Step 2: 运行测试观察竞态(实现前)**

Run: `workdir/run_tests.sh tests/unit/test_owner_invariant_concurrency.py -q`
Expected: 并发用例大概率红(两个 ok / 终态 0 个 active owner)—— 旧实现 plain count 无锁。若因调度恰好串行而偶绿,多跑几次(`--count` 或循环)确认其不稳定;此即 MB-M2 回归证据,保留至完工评论。

- [ ] **Step 3: 降级路径接入守卫**

In `backend/src/mesh/workspace/members.py`,imports 区加入:

```python
from mesh.member.owner_guard import ensure_not_last_active_owner
```

将 79-91 行:

```python
        if target.role == "owner" and new_role != "owner":
            active_owners = await session.scalar(
                select(func.count(Member.id)).where(
                    Member.workspace_id == workspace_id,
                    Member.role == "owner",
                    Member.status == "active",
                )
            )
            if active_owners <= 1:
                raise ConflictError(
                    "cannot demote the last owner of the workspace",
                    code="last_owner",
                )
```

替换为:

```python
        if target.role == "owner" and new_role != "owner":
            # Owner invariant: count under FOR UPDATE row locks so concurrent
            # demote/remove/disable attempts serialize (owner_guard.py).
            await ensure_not_last_active_owner(
                session,
                workspace_id=workspace_id,
                error_message="cannot demote the last owner of the workspace",
            )
```

(若替换后 `func` 在该文件不再被使用,从 `sqlalchemy` import 行移除 `func`;`ConflictError` 仍被 agent-owner 分支使用,保留。)

- [ ] **Step 4: 移除路径接入守卫**

In `backend/src/mesh/member/service.py`,将 remove_member 内 490-502 行:

```python
            if member.role == "owner":
                active_owners = await session.scalar(
                    select(func.count(Member.id)).where(
                        Member.workspace_id == workspace_id,
                        Member.role == "owner",
                        Member.status == "active",
                    )
                )
                if active_owners <= 1:
                    raise ConflictError(
                        "cannot remove the last owner of the workspace",
                        code="last_owner",
                    )
```

替换为:

```python
            if member.role == "owner":
                # Owner invariant: count under FOR UPDATE row locks so
                # concurrent demote/remove/disable attempts serialize.
                await ensure_not_last_active_owner(
                    session,
                    workspace_id=workspace_id,
                    error_message="cannot remove the last owner of the workspace",
                )
```

(`func` 仍被 list_members 使用,保留 import。)

- [ ] **Step 5: 运行全部相关测试确认转绿**

Run: `workdir/run_tests.sh tests/unit/test_owner_invariant_concurrency.py tests/unit/test_member_role_service.py tests/unit/test_member_service.py tests/unit/test_owner_guard.py -q`
Expected: PASS;反复运行并发文件 5 次(`for i in $(seq 5); do ... done`)无 flake。

- [ ] **Step 6: 提交**

```bash
git add backend/src/mesh/workspace/members.py backend/src/mesh/member/service.py backend/tests/unit/test_owner_invariant_concurrency.py
git commit -m "fix: MB-M2 last-owner 计数 FOR UPDATE 串行化,消除降级/移除/停用 TOCTOU 竞态(MES-35)"
```

---

### Task 4: 真实 e2e(真实服务 + 真实 API + 落库校验)

**Files:**
- Modify: `backend/tests/e2e/test_member_e2e.py`

**Interfaces:**
- Consumes: 既有 e2e helper(`_register_and_login` / `_create_workspace` / `_invite_accept` / `_auth`)、`api_client` / `session_factory` fixture。
- Produces: 3 个 e2e 用例(停用唯一 owner 409 + 落库未变;双 owner 停用放行 + 落库;并发停用+移除 HTTP 竞态其一 409 + DB 恒 ≥1)。

- [ ] **Step 1: 编写 e2e 用例**

Append to `backend/tests/e2e/test_member_e2e.py`:

```python
# --- owner invariant hardening (MES-35 MB-M1/MB-M2) ---------------------------


async def test_disable_last_active_owner_conflicts_over_http(api_client, session_factory):
    """MB-M1: PATCH status=disabled on the only active owner → 409 last_owner,
    and the roster row is untouched in the database."""
    owner = await _register_and_login(api_client, "oi-owner@corp.com")
    ws = await _create_workspace(api_client, owner, "oi-disable")
    owner_member_id = (
        await _find_member_by_email(session_factory, ws["id"], "oi-owner@corp.com")
    )

    resp = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{owner_member_id}",
        json={"status": "disabled"},
        headers=_auth(owner),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "last_owner"

    async with session_factory() as session:
        row = await session.scalar(select(Member).where(Member.id == owner_member_id))
    assert row.status == "active"
    assert row.role == "owner"
    assert row.disabled_at is None


async def test_disable_owner_allowed_when_second_owner_exists_over_http(
    api_client, session_factory
):
    owner = await _register_and_login(api_client, "oi-two@corp.com")
    ws = await _create_workspace(api_client, owner, "oi-two")
    second_id, _second = await _invite_accept(
        api_client, owner, ws["id"], "oi-second@corp.com", role="admin"
    )
    # Promote the second member to owner, then disable the first: allowed.
    promote = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{second_id}",
        json={"role": "owner"},
        headers=_auth(owner),
    )
    assert promote.status_code == 200, promote.text
    first_id = await _find_member_by_email(session_factory, ws["id"], "oi-two@corp.com")

    resp = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{first_id}",
        json={"status": "disabled"},
        headers=_auth(owner),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "disabled"
    async with session_factory() as session:
        row = await session.scalar(select(Member).where(Member.id == first_id))
    assert row.status == "disabled"
    assert row.disabled_at is not None


async def test_concurrent_disable_and_remove_keep_one_active_owner_over_http(
    api_client, session_factory
):
    """MB-M2 over real HTTP: racing disable + remove on the two owners yields
    exactly one 409 last_owner and the database keeps exactly one active owner."""
    import asyncio

    owner = await _register_and_login(api_client, "oi-race@corp.com")
    ws = await _create_workspace(api_client, owner, "oi-race")
    second_id, _second = await _invite_accept(
        api_client, owner, ws["id"], "oi-race-2@corp.com", role="admin"
    )
    promote = await api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/members/{second_id}",
        json={"role": "owner"},
        headers=_auth(owner),
    )
    assert promote.status_code == 200, promote.text
    first_id = await _find_member_by_email(session_factory, ws["id"], "oi-race@corp.com")

    barrier = asyncio.Barrier(2)

    async def disable_first():
        await barrier.wait()
        return await api_client.patch(
            f"/api/v1/workspaces/{ws['id']}/members/{first_id}",
            json={"status": "disabled"},
            headers=_auth(owner),
        )

    async def remove_second():
        await barrier.wait()
        return await api_client.delete(
            f"/api/v1/workspaces/{ws['id']}/members/{second_id}",
            headers=_auth(owner),
        )

    resp_disable, resp_remove = await asyncio.gather(disable_first(), remove_second())
    codes = sorted([resp_disable.status_code, resp_remove.status_code])
    assert codes == [200, 409], (resp_disable.text, resp_remove.text)
    conflict = resp_disable if resp_disable.status_code == 409 else resp_remove
    assert conflict.json()["error"]["code"] == "last_owner"

    async with session_factory() as session:
        active_owners = await session.scalar(
            select(func.count(Member.id)).where(
                Member.workspace_id == ws["id"],
                Member.role == "owner",
                Member.status == "active",
            )
        )
    assert active_owners == 1
```

并在该文件 helper 区(文件顶部 helper 之后)加入:

```python
async def _find_member_by_email(session_factory, workspace_id, email: str):
    """Resolve the members.id for a human roster row by users.email."""
    async with session_factory() as session:
        return await session.scalar(
            select(Member.id)
            .join(User, Member.user_id == User.id)
            .where(Member.workspace_id == workspace_id, User.email == email)
        )
```

(文件顶部补 `from sqlalchemy import func, select` 与 `from mesh.db.models.user import User` —— 若已存在则不重复。)

- [ ] **Step 2: 运行 e2e**

Run: `workdir/run_tests.sh tests/e2e/test_member_e2e.py -q`
Expected: PASS(含新增 3 项;并发用例反复跑 5 次无 flake)。

- [ ] **Step 3: 提交**

```bash
git add backend/tests/e2e/test_member_e2e.py
git commit -m "test: owner 不变式真实 e2e —— 停用唯一 owner 409 + 并发竞态落库校验(MES-35)"
```

---

### Task 5: 全量验证 + 文档同步 + 评审 + PR

**Files:**
- Modify: `docs/specs/features/member.md`(§3.3 错误表、§5.1、§5.3)
- Modify: `CHANGELOG.md`(新增 [0.9.2])

- [ ] **Step 1: Spec 文档同步(member.md)**

§3.3 错误表,将:

```
| 409 | `last_owner` | 试图移除/降级最后一个 owner |
```

改为:

```
| 409 | `last_owner` | 试图移除/降级/停用最后一个 active owner(工作区须恒有 ≥1 个 active owner;串行化校验,并发安全) |
```

§5.1,将:

```
- [ ] 降级/移除最后一个 owner 被拒,返回 409 `last_owner`。
```

改为:

```
- [ ] 降级/移除/停用最后一个 active owner 被拒,返回 409 `last_owner`;校验在行锁(FOR UPDATE)下串行化,并发竞态中恰有一个操作被拒,任何时刻 ≥1 个 active owner。
```

§5.3,将:

```
- [ ] `last_owner` 与 `agent_owner_not_allowed` 在服务端强校验,绕过前端亦被拒。
```

改为:

```
- [ ] `last_owner` 与 `agent_owner_not_allowed` 在服务端强校验,绕过前端亦被拒。`last_owner` 覆盖降级/移除/停用三条削减 active owner 的路径,计数前先锁 active owner 行(FOR UPDATE,id 升序),TOCTOU 安全。
```

- [ ] **Step 2: CHANGELOG 新增 [0.10.1]**

在 `## [0.9.1] - 2026-07-26` 之上插入(措辞按 Keep-a-Changelog 与既有条目风格;覆盖率数字以 Step 4 实测回填):

```markdown
## [0.9.2] - 2026-07-26

member v0.6.0 安全审核(MES-29)池内优先项 MB-M1 / MB-M2 闭环:owner 不变式加固。

### Security

- **MB-M1 停用最后 active owner 保护(member.md §3.3/§5.3,MES-35)**:`PATCH /members/{id}` 状态分支此前仅校验角色降级与移除的 last-owner 保护,将唯一 active owner 置 `status='disabled'` 不拦截;因成员门控要求 `status='active'` 才能进入工作区,停用唯一 active owner 后若操作者再移除自身 → 工作区无主,只能 DB 介入恢复。现状态分支对 `role='owner'` 且 active→disabled 复用与降级/移除同款的 active owner 计数,触发 409 `last_owner`(消息 "cannot disable the last owner of the workspace");re-enable 不受影响。
- **MB-M2 last-owner 计数 TOCTOU 串行化(member.md §5.3,MES-35)**:降级/移除/停用三条路径的 active owner 计数原为 READ COMMITTED 下无锁 `SELECT count`,两个 admin 并发削减两个不同 owner 时两事务同读 count=2 可同时成功 → 0 个 active owner。新增 `mesh/member/owner_guard.py` 作为唯一强制点:计数前 `SELECT ... FOR UPDATE` 锁定全部 active owner 行(按 id 升序获取,跨事务无死锁),并发竞态被串行化,败者在胜者提交后重读削减后的计数而被拒。

### Quality

- 后端:单测 + 真实 e2e(uvicorn 子进程 + PostgreSQL 16 + Redis 全真,含 DB 落库校验与 HTTP 并发竞态)全绿;新增并发回归 5 项(remove×2 / demote×2 / disable×2 / remove+disable 混合 / 跨工作区不互相阻塞)+ 守卫单测 5 项 + MB-M1 用例 3 项 + e2e 3 项;pytest-cov **XX.XX%**(≥90% 门禁,整体与新增代码双达标)。修复前并发用例可复现双成功 / 0 active owner,修复后恒「其一 409 + 恰剩 1 active owner」。
- 文档同步:member.md §3.3 错误表 / §5.1 / §5.3 补「停用」路径与串行化措辞。
```

- [ ] **Step 3: ruff + docs 校验**

Run: `cd backend && ruff check src tests && ruff format --check src tests`
Expected: 全绿(若有格式偏差:`ruff format src tests` 后重跑)。
Run: `python ../tests/docs/check_roster_entry.py`(若仓库 docs 校验脚本存在且涉及 member 词汇,按其 README 指示运行)

- [ ] **Step 4: 全量测试 + 覆盖率(双达标证据)**

Run:
```bash
workdir/run_tests.sh --cov=mesh --cov-report=term-missing -q
```
Expected: 全部通过;`TOTAL ≥ 90%`(pyproject fail_under 门禁);term-missing 报告中 `mesh/member/owner_guard.py` 与 `service.py` 新增行无 Missing(新增代码 100%)。记录 TOTAL 数字回填 CHANGELOG。

- [ ] **Step 5: 代码评审(superpower: requesting-code-review)**

以 code-reviewer / security-reviewer 子代理对 `git diff origin/main...HEAD` 评审;CRITICAL / HIGH 必须修复后重跑 Step 3-4。

- [ ] **Step 6: 提交文档**

```bash
git add docs/specs/features/member.md CHANGELOG.md docs/superpowers/plans/2026-07-26-owner-invariant-hardening.md
git commit -m "docs: member.md owner 不变式补停用路径与串行化措辞 + CHANGELOG v0.9.2(MES-35)"
```

- [ ] **Step 7: push 前自查 + 推送 + PR**

```bash
git log @{u}..HEAD --format=%B 2>/dev/null | grep -i 'co-authored-by'   # 必须无输出
git log origin/main..HEAD --format='%an <%ae> | %cn <%ce>'              # 全部 cnwenf <cnwenf@outlook.com>
git push -u origin HEAD
gh pr create --base main --title "fix: owner 不变式加固 MB-M1/MB-M2 —— 停用保护 + TOCTOU 串行化(MES-35)" --body "..."
gh pr merge --squash --delete-branch   # 验收流程允许时;否则交验收员
```

- [ ] **Step 8: 完工评论(MES-35)**

在 Issue 上发布完工评论(`--content-file` 方式):PR 链接、修复前后对比证据(并发测试红→绿、e2e 409 + 落库)、覆盖率数字、Spec 同步说明;按 Leader 要求 **mention 安全审核员** 请求复验 MB-M1/MB-M2 闭环。随后把 Issue 状态置 `in_review`。

---

## Self-Review(计划自检结论)

1. **Spec 覆盖**:MB-M1(停用保护)→ Task 2 + Task 4;MB-M2(串行化)→ Task 1 + Task 3 + Task 4;原路径保持绿 → Task 3 Step 5 全量跑既有 test_member_role_service / test_member_service / test_member_api / test_workspace_api;UT≥90% 双达标 → Task 5 Step 4;真实 e2e → Task 4;superpower skills → Task 5 Step 5(requesting-code-review)+ 执行期 TDD(每任务红→绿)+ verification-before-completion(Task 5);匿名化 / git 纪律 → Global Constraints + Task 5 Step 7;Spec 同步 → Task 5 Step 1-2。验收清单逐项有对应任务。
2. **占位符扫描**:无 TBD / TODO;所有代码块完整;CHANGELOG 覆盖率数字标注「以 Step 4 实测回填」—— 这是运行时数据,非占位逻辑。
3. **类型一致性**:`ensure_not_last_active_owner(session, *, workspace_id, error_message) -> int` 在 Task 1/2/3 签名一致;`LAST_OWNER_CODE == "last_owner"` 一致;`MemberPatch(status=...)` / `change_member_role(..., new_role=...)` / `remove_member(...)` 签名与现有源码一致(已核对 service.py / workspace/members.py)。

---

## Post-Review Amendment(2026-07-26,评审整改记录)

Task 5 的 code-reviewer + security-reviewer 并行评审独立发现同一 HIGH:本计划初版的守卫 `ensure_not_last_active_owner` 只串行化了"调用守卫的事务",但是否调用守卫取决于未加锁旧读的 `target.role`;reduce 与 promote 竞态可整体跳过守卫(deterministic 交错可致 0 active owner)。整改后实现与原计划差异:

- 守卫原语改为 `owner_guard.lock_active_owner_set(session, *, workspace_id, target_id) -> (active_owner_count, target)`:**一条** `SELECT ... FOR UPDATE` 锁定 `(active owner 集 ∪ 目标行)`,id 升序单遍获取(无死锁),`populate_existing` 刷新 identity map 旧实体。抛 409 的职责回到三个调用方(消息/门控各异)。
- 三条路径一律**先锁后判**:no-op、agent-owner、last_owner、removed 重判全部基于锁后状态;移除/降级的门控追加 `target.status == 'active'`(disabled owner 不再误报 409 —— 原 MEDIUM)。
- 评审未采纳项:DB 级延迟约束触发器兜底(security MEDIUM,属 schema 需求变更,已在 CHANGELOG 记为后续建议);复合 PATCH 原子化(pre-existing LOW)。
- 测试增量:确定性锁阻塞-刷新证明、在途移除 → 404(remove/disable 双路径)、promote+disable+remove 三方 barrier 压力 ×10、disabled co-owner 放行 ×2。

