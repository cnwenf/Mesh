# MES-51 issue 模块 L1–L8 安全硬化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 闭环 MES-46 终局排期归入 MES-51 的 8 项 LOW + 1 处文档瑕疵：多租查询谓词统一（L1/L2）、404 口径统一（L3/L8）、issue 五表 RLS 实测（L4）、LIKE 通配符转义（L5）、过滤合并计数（L6）、bulk 预览全量化（L7）、0010 迁移 docstring 修正（NOTE）。

**Architecture:** 全部为服务层/路由层硬化，不动数据模型与 RLS 策略定义（策略同模板生成，已正确）。L1/L2 给既有 SELECT 补 `workspace_id` 谓词（约定统一 + 收敛跨租扫描面）；L3 在路由层把成员门 404 转写为资源级 not-found 消息消除存在性 oracle（不改 SECURITY DEFINER 函数，无需新迁移）；L4 补 mesh_app 角色 fail-closed/跨租不可见实测 + rowsecurity 断言；L5/L6/L7 契约硬化；L8 实现已由 MES-48 落地，仅补负向测试。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 async / Alembic / PostgreSQL 16（真实库，RLS）/ Redis 7 / pytest + pytest-cov（≥90%）/ ruff / mypy。

## Global Constraints

- 提交身份一律 `cnwenf <cnwenf@outlook.com>`；提交信息绝无 `Co-Authored-By`/co-author 行（`core.hooksPath=/dev/null` 已设）。
- 代码/注释/文档/提交信息/分支名不得暴露任何参考来源（无对标产品名、无 multica 字样）。
- 不得回退 MES-48（H1/H2 鉴权前置 + bulk 逐条读门负向矩阵）与 MES-50（M1/M2）修复。
- Spec 为权威：§6.14 `filter_too_complex` 为 **HTTP 400**（`ValidationError` 子类）；404 口径对照 workspace.md §5.3「错误信息不泄露其它工作区存在性」。
- 常规门槛：`ruff check backend/src backend/tests`、`mypy`（对比基线不新增错误）、`pytest --cov=mesh --cov-fail-under=90` 全绿。
- 复合 FK（README §6.2）已保 issues→statuses/projects/milestones/cycles 同租：L1 谓词为纵深防御/约定统一，行为回归测试仅在签名允许传入异租 id 的路径（`_group_label`、`_assert_transition_allowed`）可构造。

---

### Task 1: L1 — 七处单 id 查询补 workspace_id 谓词

**Files:**
- Modify: `backend/src/mesh/issue/service.py`（render_issue 状态查询 ~216-218；`_group_label` project/cycle ~1068-1075；`_assert_transition_allowed` 当前状态 ~1214-1216）
- Modify: `backend/src/mesh/issue/move.py`（`compute_plan` status/milestone/cycle ~174-212）
- Test: `backend/tests/unit/test_issue_tenant_predicates.py`（新建，L1/L2 谓词回归集中于此）

**Interfaces:**
- Consumes: `IssueService.render_issue(session, issue)`、`IssueService._group_label(session, key, group_by, workspace_id)`、`IssueService._assert_transition_allowed(session, *, workspace_id, current_status_id, target_status)`、`MoveService.compute_plan(session, *, workspace_id, issue, target_project)`
- Produces: 上述方法内所有按裸 id 的 `select(...)` 均携带 `workspace_id == <同租锚>` 谓词；新建测试模块供 Task 2 复用

- [ ] **Step 1: 写失败测试（`_group_label` 跨租名泄露回归）**

新建 `backend/tests/unit/test_issue_tenant_predicates.py`：

```python
"""多租查询谓词统一回归（MES-51 L1/L2,README §6.2 rule 5/6）。

复合 FK 已保证行级同租;本文件覆盖签名允许传入异租 id 的路径:
补谓词后,异租对象一律查不到(回退 key / 空 allowed),owner 无 RLS
形态下也不产生跨租读取。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.db.models.issue import IssueStatus
from mesh.db.models.project import Cycle, Milestone, Project
from mesh.issue.service import IssueService
from mesh.project.service import ProjectService

pytestmark = pytest.mark.unit


@pytest.fixture
def issue_service(session_factory) -> IssueService:
    return IssueService(session_factory)


async def _make_workspace(session_factory, slug: str):
    from mesh.db.models.workspace import Workspace

    async with session_factory() as session, session.begin():
        ws = Workspace(name=f"WS {slug}", slug=slug)
        session.add(ws)
    return ws


async def test_group_label_does_not_leak_cross_tenant_project_name(
    session_factory, issue_service
):
    # Arrange: 两个工作区各一个项目
    ws_a = await _make_workspace(session_factory, f"a-{uuid.uuid4().hex[:8]}")
    ws_b = await _make_workspace(session_factory, f"b-{uuid.uuid4().hex[:8]}")
    project_service = ProjectService(session_factory)
    from mesh.db.models.member import Member
    from mesh.db.models.user import User

    async with session_factory() as session, session.begin():
        user = User(email=f"{uuid.uuid4().hex[:10]}@corp.com", password_hash="x")
        session.add(user)
        await session.flush()
        actor = Member(workspace_id=ws_b.id, member_type="human", user_id=user.id, role="admin")
        session.add(actor)
    from mesh.project.schemas import CreateProjectRequest

    project_b = await project_service.create_project(
        actor=actor, workspace_id=ws_b.id, body=CreateProjectRequest(name="Secret", key="SEC")
    )

    # Act: 以 ws_a 为锚解析 ws_b 项目的 key
    async with session_factory() as session:
        label = await issue_service._group_label(
            session, project_b["id"], "project", ws_a.id
        )

    # Assert: 查不到 → 原样返回 key,不回显异租项目名
    assert label == project_b["id"]
    assert label != "Secret"


async def test_group_label_does_not_leak_cross_tenant_cycle_name(
    session_factory, issue_service
):
    ws_a = await _make_workspace(session_factory, f"a-{uuid.uuid4().hex[:8]}")
    ws_b = await _make_workspace(session_factory, f"b-{uuid.uuid4().hex[:8]}")
    async with session_factory() as session, session.begin():
        cycle = Cycle(workspace_id=ws_b.id, name="Sprint B")
        session.add(cycle)
    async with session_factory() as session:
        label = await issue_service._group_label(
            session, str(cycle.id), "cycle", ws_a.id
        )
    assert label == str(cycle.id)


async def test_strict_mode_ignores_cross_tenant_current_status(session_factory, issue_service):
    # Arrange: ws_a 开启严格模式;current_status_id 传 ws_b 的状态(其
    # allowed_transitions 本会放行),target 为 ws_a 状态。
    ws_a = await _make_workspace(session_factory, f"a-{uuid.uuid4().hex[:8]}")
    ws_b = await _make_workspace(session_factory, f"b-{uuid.uuid4().hex[:8]}")
    async with session_factory() as session, session.begin():
        from mesh.db.models.workspace import Workspace

        await session.execute(
            select(Workspace).where(Workspace.id == ws_a.id)
        )  # ensure loaded
        ws_row = await session.get(Workspace, ws_a.id)
        ws_row.settings = {**(ws_row.settings or {}), "status_strict_mode": True}
        foreign = IssueStatus(
            workspace_id=ws_b.id, name="Foreign", category="todo", position=0.0
        )
        target = IssueStatus(workspace_id=ws_a.id, name="Target", category="todo", position=1.0)
        session.add_all([foreign, target])
    await session_factory().dispose()  # noqa — no-op, keep pattern

    # Act / Assert: 补谓词后 foreign 状态在 ws_a 锚下不可见 → allowed 为空 → 409
    from mesh.errors import ConflictError

    async with session_factory() as session:
        with pytest.raises(ConflictError) as exc_info:
            await issue_service._assert_transition_allowed(
                session,
                workspace_id=ws_a.id,
                current_status_id=foreign.id,
                target_status=target,
            )
    assert exc_info.value.code == "invalid_status_transition"
    assert exc_info.value.details["allowed"] == []
```

注：`test_strict_mode_ignores_cross_tenant_current_status` 里若 foreign 状态携带会放行 target 的 `allowed_transitions`，旧代码不抛异常（测试失败）；补谓词后抛 409。Arrange 中给 foreign 设置：

```python
        foreign.allowed_transitions = [str(target.id)]
```

（放在 `session.add_all` 之前。）删掉无意义的 `dispose` 行与多余 `session.execute`。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/unit/test_issue_tenant_predicates.py -v`
Expected: `_group_label` 两例 FAIL（返回异租名）；严格模式例 FAIL（不抛异常，因异租状态的 allowed_transitions 放行）。

- [ ] **Step 3: 实现 — service.py 四处补谓词**

`render_issue`（~216）：

```python
        status = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.id == issue.status_id,
                IssueStatus.workspace_id == issue.workspace_id,
            )
        )
```

`_group_label`（~1068-1075）：

```python
        if group_by == "project" and key != "no_project":
            name = await session.scalar(
                select(Project.name).where(
                    Project.id == uuid.UUID(key), Project.workspace_id == workspace_id
                )
            )
            return name or key
        if group_by == "cycle" and key != "no_cycle":
            name = await session.scalar(
                select(Cycle.name).where(
                    Cycle.id == uuid.UUID(key), Cycle.workspace_id == workspace_id
                )
            )
            return name or key
```

`_assert_transition_allowed`（~1214）：

```python
        current = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.id == current_status_id,
                IssueStatus.workspace_id == workspace_id,
            )
        )
```

- [ ] **Step 4: 实现 — move.py compute_plan 三处补谓词**

status（~174）：

```python
        status = await session.scalar(
            select(IssueStatus).where(
                IssueStatus.id == issue.status_id,
                IssueStatus.workspace_id == workspace_id,
            )
        )
```

milestone（~200）：

```python
            milestone = await session.scalar(
                select(Milestone).where(
                    Milestone.id == issue.milestone_id,
                    Milestone.workspace_id == workspace_id,
                )
            )
```

cycle（~212）：

```python
            cycle = await session.scalar(
                select(Cycle).where(
                    Cycle.id == issue.cycle_id, Cycle.workspace_id == workspace_id
                )
            )
```

- [ ] **Step 5: 运行测试确认通过 + 相关回归**

Run: `cd backend && python -m pytest tests/unit/test_issue_tenant_predicates.py tests/unit/test_issue_service.py tests/unit/test_issue_strict_mode.py tests/unit/test_issue_graph_move_bulk.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/mesh/issue/service.py backend/src/mesh/issue/move.py backend/tests/unit/test_issue_tenant_predicates.py
git commit -m "fix(security): L1 单 id 查询统一补 workspace_id 谓词,收敛多租查询约定(MES-51)"
```

---

### Task 2: L2 — _base_visibility_clause 子查询补 workspace_id

**Files:**
- Modify: `backend/src/mesh/issue/service.py`（`_base_visibility_clause` ~738-761 及唯一调用点 ~826）
- Test: `backend/tests/unit/test_issue_tenant_predicates.py`（追加）

**Interfaces:**
- Consumes: `Member.workspace_id`、`MemberProjectAccess.workspace_id`、`ProjectMember.workspace_id`、`Project.workspace_id`
- Produces: `_base_visibility_clause(self, viewer, workspace_id)`（去掉未使用的 `session` 参数）；三个子查询均带 `workspace_id` 谓词

- [ ] **Step 1: 写失败测试（编译后子查询含 workspace_id 谓词）**

追加到 `test_issue_tenant_predicates.py`：

```python
async def test_visibility_subqueries_carry_workspace_predicate(
    session_factory, issue_service
):
    """L2:guest/member 可见性子查询补 workspace_id 谓词(收敛跨租扫描面)。"""
    from mesh.db.models.member import Member
    from mesh.db.models.user import User

    ws = await _make_workspace(session_factory, f"v-{uuid.uuid4().hex[:8]}")
    async with session_factory() as session, session.begin():
        user = User(email=f"{uuid.uuid4().hex[:10]}@corp.com", password_hash="x")
        session.add(user)
        await session.flush()
        member = Member(workspace_id=ws.id, member_type="human", user_id=user.id, role="member")
        guest = Member(workspace_id=ws.id, member_type="human", user_id=user.id, role="guest")
        session.add_all([member, guest])

    clause = issue_service._base_visibility_clause(member, ws.id)
    compiled = str(clause)
    assert "project_members.workspace_id" in compiled
    assert "projects.workspace_id" in compiled

    guest_clause = issue_service._base_visibility_clause(guest, ws.id)
    assert "member_project_access.workspace_id" in str(guest_clause)
```

（`project_members` 为 ProjectMember 表名；实现前先 `grep -n '__tablename__' backend/src/mesh/db/models/project.py` 核实。）

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/unit/test_issue_tenant_predicates.py::test_visibility_subqueries_carry_workspace_predicate -v`
Expected: FAIL（TypeError：参数不匹配，或断言失败——先改签名则断言失败）。

- [ ] **Step 3: 实现**

```python
    def _base_visibility_clause(self, viewer: Member, workspace_id: uuid.UUID):
        """SQL filter restricting rows to what the viewer may read.

        三个子查询都锚定 workspace_id(README §6.2 rule 5/6):外层
        Issue.workspace_id 过滤 + RLS 已保正确性,此处收敛跨租全表扫描。
        """
        if role_satisfies(viewer.role, "project:manage"):
            return None
        if viewer.role == "guest":
            granted = select(MemberProjectAccess.project_id).where(
                MemberProjectAccess.member_id == viewer.id,
                MemberProjectAccess.workspace_id == workspace_id,
            )
            return or_(
                Issue.project_id.in_(granted),
                Issue.assignee_id == viewer.id,
                Issue.reporter_id == viewer.id,
            )
        member_projects = select(ProjectMember.project_id).where(
            ProjectMember.member_id == viewer.id,
            ProjectMember.workspace_id == workspace_id,
        )
        visible_projects = select(Project.id).where(
            Project.workspace_id == workspace_id,
            Project.visibility == "public",
            Project.deleted_at.is_(None),
        )
        return or_(
            Issue.project_id.is_(None),
            Issue.project_id.in_(member_projects),
            Issue.project_id.in_(visible_projects),
        )
```

调用点（~826）改为：

```python
            visibility = self._base_visibility_clause(viewer, workspace_id)
```

- [ ] **Step 4: 运行测试 + 可见性回归**

Run: `cd backend && python -m pytest tests/unit/test_issue_tenant_predicates.py tests/unit/test_issue_service.py tests/unit/test_issue_api.py tests/unit/test_member_project_access.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/mesh/issue/service.py backend/tests/unit/test_issue_tenant_predicates.py
git commit -m "fix(security): L2 可见性子查询补 workspace_id 谓词,收敛跨租扫描面(MES-51)"
```

---

### Task 3: L3 — 无 workspace 前缀端点统一 404 口径

**Files:**
- Modify: `backend/src/mesh/issue/routes.py`（`_context_for` ~103-108 及全部 ~15 个调用点）
- Test: `backend/tests/unit/test_issue_api.py`（追加跨租/软删 404 口径测试）

**Interfaces:**
- Consumes: `resolve_workspace_context`（成员门 404 "workspace not found"）、`_ISSUE_NOT_FOUND`/`_STATUS_NOT_FOUND`/`_TEMPLATE_NOT_FOUND` 常量
- Produces: `_context_for(session, user, workspace_id, *, not_found_message)` 将成员门 NotFoundError 转写为资源级消息；「不存在 / 存在但非成员」404 消息不可区分（workspace.md §5.3）

- [ ] **Step 1: 写失败测试**

追加到 `test_issue_api.py`：

```python
async def test_prefixless_endpoints_404_message_uniform(client):
    """L3:/issues/{id} /statuses/{id} /issue-templates/{id} 对
    「不存在」与「存在但非成员」返回同一 404 消息,消除存在性 oracle。"""
    owner_a = await _register_and_login(client, "l3-a@corp.com")
    owner_b = await _register_and_login(client, "l3-b@corp.com")
    ws_a = await _create_workspace(client, owner_a, "l3-a")
    ws_b = await _create_workspace(client, owner_b, "l3-b")
    issue_b = await _create_issue(client, owner_b, ws_b["id"])
    status_b = await client.post(
        f"/api/v1/workspaces/{ws_b['id']}/statuses",
        json={"name": "S", "category": "todo"},
        headers=_auth(owner_b),
    )
    template_b = await client.post(
        f"/api/v1/workspaces/{ws_b['id']}/issue-templates",
        json={"name": "T"},
        headers=_auth(owner_b),
    )
    random_id = str(uuid.uuid4())

    # owner_a 非 ws_b 成员:存在但非成员 vs 不存在 → 消息必须一致
    for existing, resource_msg in (
        (issue_b["id"], "issue not found"),
        (status_b.json()["data"]["id"], "issue status not found"),
        (template_b.json()["data"]["id"], "issue template not found"),
    ):
        for target in (existing, random_id):
            path = {
                "issue not found": "issues",
                "issue status not found": "statuses",
                "issue template not found": "issue-templates",
            }[resource_msg]
            resp = await client.get(f"/api/v1/{path}/{target}", headers=_auth(owner_a)) \
                if path != "statuses" else None
            # statuses/templates 无 GET 前缀less端点 → 用写端点探测
    ...
```

（实现时按端点表调整：`GET /issues/{id}`、`PATCH /statuses/{id}`（body `{}`）、`PATCH /issue-templates/{id}`（body `{}`）；断言每条 `resp.status_code == 404` 且 `resp.json()["error"]["message"]` 对 existing 与 random_id 相同且等于资源消息。）

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/unit/test_issue_api.py::test_prefixless_endpoints_404_message_uniform -v`
Expected: FAIL（存在但非成员返回 "workspace not found"）。

- [ ] **Step 3: 实现 — _context_for 转写**

```python
async def _context_for(
    session: AsyncSession,
    user: User,
    workspace_id: uuid.UUID,
    *,
    not_found_message: str,
) -> WorkspaceContext:
    """成员门 404 → 资源级 not-found 消息(workspace.md §5.3)。

    无前缀端点先经 SECURITY DEFINER 解析器拿到 workspace_id;若调用者
    不是该工作区成员,成员门抛 "workspace not found"——与「资源不存在」
    的消息差异会成为存在性 oracle。统一转写为资源消息后两态不可区分。
    """
    try:
        return await resolve_workspace_context(
            session, user=user, workspace_id=workspace_id, permission=None
        )
    except NotFoundError as exc:
        raise NotFoundError(not_found_message) from exc
```

全部调用点补 keyword（issue 系 → `not_found_message=_ISSUE_NOT_FOUND`；status 系 → `_STATUS_NOT_FOUND`；template 系 → `_TEMPLATE_NOT_FOUND`）。用 `grep -n "_context_for(session" backend/src/mesh/issue/routes.py` 清点后逐处改。

- [ ] **Step 4: 运行测试 + 全路由回归**

Run: `cd backend && python -m pytest tests/unit/test_issue_api.py tests/unit/test_issue_review_fixes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/mesh/issue/routes.py backend/tests/unit/test_issue_api.py
git commit -m "fix(security): L3 无前缀端点统一 404 消息,消除资源存在性 oracle(MES-51,workspace.md §5.3)"
```

---

### Task 4: L4 — issue 五表 RLS fail-closed / 跨租不可见 / rowsecurity 实测

**Files:**
- Create: `backend/tests/e2e/test_issue_rls_e2e.py`
- Create: `backend/tests/unit/test_issue_rls_schema.py`

**Interfaces:**
- Consumes: `tests/e2e/conftest.py::_app_role_url`、`workspace_factory`、`session_factory` 夹具；五表 `issues`/`issue_statuses`/`issue_dependencies`/`issue_activity`/`issue_templates`；策略名 `mesh_<table>_tenant`
- Produces: mesh_app 角色下五表 fail-closed（GUC 未设即报错）+ 跨租不可见实测；rowsecurity 启用 + 策略存在断言

- [ ] **Step 1: 写 rowsecurity 断言（单测层）**

`backend/tests/unit/test_issue_rls_schema.py`：

```python
"""issue 五表 RLS 启用断言(MES-51 L4,README §6.2 rule 5)。

与 test_models_schema.py / test_workspace_schema.py 的 rowsecurity 断言
同层:策略同模板生成(迁移 0009),此处补齐 issue 五表覆盖。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.unit

ISSUE_TENANT_TABLES = (
    "issues",
    "issue_statuses",
    "issue_dependencies",
    "issue_activity",
    "issue_templates",
)


async def test_issue_tables_rls_enabled_with_tenant_policies(db_session):
    rls = (
        await db_session.execute(
            text(
                "SELECT tablename, rowsecurity FROM pg_tables WHERE tablename IN "
                "('issues', 'issue_statuses', 'issue_dependencies', 'issue_activity', "
                "'issue_templates')"
            )
        )
    ).all()
    assert {table for table, enabled in rls if enabled} == set(ISSUE_TENANT_TABLES)
    policies = (
        await db_session.execute(
            text("SELECT polname, pg_get_expr(polqual, polrelid) FROM pg_policy")
        )
    ).all()
    expected = {f"mesh_{table}_tenant" for table in ISSUE_TENANT_TABLES}
    names = {name for name, _ in policies}
    assert expected <= names
    quals = {name: qual for name, qual in policies if name in expected}
    assert all("mesh.workspace_id" in (qual or "") for qual in quals.values())
```

- [ ] **Step 2: 写 mesh_app 实测（e2e 层）**

`backend/tests/e2e/test_issue_rls_e2e.py` — 仿 `test_app_role_rls_e2e.py` 结构：`app_role_engine` 夹具（mesh_app 连接）；`_seed(session_factory, ws)` 经 owner 连接在两个工作区播种最小行集（workspace 已由夹具建；直接 SQL 插入 `issue_statuses`（default todo）→ `issues`（两工作区各 1 条，identifier 唯一）→ `issue_dependencies`（需同租两 issue，故每工作区播 2 条 issue）→ `issue_activity` → `issue_templates`）。三个测试：

1. `test_app_role_issue_tables_fail_closed_without_guc`：对五表逐一 `SELECT count(*)`，均 `pytest.raises(DBAPIError)`。
2. `test_app_role_issue_tables_cross_tenant_hidden_with_guc`：`set_config('mesh.workspace_id', ws_a, true)` 后五表各自 count == 该工作区播种数（ws_b 行不可见）。
3. `test_app_role_issue_tables_write_blocked_cross_tenant`（写路径兜底）：GUC=ws_a 下 `INSERT INTO issue_templates (workspace_id=ws_b...)` → `pytest.raises(DBAPIError)`（RLS 对 INSERT 同样生效：WITH CHECK 不通过）。若策略仅 USING（无 WITH CHECK），INSERT 异租会被 USING 拦——实测确认，按实际断言报错。

播种 SQL 要点：`issues.status_id` 需指向同租状态（复合 FK）；`identifier_namespace_key/number/identifier` 非空且 `(workspace_id, identifier)` 唯一；`state_category`/`priority` 合法值；`issue_activity.field/old/new` 文本；`issue_dependencies` 复合 FK `(workspace_id, issue_id)` + `(workspace_id, depends_on_id)`。

- [ ] **Step 3: 运行**

Run: `cd backend && python -m pytest tests/unit/test_issue_rls_schema.py tests/e2e/test_issue_rls_e2e.py -v`
Expected: PASS（策略已在迁移 0009 就位,此为补实测,不应失败;若失败说明存在 drift,按 systematic-debugging 排查并上报 Leader）。

- [ ] **Step 4: Commit**

```bash
git add backend/tests/unit/test_issue_rls_schema.py backend/tests/e2e/test_issue_rls_e2e.py
git commit -m "test(security): L4 issue 五表 RLS fail-closed/跨租不可见实测 + rowsecurity 断言(MES-51)"
```

---

### Task 5: L5 — q 搜索转义 LIKE 通配符

**Files:**
- Modify: `backend/src/mesh/issue/service.py`（q 分支 ~851-855 + 新增 `_escape_like`）
- Test: `backend/tests/unit/test_issue_service.py`（追加，或新建 `test_issue_list_search.py`——追加更省夹具）

- [ ] **Step 1: 写失败测试**

```python
async def test_q_search_escapes_like_wildcards(session_factory, issue_service, ...):
    """L5:q 中 % _ \\ 按字面匹配(契约要求 LIKE 转义),不通配。"""
    # 建同工作区三条:title "100%" / "100X" / "a_b"
    # q="100%" → 只命中 "100%"(旧实现 % 通配会同时命中 "100X" 前缀相似项——
    #   更精确的反例:q="1_0" 旧实现命中 "100%" 与 "100X",新实现零命中)
    # q="1_0" → [] ; q="a_b" → 恰 ["a_b"]
```

（用既有 `_make_workspace`/`_make_member`/`create_issue` 夹具链；list_issues(viewer=owner_member, workspace_id=..., q=...)。）

- [ ] **Step 2: 运行确认失败**（q="1_0" 命中两条 → FAIL）

- [ ] **Step 3: 实现**

service.py 顶层私有函数：

```python
def _escape_like(term: str) -> str:
    """转义 LIKE 通配符,使 q 按字面子串匹配(issue.md §3.2,§5.3 输入校验)。"""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
```

q 分支：

```python
            if q is not None:
                pattern = f"%{_escape_like(q.strip())}%"
                stmt = stmt.where(
                    or_(
                        Issue.title.ilike(pattern, escape="\\"),
                        Issue.identifier.ilike(pattern, escape="\\"),
                    )
                )
```

- [ ] **Step 4: 运行测试 + 搜索回归**

Run: `cd backend && python -m pytest tests/unit/test_issue_service.py -q -k "search or q_" ; python -m pytest tests/unit/test_issue_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/mesh/issue/service.py backend/tests/unit/test_issue_service.py
git commit -m "fix(security): L5 列表搜索 q 转义 LIKE 通配符,按字面子串匹配(MES-51)"
```

---

### Task 6: L6 — 扁平参数与 filters 树合并计数 ≤20

**Files:**
- Modify: `backend/src/mesh/issue/filters.py`（`validate_filter_tree` 增 `extra_conditions` 参数；新增 `validate_combined_condition_count`）
- Modify: `backend/src/mesh/issue/service.py`（list_issues ~806-815 + ~856）
- Modify: `docs/specs/features/issue.md`（§3.2 补一句口径）
- Test: `backend/tests/unit/test_issue_service.py`（追加）

**Interfaces:**
- Produces: `validate_filter_tree(filters, *, extra_conditions: int = 0)`；`validate_combined_condition_count(flat_count: int, filters: object | None)`——合计 >20 抛 `FilterTooComplexError`（400 `filter_too_complex`，§6.14）

- [ ] **Step 1: 写失败测试**

```python
async def test_combined_flat_and_tree_condition_budget(...):
    """L6:扁平参数 + filters 树条件合计 ≤20(§6.14),超限 400。"""
    # 扁平 12 参全填 + 树 9 条件 = 21 → FilterTooComplexError(code=filter_too_complex)
    # 扁平 12 + 树 8 = 20 → 正常返回(不抛)
```

- [ ] **Step 2: 运行确认失败**（21 条件不抛 → FAIL）

- [ ] **Step 3: 实现**

filters.py：

```python
def _walk(node: Any, depth: int, counter: list[int]) -> None:  # 不变


def validate_filter_tree(filters: Any, *, extra_conditions: int = 0) -> None:
    """Depth/count-validate a structured filter tree (§6.14).

    ``extra_conditions`` 计入同请求的扁平查询参数条件数(L6 合并口径:
    扁平 + 树合计 ≤20)。
    """
    counter = [extra_conditions]
    _walk(filters, 1, counter)


def validate_combined_condition_count(flat_count: int, filters: Any | None) -> None:
    """扁平参数与 filters 树共享 §6.14 的 20 条件预算(L6)。"""
    if filters is None:
        validate_flat_condition_count(flat_count)
        return
    validate_filter_tree(filters, extra_conditions=flat_count)
```

（`_walk` 内 `counter[0] > MAX_FILTER_CONDITIONS` 检查对 extra 起点天然生效。）

service.py list_issues：删去独立的 `validate_flat_condition_count(flat_conditions)` 调用，在 filters 解析后可用处改为：

```python
        validate_combined_condition_count(flat_conditions, filters)
```

（`filters` 参数即原始树对象；`compile_filter_tree` 内部仍会自 0 再校验一次,更严不更松,保留。）

`__all__` 补 `validate_combined_condition_count`。

issue.md §3.2 过滤限制条目补：「**扁平查询参数与 `filters` 树条件合并计数**(合计 ≤20)」。

- [ ] **Step 4: 运行测试 + 过滤回归**

Run: `cd backend && python -m pytest tests/unit/test_issue_service.py -q -k "filter or condition" ; python -m pytest tests/unit/test_issue_api.py tests/unit/test_view_e2e.py -q 2>/dev/null || python -m pytest tests/unit/test_issue_api.py -q`
Expected: PASS（既有「树 21 条件 → 400」「扁平 13+ → 400」用例不回退；注意扁平最多 12 个,旧 flat-only 超限用例若存在仍走 validate_flat_condition_count 分支,行为不变）。

- [ ] **Step 5: Commit**

```bash
git add backend/src/mesh/issue/filters.py backend/src/mesh/issue/service.py backend/tests/unit/test_issue_service.py docs/specs/features/issue.md
git commit -m "fix(security): L6 过滤条件扁平+树合并计数 ≤20,对齐 §6.14 单一预算(MES-51)"
```

---

### Task 7: L7 — bulk 预览全量化（≤100）

**Files:**
- Modify: `backend/src/mesh/issue/bulk.py`（预览循环 ~77）
- Modify: `docs/specs/features/issue.md`（§3.8 聚合预览条目补全量口径）
- Test: `backend/tests/unit/test_issue_graph_move_bulk.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
async def test_bulk_move_preview_covers_all_items_up_to_schema_cap(...):
    """L7:未确认聚合预览覆盖全部 issue_ids(≤100),不再截断前 20。"""
    # 建 25 条 issue + 目标项目;POST /issues/bulk 不带 confirm
    # → 422 move_confirmation_required,details.previews 长度 == 25,
    #   每项为 plan(含 issue_id)或 error marker,无截断字段
```

- [ ] **Step 2: 运行确认失败**（previews 长度 20 → FAIL）

- [ ] **Step 3: 实现**

bulk.py：

```python
                # §3.8 预览→确认契约:预览覆盖全部条目(schema 上限 100),
                # 确认前映射/清除清单对每一项可见(MES-51 L7)。
                for raw_id in body.issue_ids:
```

issue.md §3.8 鉴权前置条目（聚合预览句）补：「聚合预览**覆盖全部条目**（`issue_ids` 上限 100），不截断」。

- [ ] **Step 4: 运行测试 + bulk 回归**

Run: `cd backend && python -m pytest tests/unit/test_issue_graph_move_bulk.py tests/unit/test_issue_move_auth.py -q`
Expected: PASS（MES-48 逐条读门负向矩阵不回退：越权项仍为 error marker 不出 plan）。

- [ ] **Step 5: Commit**

```bash
git add backend/src/mesh/issue/bulk.py backend/tests/unit/test_issue_graph_move_bulk.py docs/specs/features/issue.md
git commit -m "fix(security): L7 bulk 未确认预览全量化(≤100),消除确认前清单盲区(MES-51,§3.8)"
```

---

### Task 8: L8 — guest 写门负向测试（实现已由 MES-48 落地）

**Files:**
- Test: `backend/tests/unit/test_project_service.py`（追加 `assert_can_write` guest 三分支）

- [ ] **Step 1: 核对实现已在位**

Run: `git log --oneline -1 -L446,472:backend/src/mesh/project/service.py origin/main | head -5`
Expected: 2b79950（MES-48）已把 guest 无授权分支改 NotFoundError——仅补测试,不改实现。

- [ ] **Step 2: 写测试**

```python
async def test_assert_can_write_guest_matrix(session_factory, project_service):
    """L8:guest 无授权 → 404(不可见,非 403 存在性 oracle);
    read 授权 → 403;write 授权 → 放行。与 assert_can_view 口径一致。"""
    # 建 ws + private project + guest member
    # 无 access → NotFoundError(code not_found)
    # MemberProjectAccess permission="read" → ForbiddenError
    # permission="write" → None(放行)
```

- [ ] **Step 3: 运行**

Run: `cd backend && python -m pytest tests/unit/test_project_service.py -q -k guest`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/tests/unit/test_project_service.py
git commit -m "test(security): L8 guest 写门 404/403 三分支负向测试(MES-51,实现已由 MES-48 落地)"
```

---

### Task 9: NOTE + 文档同步 + 全量门槛 + PR

**Files:**
- Modify: `backend/migrations/versions/0010_status_strict_mode.py`（docstring）
- Modify: `CHANGELOG.md`（新版本块）
- Modify: `README.md`（如有口径表述需同步——§6.14 已写 ≤20,核对无需改则不动）

- [ ] **Step 1: 修 docstring**

```python
Revision ID: 0010
Revises: 0009
```

- [ ] **Step 2: CHANGELOG 新块**（仿既有版式，记 MES-51 L1–L8 + NOTE，不出现参考来源字样）

- [ ] **Step 3: 全量门槛**

```bash
cd backend
ruff check ../backend/src ../backend/tests   # 等价 CI: ruff check backend/src backend/tests（在仓库根执行）
python -m mypy src/mesh -p mesh 2>&1 | tail -5   # 与 main 基线对比不新增错误（先 git stash 测基线）
python -m pytest --cov=mesh --cov-report=term-missing --cov-fail-under=90
```

Expected: 全绿；覆盖率 ≥90%（新增代码逐文件核对 term-missing）。

- [ ] **Step 4: e2e 全量**

Run: `cd backend && python -m pytest tests/e2e -q`（真实服务/真实 HTTP；T1/T18/T19/T22 等不回退）

- [ ] **Step 5: push 前自查 + Commit + Push + PR**

```bash
git log @{u}..HEAD --format=%B 2>/dev/null | grep -i 'co-authored-by'   # 必须无输出
git log origin/main..HEAD --format='%an <%ae> | %cn <%ce>' | sort -u    # 必须仅 cnwenf <cnwenf@outlook.com>
git push -u origin HEAD
gh pr create --base main --title "fix(security): MES-51 issue 模块 L1–L8 安全硬化 + 0010 docstring" --body-file ./pr-body.md
```

- [ ] **Step 6: 完工评论**

`multica issue comment add` 发回 MES-51：结果 + PR 链接 + @Mesh 验收员（mention 约定见 multica-mentioning skill）。
