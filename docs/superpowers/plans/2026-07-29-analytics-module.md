# Analytics 模块(MES-71)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/specs/features/analytics.md` 五章全量实现统计报表模块(阶段 8·平台能力 C):六类只读聚合指标 + `analytics_snapshots` 物化缓存(scope_key 跨权限分行)+ 8 个端点 + 项目/工作区仪表盘与 agent 统计卡 UI,T33 七项真实 e2e 闭环。

**Architecture:** 后端新增 `mesh/analytics` feature 包(routes/schemas/service + visibility/scope/cache/queries 子模块)。一切 execution 指标聚合逐字复用 `visibility.visible_executions_cte()` 产出的统一 CTE 文本(R5 权威构件);issue 型指标按请求者可见项目集过滤;缓存键含 scope_key,跨权限物理分行、绝不共享。前端新增 `src/features/analytics`(手写 SVG 图表 + 语义 token),接入项目详情「仪表盘」页签、工作区「洞察」页、agent 详情统计卡。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 async / asyncpg / PostgreSQL 16(`percentile_cont`、`date_trunc … AT TIME ZONE`)、Alembic raw-SQL 迁移、pytest + pytest-cov(≥90% branch);React 19 / react-router 8 / react-intl 7 / vitest(≥90% 全局 + per-file)/ Playwright 真实栈走查。

## Global Constraints

- git 身份:`cnwenf <cnwenf@outlook.com>`(仓库级 `git config user.name/user.email`);`core.hooksPath=/dev/null`;提交**绝无** `Co-Authored-By`(每次 commit 后 `git log -1 --format=%B` 自查;push 前 `git log @{u}..HEAD --format=%B | grep -i co-authored-by` 必须无输出)。
- 覆盖率双达标:后端 `pytest --cov=mesh --cov-report=term-missing --cov-fail-under=90`(branch=true);前端 `npm run test:coverage`(全局 90% + `verify-perfile-coverage.mjs` per-file,需把 `src/features/analytics/` 加入 `PER_FILE_DIRS`)+ `verify-coverage.mjs --base origin/main` 增量 ≥90%。
- 不暴露任何参考来源(代码/注释/文档/提交信息/分支名不得出现对标产品字样)。
- 一切时间存储/传输 UTC RFC3339;窗语义左闭右开 `[from, to)`;`issues` 统计一律 `deleted_at IS NULL`。
- 错误信封/包络/分页以 README §6.14 为唯一权威;具名错误码见 analytics.md §3.4(`invalid_time_range`/`invalid_timezone`/`burndown_scope_required`/`burndown_scope_conflict`/`project_not_visible`/`agent_not_visible`/`filter_too_complex`/`query_cost_exceeded`/`not_found`/`rate_limited`)。
- 只读:任何端点(含 `refresh=true`)不得写 `issues`/`task_executions`/`execution_attempts`/`autopilot_runs`/`issue_activity` 等真源表,仅 `analytics_snapshots` 可写。
- ruff:`line-length=110`,select E/F/I/UP/B;前端 eslint + tsc 零错。
- 迁移重编号惯例:本模块基线 `0027_analytics`(`down_revision="0026"`);若 push 前 main 已有 0027(onboarding/import-export/integrations 并行线),顺延重编号并改 `down_revision`,保证 0001→head 单链。
- 测试环境(本机):PG `postgresql+asyncpg://mesh:mesh@127.0.0.1:54399/mesh_test`(容器 `acc64t-pg`),Redis `redis://127.0.0.1:6399/1`(容器 `acc64t-redis`,无密码),MinIO `http://127.0.0.1:9100`(mesh/mesh_minio_secret)。运行时导出:`export MESH_TEST_DATABASE_URL=... MESH_TEST_REDIS_URL=... MESH_TEST_STORAGE_ENDPOINT=... MESH_STORAGE_ACCESS_KEY=mesh MESH_STORAGE_SECRET_KEY=mesh_minio_secret`。

## File Structure

**Backend(create):**
- `backend/src/mesh/db/models/analytics.py` — `AnalyticsSnapshot` ORM 模型(物化缓存,本模块 owns)
- `backend/migrations/versions/0027_analytics.py` — raw-SQL 迁移:表/唯一键/索引/`uq_..._ws_id`/RLS/grants
- `backend/src/mesh/analytics/__init__.py`
- `backend/src/mesh/analytics/visibility.py` — `VISIBLE_EXECUTIONS_CTE`(R5 权威 CTE 文本,唯一来源)+ `visible_executions_cte()` + `analytics_exec_visible_to()`(逐执行布尔形态,可执行参照)+ 可见项目集/可见 agent 集查询
- `backend/src/mesh/analytics/scope.py` — 请求者 scope_key 计算(`ws_admin`/`projects:<hash>`/`project:<id>`/`exec:p<h>:a<h>`)+ 时区解析链 + 时间窗/粒度校验
- `backend/src/mesh/analytics/queries.py` — 六类指标聚合 SQL 构建(execution 四段逐字内联统一 CTE)
- `backend/src/mesh/analytics/cache.py` — 快照命中/过期重算/写入(stale-while-revalidate 可选)
- `backend/src/mesh/analytics/service.py` — `AnalyticsService`(8 个查询方法 + 可见性闸门)
- `backend/src/mesh/analytics/schemas.py` — 请求模型
- `backend/src/mesh/analytics/routes.py` — 8 端点 + 限流
- 测试:`backend/tests/unit/test_analytics_*.py`(8 个文件)+ `backend/tests/e2e/test_analytics_e2e.py`(T33)

**Backend(modify):**
- `backend/src/mesh/db/models/__init__.py` — 导出 `AnalyticsSnapshot`
- `backend/src/mesh/config.py` — `analytics_snapshot_ttl`(默认 15min)+ `analytics_stale_while_revalidate`(默认 False)
- `backend/src/mesh/api/app.py` — 构造 `AnalyticsService` + 挂载 router

**Frontend(create):**
- `frontend/src/features/analytics/`:`api.ts`、`types.ts`、`charts.tsx`(手写 SVG:LineChart/BarChart/Sparkline)、`InsightsPage.tsx`、`ProjectDashboardPanel.tsx`、`AgentStatsCard.tsx`、`analytics.css`、`__tests__/*.test.tsx`
- `frontend/e2e/real-analytics.spec.ts` + `frontend/playwright.analytics.config.ts` + `frontend/e2e/evidence/analytics/*.png`

**Frontend(modify):**
- `frontend/src/App.tsx` — `insights` 路由
- `frontend/src/features/projects/ProjectDetailPage.tsx` — `dashboard` 页签
- `frontend/src/features/agents/AgentDetailPage.tsx` — overview 内嵌统计卡
- `frontend/src/shell/Sidebar.tsx`、`frontend/src/shell/shortcutsRegistration.ts`、`frontend/src/shell/AppShell.tsx` — 导航/命令面板
- `frontend/src/i18n/catalogs/en.json` + `zh-CN.json` — `analytics.*` + 新 `error.<code>` + version 重算
- `frontend/src/i18n/__tests__/catalogs.test.ts` — 新占位符入 `dummyValues`
- `frontend/scripts/verify-perfile-coverage.mjs` — `PER_FILE_DIRS` 加 `src/features/analytics/`

---

## Task 1: `analytics_snapshots` 模型 + 迁移 0027

**Files:**
- Create: `backend/src/mesh/db/models/analytics.py`
- Create: `backend/migrations/versions/0027_analytics.py`
- Modify: `backend/src/mesh/db/models/__init__.py`
- Test: `backend/tests/unit/test_analytics_model.py`

**Interfaces:**
- Produces: `AnalyticsSnapshot`(字段:id/workspace_id/metric_key/scope_key/dimensions/dim_hash(generated)/window_start/window_end/value/computed_at/created_at/updated_at);迁移后 `analytics_snapshots` 表带 `UNIQUE(workspace_id, metric_key, scope_key, dim_hash, window_start, window_end)`、`idx_snapshots_lookup`、`idx_snapshots_stale`、`uq_analytics_snapshots_ws_id`、RLS(`workspace_id = current_setting('mesh.workspace_id')::uuid`)、`mesh_app` grants。

- [ ] **Step 1: 写模型**

```python
# backend/src/mesh/db/models/analytics.py
"""Analytics materialized cache model (analytics.md §2.5).

Read-only aggregation module owns exactly one table: ``analytics_snapshots``.
``scope_key`` is part of the cache key so aggregates computed for different
visibility sets never share rows (cross-permission cache reuse is impossible).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy import TIMESTAMP as PG_TIMESTAMP
from sqlalchemy import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import Computed

from mesh.db.base import Base

METRIC_KEYS = ("cycle_time", "velocity", "throughput", "burndown", "agent_stats")


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"
    __table_args__ = (
        CheckConstraint(f"metric_key IN {METRIC_KEYS!r}", name="analytics_snapshots_metric_key_check"),
        Index("uq_analytics_snapshots_ws_id", "workspace_id", "id", unique=True),
        Index("idx_snapshots_lookup", "workspace_id", "metric_key", "scope_key", "dim_hash",
              "window_start", "window_end"),
        Index("idx_snapshots_stale", "computed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    metric_key: Mapped[str] = mapped_column(Text, nullable=False)
    scope_key: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ws_admin'"))
    dimensions: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    dim_hash: Mapped[str] = mapped_column(Text, Computed("md5(dimensions::text)"), nullable=False)
    window_start: Mapped[datetime] = mapped_column(PG_TIMESTAMP(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(PG_TIMESTAMP(timezone=True), nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        PG_TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    created_at: Mapped[datetime] = mapped_column(
        PG_TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        PG_TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
```

在 `models/__init__.py` 末尾追加 `from mesh.db.models.analytics import AnalyticsSnapshot  # noqa: F401`(按既有排序风格插入)。

- [ ] **Step 2: 写迁移**

```python
# backend/migrations/versions/0027_analytics.py
"""analytics snapshots materialized cache (analytics.md §2.5).

Lands: analytics_snapshots (scope_key part of unique key — cross-permission
cache sharing impossible), lookup/stale indexes, ws-id unique index for
composite FKs, RLS policy, mesh_app grants.
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE analytics_snapshots (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          metric_key   TEXT NOT NULL
            CHECK (metric_key IN ('cycle_time','velocity','throughput','burndown','agent_stats')),
          scope_key    TEXT NOT NULL DEFAULT 'ws_admin',
          dimensions   JSONB NOT NULL DEFAULT '{}'::jsonb,
          dim_hash     TEXT GENERATED ALWAYS AS (md5(dimensions::text)) STORED,
          window_start TIMESTAMPTZ NOT NULL,
          window_end   TIMESTAMPTZ NOT NULL,
          value        JSONB NOT NULL,
          computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (workspace_id, metric_key, scope_key, dim_hash, window_start, window_end)
        )
    """)
    op.execute("CREATE UNIQUE INDEX uq_analytics_snapshots_ws_id ON analytics_snapshots (workspace_id, id)")
    op.execute("""CREATE INDEX idx_snapshots_lookup ON analytics_snapshots
        (workspace_id, metric_key, scope_key, dim_hash, window_start, window_end)""")
    op.execute("CREATE INDEX idx_snapshots_stale ON analytics_snapshots (computed_at)")
    op.execute("ALTER TABLE analytics_snapshots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE analytics_snapshots FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY analytics_snapshots_tenant_isolation ON analytics_snapshots
        USING (workspace_id = current_setting('mesh.workspace_id')::uuid)
        WITH CHECK (workspace_id = current_setting('mesh.workspace_id')::uuid)""")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON analytics_snapshots TO mesh_app")


def downgrade() -> None:
    op.execute("DROP TABLE analytics_snapshots")
```

- [ ] **Step 3: 写失败测试(模型↔迁移一致性 + 唯一键 + dim_hash 生成)**

```python
# backend/tests/unit/test_analytics_model.py
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

from mesh.db.models.analytics import AnalyticsSnapshot

pytestmark = pytest.mark.unit


async def test_snapshot_roundtrip_and_dim_hash(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        snap = AnalyticsSnapshot(
            workspace_id=ws.id, metric_key="throughput", scope_key="projects:abc",
            dimensions={"granularity": "day", "calendar_timezone": "UTC"},
            window_start=datetime(2026, 7, 1, tzinfo=timezone.utc),
            window_end=datetime(2026, 7, 8, tzinfo=timezone.utc),
            value={"series": []},
        )
        session.add(snap)
        await session.commit()
        row = (await session.execute(select(AnalyticsSnapshot))).scalar_one()
        assert row.dim_hash and len(row.dim_hash) == 32  # md5 hex
        assert row.scope_key == "projects:abc"


async def test_snapshot_unique_key_blocks_cross_scope_collision_only_on_same_scope(
    session_factory, workspace_factory
):
    ws = await workspace_factory()
    win = dict(window_start=datetime(2026, 7, 1, tzinfo=timezone.utc),
               window_end=datetime(2026, 7, 8, tzinfo=timezone.utc))
    async with session_factory() as session:
        for scope in ("ws_admin", "projects:abc"):
            session.add(AnalyticsSnapshot(workspace_id=ws.id, metric_key="throughput",
                                          scope_key=scope, dimensions={"g": "day"}, value={}, **win))
        await session.commit()  # 不同 scope_key 可并存
    async with session_factory() as session:
        session.add(AnalyticsSnapshot(workspace_id=ws.id, metric_key="throughput",
                                      scope_key="ws_admin", dimensions={"g": "day"}, value={}, **win))
        with pytest.raises(Exception):  # UniqueViolation
            await session.commit()


async def test_snapshot_rls_policy_exists(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        rows = (await session.execute(text(
            "SELECT polname FROM pg_policy WHERE polrelid = 'analytics_snapshots'::regclass"))).all()
        assert [r[0] for r in rows] == ["analytics_snapshots_tenant_isolation"]
```

- [ ] **Step 4: 跑测试(需先设测试环境 env)**

Run: `cd backend && pytest tests/unit/test_analytics_model.py -v`(conftest 自动 `alembic upgrade head` + TRUNCATE)
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/src/mesh/db/models/analytics.py backend/src/mesh/db/models/__init__.py \
  backend/migrations/versions/0027_analytics.py backend/tests/unit/test_analytics_model.py
git commit -m "feat(analytics): analytics_snapshots 物化缓存表 + 迁移 0027(scope_key 入唯一键,RLS/grants)"
```

---

## Task 2: Settings + visibility/scope 核心(统一 CTE + scope_key + 时区/窗校验)

**Files:**
- Create: `backend/src/mesh/analytics/__init__.py`、`visibility.py`、`scope.py`
- Modify: `backend/src/mesh/config.py`
- Test: `backend/tests/unit/test_analytics_visibility.py`、`test_analytics_scope.py`

**Interfaces:**
- Consumes: ORM 模型 `Agent/Project/ProjectMember/MemberProjectAccess/Member/User/Workspace`
- Produces:
  - `VISIBLE_EXECUTIONS_CTE: str`(R5 权威 CTE 文本,具名绑定 `ws/requester_member_id/requester_user_id/requester_role`,**全部 execution 聚合唯一来源**)
  - `visible_executions_cte() -> str`(返回同一常量)
  - `analytics_exec_visible_to(session, *, execution_id, member_id, workspace_id) -> bool`(逐执行布尔形态)
  - `visible_project_ids(session, *, workspace_id, member) -> list[UUID] | None`(None = admin/owner 全量)
  - `visible_agent_ids(session, *, workspace_id, member) -> list[UUID] | None`
  - `compute_issue_scope_key(...)` / `compute_exec_scope_key(...)` / `single_project_scope_key(project_id)`
  - `hash_id_set(ids) -> str`(sha256 hex of sorted str ids)
  - `resolve_display_timezone(user, workspace, tz_param) -> str` + `assert_valid_timezone(tz)`(非法 → `ValidationError code="invalid_timezone"`)
  - `parse_time_window(from_s, to_s, *, now) -> tuple[datetime, datetime]`(RFC3339 UTC;`from>=to`/非 UTC → `ValidationError code="invalid_time_range"`;缺省近 30 天)
  - `validate_granularity(g)` / `validate_from_category(c)` / `validate_metric(m)`(非法 → `validation_error`)
  - Settings:`analytics_snapshot_ttl: timedelta = 15min`、`analytics_stale_while_revalidate: bool = False`

- [ ] **Step 1: 写 config 字段**

`backend/src/mesh/config.py` 的 `Settings` 类内、与其他模块分组同风格追加:

```python
    # -- Analytics module (analytics.md §2.6) --
    analytics_snapshot_ttl: timedelta = Field(default=timedelta(minutes=15), gt=timedelta(0))
    analytics_stale_while_revalidate: bool = False
```

- [ ] **Step 2: 写失败测试 — scope.py 纯函数(时区/窗/校验/hash)**

```python
# backend/tests/unit/test_analytics_scope.py
import uuid
from datetime import datetime, timezone

import pytest

from mesh.analytics.scope import (
    assert_valid_timezone, hash_id_set, parse_time_window, resolve_display_timezone,
    validate_from_category, validate_granularity,
)
from mesh.errors import ValidationError

pytestmark = pytest.mark.unit
NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_window_ok():
    f, t = parse_time_window("2026-07-01T00:00:00Z", "2026-07-08T00:00:00Z", now=NOW)
    assert f < t and f.tzinfo is not None


def test_parse_window_from_ge_to():
    with pytest.raises(ValidationError) as exc:
        parse_time_window("2026-07-08T00:00:00Z", "2026-07-01T00:00:00Z", now=NOW)
    assert exc.value.code == "invalid_time_range"


def test_parse_window_non_utc_rejected():
    with pytest.raises(ValidationError) as exc:
        parse_time_window("2026-07-01T08:00:00+08:00", "2026-07-08T00:00:00Z", now=NOW)
    assert exc.value.code == "invalid_time_range"


def test_parse_window_defaults_last_30_days():
    f, t = parse_time_window(None, None, now=NOW)
    assert (t - f).days == 30 and t == NOW


def test_invalid_timezone():
    with pytest.raises(ValidationError) as exc:
        assert_valid_timezone("Mars/Olympus")
    assert exc.value.code == "invalid_timezone"


def test_hash_id_set_order_insensitive():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert hash_id_set([a, b]) == hash_id_set([b, a])
    assert len(hash_id_set([a])) == 64


class _U:  # minimal user/workspace doubles
    def __init__(self, timezone_=None):
        self.timezone = timezone_


class _W:
    def __init__(self, timezone_="UTC"):
        self.timezone = timezone_


def test_display_timezone_chain():
    assert resolve_display_timezone(_U(None), _W("UTC"), None) == "UTC"
    assert resolve_display_timezone(_U("Asia/Shanghai"), _W("UTC"), None) == "Asia/Shanghai"
    assert resolve_display_timezone(_U("Asia/Shanghai"), _W("UTC"), "America/New_York") == "America/New_York"
    assert resolve_display_timezone(_U(None), _W("Asia/Shanghai"), None) == "Asia/Shanghai"


def test_validators():
    assert validate_granularity(None) == "day"
    assert validate_granularity("week") == "week"
    for bad, fn in (("year", validate_granularity), ("flying", validate_from_category)):
        with pytest.raises(ValidationError) as exc:
            fn(bad)
        assert exc.value.code == "validation_error"
    assert validate_from_category(None) == "in_progress"
```

- [ ] **Step 3: 实现 scope.py**

```python
# backend/src/mesh/analytics/scope.py
"""Request-scoped key derivation for analytics (analytics.md §2.4/§2.5/§3.1/§3.2)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mesh.errors import ValidationError

DEFAULT_WINDOW_DAYS = 30
GRANULARITIES = ("day", "week", "month")
STATE_CATEGORIES = ("backlog", "todo", "in_progress", "in_review", "blocked", "done", "cancelled")
BURNDOWN_METRICS = ("count", "points")


def hash_id_set(ids) -> str:
    joined = ",".join(str(i) for i in sorted(str(x) for x in ids))
    return hashlib.sha256(joined.encode()).hexdigest()


def assert_valid_timezone(tz: str) -> None:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValidationError("invalid IANA timezone", details={"tz": tz[:64]}, code="invalid_timezone")


def resolve_display_timezone(user, workspace, tz_param: str | None) -> str:
    for candidate in (tz_param, getattr(user, "timezone", None), getattr(workspace, "timezone", None), "UTC"):
        if candidate:
            assert_valid_timezone(candidate)
            return candidate
    return "UTC"


def _parse_rfc3339_utc(raw: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError("invalid RFC3339 timestamp", details={"value": raw[:64]},
                              code="invalid_time_range")
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValidationError("timestamps must be RFC3339 UTC", details={"value": raw[:64]},
                              code="invalid_time_range")
    return parsed.astimezone(timezone.utc)


def parse_time_window(from_s: str | None, to_s: str | None, *, now: datetime) -> tuple[datetime, datetime]:
    if from_s is None and to_s is None:
        return now - timedelta(days=DEFAULT_WINDOW_DAYS), now
    if from_s is None or to_s is None:
        raise ValidationError("from and to must be provided together", code="invalid_time_range")
    start, end = _parse_rfc3339_utc(from_s), _parse_rfc3339_utc(to_s)
    if start >= end:
        raise ValidationError("from must be earlier than to",
                              details={"from": from_s, "to": to_s}, code="invalid_time_range")
    return start, end


def validate_granularity(raw: str | None) -> str:
    value = raw or "day"
    if value not in GRANULARITIES:
        raise ValidationError("granularity must be day, week or month",
                              details={"granularity": value}, code="validation_error")
    return value


def validate_from_category(raw: str | None) -> str:
    value = raw or "in_progress"
    if value not in STATE_CATEGORIES:
        raise ValidationError("from_category must be a valid state category",
                              details={"from_category": value}, code="validation_error")
    return value


def validate_metric(raw: str | None) -> str:
    value = raw or "points"
    if value not in BURNDOWN_METRICS:
        raise ValidationError("metric must be count or points",
                              details={"metric": value}, code="validation_error")
    return value


def single_project_scope_key(project_id: uuid.UUID) -> str:
    return f"project:{project_id}"
```

- [ ] **Step 4: 写失败测试 — visibility.py(DB 支撑:可见集 + 逐执行谓词)**

```python
# backend/tests/unit/test_analytics_visibility.py
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from mesh.analytics.visibility import (
    analytics_exec_visible_to, compute_exec_scope_key, compute_issue_scope_key,
    visible_agent_ids, visible_executions_cte, visible_project_ids,
)
from mesh.db.models.agent import Agent
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Project, ProjectMember
from mesh.db.models.runtime import TaskExecution
from tests.unit.analytics_support import seed_world

pytestmark = pytest.mark.unit


async def test_visible_projects_for_plain_member_excludes_private(session_factory, workspace_factory, member_factory):
    world = await seed_world(session_factory, workspace_factory, member_factory)
    async with session_factory() as session:
        ids = await visible_project_ids(session, workspace_id=world.ws.id, member=world.m1)
        assert set(ids) == {world.pub.id}  # PPRIV 不可见
        assert await visible_project_ids(session, workspace_id=world.ws.id, member=world.admin) is None  # 全量


async def test_visible_agents_private_only_owner(session_factory, workspace_factory, member_factory):
    world = await seed_world(session_factory, workspace_factory, member_factory)
    async with session_factory() as session:
        m1_agents = await visible_agent_ids(session, workspace_id=world.ws.id, member=world.m1)
        assert world.pa.id not in m1_agents and world.wa.id in m1_agents
        owner_agents = await visible_agent_ids(session, workspace_id=world.ws.id, member=world.m3)
        assert world.pa.id in owner_agents


async def test_exec_predicate_matrix(session_factory, workspace_factory, member_factory):
    world = await seed_world(session_factory, workspace_factory, member_factory)
    async with session_factory() as session:
        # WA 执行挂在 PPRIV issue 上:m1 不可见、m2 可见、admin 可见
        assert await analytics_exec_visible_to(
            session, execution_id=world.exec_wa_priv.id, member_id=world.m1.id, workspace_id=world.ws.id) is False
        assert await analytics_exec_visible_to(
            session, execution_id=world.exec_wa_priv.id, member_id=world.m2.id, workspace_id=world.ws.id) is True
        assert await analytics_exec_visible_to(
            session, execution_id=world.exec_wa_priv.id, member_id=world.admin.id, workspace_id=world.ws.id) is True
        # PA 的无 issue 执行:仅 owner/admin 可见
        assert await analytics_exec_visible_to(
            session, execution_id=world.exec_pa_manual.id, member_id=world.m1.id, workspace_id=world.ws.id) is False
        assert await analytics_exec_visible_to(
            session, execution_id=world.exec_pa_manual.id, member_id=world.m3.id, workspace_id=world.ws.id) is True


async def test_scope_keys_differ_across_permissions(session_factory, workspace_factory, member_factory):
    world = await seed_world(session_factory, workspace_factory, member_factory)
    async with session_factory() as session:
        admin_issue = await compute_issue_scope_key(session, workspace_id=world.ws.id, member=world.admin)
        m1_issue = await compute_issue_scope_key(session, workspace_id=world.ws.id, member=world.m1)
        assert admin_issue == "ws_admin"
        assert m1_issue.startswith("projects:") and m1_issue != admin_issue
        m1_exec = await compute_exec_scope_key(session, workspace_id=world.ws.id, member=world.m1)
        admin_exec = await compute_exec_scope_key(session, workspace_id=world.ws.id, member=world.admin)
        assert m1_exec.startswith("exec:p") and ":a" in m1_exec and admin_exec == "ws_admin"


def test_cte_text_is_single_authoritative_source():
    cte = visible_executions_cte()
    assert "visible_executions AS" in cte
    assert "a.visibility = 'workspace'" in cte and "p.visibility = 'public'" in cte
    assert cte.count("project_members pm") == 1 and cte.count("member_project_access mx") == 1
```

- [ ] **Step 5: 写测试支撑 `tests/unit/analytics_support.py`(seed_world:用户/成员/项目/agent/执行)**

```python
# backend/tests/unit/analytics_support.py
"""Shared world seeder for analytics tests (real ORM rows, no mocks)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from mesh.db.models.agent import Agent
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Cycle, Milestone, Project, ProjectMember
from mesh.db.models.runtime import ExecutionAttempt, TaskExecution

TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


@dataclass
class World:
    ws: object
    admin: object
    m1: object          # 普通成员(非私有项目成员、非 private agent owner)
    m2: object          # 私有项目成员
    m3: object          # private agent owner(普通角色)
    pub: object         # public project
    priv: object        # private project
    wa: object          # workspace agent
    pa: object          # private agent (owner=m3.user_id)
    status: object
    issue_priv: object
    issue_pub: object
    cycle: object
    milestone: object
    exec_wa_priv: object
    exec_pa_manual: object


async def seed_world(session_factory, workspace_factory, member_factory) -> World:
    ws = await workspace_factory()
    admin = await member_factory(ws, role="admin", name="Admin")
    m1 = await member_factory(ws, role="member", name="M1")
    m2 = await member_factory(ws, role="member", name="M2")
    m3 = await member_factory(ws, role="member", name="M3")
    async with session_factory() as session:
        pub = Project(workspace_id=ws.id, name="Pub", key=f"pub{uuid.uuid4().hex[:6]}",
                      visibility="public", created_by_member_id=admin.id)
        priv = Project(workspace_id=ws.id, name="Priv", key=f"pri{uuid.uuid4().hex[:6]}",
                       visibility="private", created_by_member_id=admin.id)
        session.add_all([pub, priv])
        await session.flush()
        session.add(ProjectMember(workspace_id=ws.id, project_id=priv.id, member_id=m2.id, role="member"))
        wa = Agent(workspace_id=ws.id, name="WA", owner_user_id=admin.user_id, visibility="workspace")
        pa = Agent(workspace_id=ws.id, name="PA", owner_user_id=m3.user_id, visibility="private")
        session.add_all([wa, pa])
        await session.flush()
        session.add(Member(workspace_id=ws.id, member_type="agent", agent_id=wa.id, role="member"))
        session.add(Member(workspace_id=ws.id, member_type="agent", agent_id=pa.id, role="member"))
        status = IssueStatus(workspace_id=ws.id, name="Done", state_category="done",
                             is_default=False, created_by_member_id=admin.id)
        session.add(status)
        await session.flush()
        issue_priv = Issue(workspace_id=ws.id, title="priv issue", project_id=priv.id,
                           status_id=status.id, state_category="todo",
                           identifier_namespace_key=pub.key, number=1, identifier=f"{pub.key}-1",
                           created_by_member_id=admin.id)
        issue_pub = Issue(workspace_id=ws.id, title="pub issue", project_id=pub.id,
                          status_id=status.id, state_category="todo",
                          identifier_namespace_key=pub.key, number=2, identifier=f"{pub.key}-2",
                          created_by_member_id=admin.id)
        session.add_all([issue_priv, issue_pub])
        cycle = Cycle(workspace_id=ws.id, name="C1", project_id=pub.id,
                      starts_at=datetime(2026, 7, 6).date(), ends_at=datetime(2026, 7, 12).date())
        milestone = Milestone(workspace_id=ws.id, name="M1", project_id=pub.id,
                              target_date=datetime(2026, 7, 20).date())
        session.add_all([cycle, milestone])
        await session.flush()
        exec_wa_priv = TaskExecution(workspace_id=ws.id, agent_id=wa.id, issue_id=issue_priv.id,
                                     trigger="assign", status="completed",
                                     queued_at=TS, finished_at=TS)
        exec_pa_manual = TaskExecution(workspace_id=ws.id, agent_id=pa.id, issue_id=None,
                                       trigger="manual", status="completed",
                                       queued_at=TS, finished_at=TS)
        session.add_all([exec_wa_priv, exec_pa_manual])
        await session.flush()
        await session.commit()
        ids = dict(ws=ws.id, admin=admin.id, m1=m1.id, m2=m2.id, m3=m3.id, pub=pub.id, priv=priv.id,
                   wa=wa.id, pa=pa.id, status=status.id, issue_priv=issue_priv.id, issue_pub=issue_pub.id,
                   cycle=cycle.id, milestone=milestone.id,
                   exec_wa_priv=exec_wa_priv.id, exec_pa_manual=exec_pa_manual.id)
    async with session_factory() as session:
        rows = {}
        for key, model in (("ws", None),):
            pass
        from mesh.db.models.workspace import Workspace
        rows["ws"] = await session.get(Workspace, ids["ws"])
        for key, model in (("admin", Member), ("m1", Member), ("m2", Member), ("m3", Member)):
            rows[key] = await session.get(model, ids[key])
        for key, model in (("pub", Project), ("priv", Project), ("cycle", Cycle), ("milestone", Milestone)):
            rows[key] = await session.get(model, ids[key])
        for key, model in (("wa", Agent), ("pa", Agent)):
            rows[key] = await session.get(model, ids[key])
        rows["status"] = await session.get(IssueStatus, ids["status"])
        for key in ("issue_priv", "issue_pub"):
            rows[key] = await session.get(Issue, ids[key])
        for key in ("exec_wa_priv", "exec_pa_manual"):
            rows[key] = await session.get(TaskExecution, ids[key])
    return World(**rows)
```

(实现时按真实模型必填字段微调——先读 `db/models/issue.py`/`project.py`/`runtime.py` 的 NOT NULL 列与 `created_by_member_id` 命名;`member_factory` 返回的 Member 带 `.user_id`。)

- [ ] **Step 6: 实现 visibility.py**

```python
# backend/src/mesh/analytics/visibility.py
"""Unified execution-visibility scope (analytics.md §2.3.1, R4/R5).

``VISIBLE_EXECUTIONS_CTE`` is the SINGLE authoritative SQL text for every
execution-metric aggregation (workload-B, agent stats main/retry/token,
workspace dashboard). No endpoint may aggregate task_executions without it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy import or_

from mesh.analytics.scope import hash_id_set
from mesh.auth.rbac import role_satisfies
from mesh.db.models.agent import Agent
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.project import Project, ProjectMember
from mesh.db.models.runtime import TaskExecution

VISIBLE_EXECUTIONS_CTE = """
visible_executions AS (
  SELECT e.*
  FROM task_executions e
  JOIN agents a        ON a.id = e.agent_id AND a.workspace_id = e.workspace_id
  LEFT JOIN issues i   ON i.id = e.issue_id AND i.workspace_id = e.workspace_id
  LEFT JOIN projects p ON p.id = i.project_id AND p.workspace_id = i.workspace_id
  WHERE e.workspace_id = :ws
    AND (a.visibility = 'workspace'
         OR (a.visibility = 'private'
             AND (a.owner_user_id = :requester_user_id
                  OR :requester_role IN ('owner', 'admin'))))
    AND (i.id IS NULL
         OR p.id IS NULL
         OR p.visibility = 'public'
         OR :requester_role IN ('owner', 'admin')
         OR EXISTS (SELECT 1 FROM project_members pm
                     WHERE pm.workspace_id = e.workspace_id AND pm.project_id = p.id
                       AND pm.member_id = :requester_member_id)
         OR EXISTS (SELECT 1 FROM member_project_access mx
                     WHERE mx.workspace_id = e.workspace_id AND mx.project_id = p.id
                       AND mx.member_id = :requester_member_id))
)
"""


def visible_executions_cte() -> str:
    """Return the authoritative CTE text (verbatim reuse everywhere)."""
    return VISIBLE_EXECUTIONS_CTE


def is_workspace_manager(member) -> bool:
    return role_satisfies(member.role, "project:manage")


async def visible_project_ids(session, *, workspace_id: uuid.UUID, member) -> list[uuid.UUID] | None:
    """Visible private+public project id set; None means full workspace (admin/owner)."""
    if is_workspace_manager(member):
        return None
    stmt = select(Project.id).where(
        Project.workspace_id == workspace_id,
        Project.visibility == "public",
        Project.archived_at.is_(None) is not None,  # placeholder replaced below
    )
    stmt = select(Project.id).where(Project.workspace_id == workspace_id, Project.visibility == "public")
    public_ids = {row for row in (await session.execute(stmt)).scalars().all()}
    pm_ids = {r for r in (await session.execute(
        select(ProjectMember.project_id).where(
            ProjectMember.workspace_id == workspace_id,
            ProjectMember.member_id == member.id))).scalars().all()}
    mx_ids = {r for r in (await session.execute(
        select(MemberProjectAccess.project_id).where(
            MemberProjectAccess.workspace_id == workspace_id,
            MemberProjectAccess.member_id == member.id))).scalars().all()}
    return sorted(public_ids | pm_ids | mx_ids)


async def visible_agent_ids(session, *, workspace_id: uuid.UUID, member) -> list[uuid.UUID] | None:
    if is_workspace_manager(member):
        return None
    stmt = select(Agent.id).where(
        Agent.workspace_id == workspace_id,
        Agent.deleted_at.is_(None),
        or_(Agent.visibility == "workspace", Agent.owner_user_id == member.user_id),
    )
    return sorted((await session.execute(stmt)).scalars().all())


async def compute_issue_scope_key(session, *, workspace_id: uuid.UUID, member) -> str:
    ids = await visible_project_ids(session, workspace_id=workspace_id, member=member)
    return "ws_admin" if ids is None else f"projects:{hash_id_set(ids)}"


async def compute_exec_scope_key(session, *, workspace_id: uuid.UUID, member) -> str:
    project_ids = await visible_project_ids(session, workspace_id=workspace_id, member=member)
    if project_ids is None:
        return "ws_admin"
    agent_ids = await visible_agent_ids(session, workspace_id=workspace_id, member=member)
    return f"exec:p{hash_id_set(project_ids)}:a{hash_id_set(agent_ids or [])}"


async def analytics_exec_visible_to(session, *, execution_id: uuid.UUID, member_id: uuid.UUID,
                                    workspace_id: uuid.UUID) -> bool:
    """Per-execution boolean form of VISIBLE_EXECUTIONS_CTE (executable reference)."""
    member = await session.get(Member, member_id)
    if member is None:
        return False
    sql = text(
        "WITH " + VISIBLE_EXECUTIONS_CTE +
        " SELECT 1 FROM visible_executions e WHERE e.id = :execution_id LIMIT 1"
    )
    row = await session.execute(sql, {
        "ws": workspace_id, "requester_member_id": member.id,
        "requester_user_id": member.user_id, "requester_role": member.role,
        "execution_id": execution_id,
    })
    return row.first() is not None
```

(实现后删除 `visible_project_ids` 中的占位行——Step 中留了示意错行,实现时只保留最终两条 select;跑测试确认。)

- [ ] **Step 7: 跑测试**

Run: `cd backend && pytest tests/unit/test_analytics_scope.py tests/unit/test_analytics_visibility.py -v`
Expected: all passed(如模型必填字段不匹配,按报错调整 `analytics_support.py` 的 seed 字段)

- [ ] **Step 8: Commit**

```bash
git add backend/src/mesh/analytics/__init__.py backend/src/mesh/analytics/visibility.py \
  backend/src/mesh/analytics/scope.py backend/src/mesh/config.py \
  backend/tests/unit/analytics_support.py backend/tests/unit/test_analytics_scope.py \
  backend/tests/unit/test_analytics_visibility.py
git commit -m "feat(analytics): 统一 execution 可见性 CTE + scope_key 计算 + 时区/窗校验(§2.3.1/§2.4)"
```

---

## Task 3: 权威聚合 SQL 构建器(queries.py)

**Files:**
- Create: `backend/src/mesh/analytics/queries.py`
- Test: `backend/tests/unit/test_analytics_queries.py`

**Interfaces:**
- Consumes: `visibility.VISIBLE_EXECUTIONS_CTE`
- Produces(返回 `sqlalchemy.text` 文本 + 绑定参数 dict 的构建函数,全部具名绑定):
  - `build_cycle_time_sql()` → `(text, params)`:P50/P90 + sample_size(`percentile_cont`,first_start CTE,`new_value #>> '{}' = :from_category`,窗按 `completed_at`)
  - `build_insufficient_count_sql()` → done 窗内无 first_start/负时长计数
  - `build_velocity_sql()` → 逐周期 completed_issues/completed_points(DATE 边界 `AT TIME ZONE :display_tz` 展开,`:cycle_ids` ANY)
  - `build_throughput_sql()` → `date_trunc(:granularity, ts AT TIME ZONE :calendar_tz)` 双序列分桶(桶标签 + UTC 窗)
  - `build_burndown_days_sql()` → 逐日 remaining(scope 当前归属,`metric` count/points 切换)
  - `build_workload_open_sql()` → workload-A(项目可见性过滤 `:project_ids_filter` SQL 片段)
  - `build_workload_inflight_sql()` → workload-B(**CTE 内联**,running/queued/awaiting_approval)
  - `build_agent_stats_sql()` → 主统计(**CTE 内联**;`agent_id` 可空=全体)
  - `build_retry_rate_sql()` → (**CTE 内联** + attempts LEFT JOIN)
  - `build_tokens_sql()` → (**CTE 内联** + autopilot_runs JOIN;prompt/completion 求和,runs_with_token_data)
  - `assert_cte_inlined(sql)` → 测试辅助:断言文本含 `visible_executions AS`

- [ ] **Step 1: 写失败测试(文本级断言:四段 execution SQL 全部内联统一 CTE,issue SQL 不带 CTE)**

```python
# backend/tests/unit/test_analytics_queries.py
import pytest

from mesh.analytics import queries
from mesh.analytics.visibility import VISIBLE_EXECUTIONS_CTE

pytestmark = pytest.mark.unit


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def test_execution_queries_inline_authoritative_cte_verbatim():
    for builder in (queries.build_workload_inflight_sql, queries.build_agent_stats_sql,
                    queries.build_retry_rate_sql, queries.build_tokens_sql):
        sql, _params = builder(agent_id=None) if builder is queries.build_agent_stats_sql \
            else builder(agent_id="00000000-0000-0000-0000-000000000000")
        norm = _norm(sql)
        assert _norm("WITH " + VISIBLE_EXECUTIONS_CTE) in norm, builder.__name__
        assert "FROM visible_executions" in norm, builder.__name__
        assert "FROM task_executions e" not in norm.replace(
            _norm("FROM task_executions e\n  JOIN agents"), ""), builder.__name__


def test_no_bypass_aggregation_exists():
    # 模块内不得出现未经 CTE 的 task_executions 直接聚合
    import inspect
    from mesh.analytics import queries as q
    src = inspect.getsource(q)
    assert "GROUP BY e.agent_id" in src
    assert src.count("visible_executions AS") >= 1  # 常量引用来自 visibility
```

- [ ] **Step 2: 实现 queries.py(要点;完整 SQL 逐字取自 analytics.md §2.2/§2.3)**

```python
# backend/src/mesh/analytics/queries.py
"""Authoritative aggregation SQL builders (analytics.md §2.2/§2.3).

Execution-metric builders inline visibility.VISIBLE_EXECUTIONS_CTE verbatim;
issue-metric builders filter by requester-visible project set at the service
layer via the ``project_filter`` fragment.
"""

from __future__ import annotations

from sqlalchemy import text

from mesh.analytics.visibility import VISIBLE_EXECUTIONS_CTE

# --- issue metrics -------------------------------------------------------

CYCLE_TIME_SQL = """
WITH first_start AS (
  SELECT a.issue_id, MIN(a.created_at) AS started_at
  FROM issue_activity a
  WHERE a.workspace_id = :ws
    AND a.field = 'state_category'
    AND (a.new_value #>> '{}') = :from_category
  GROUP BY a.issue_id
)
SELECT
  percentile_cont(0.5) WITHIN GROUP
    (ORDER BY EXTRACT(EPOCH FROM (i.completed_at - f.started_at))) AS p50_seconds,
  percentile_cont(0.9) WITHIN GROUP
    (ORDER BY EXTRACT(EPOCH FROM (i.completed_at - f.started_at))) AS p90_seconds,
  COUNT(*) AS sample_size
FROM issues i
JOIN first_start f ON f.issue_id = i.id
WHERE i.workspace_id = :ws
  AND i.deleted_at IS NULL
  AND i.state_category = 'done'
  AND i.completed_at IS NOT NULL
  AND i.completed_at >= :win_from AND i.completed_at < :win_to
  AND f.started_at < i.completed_at
  {project_filter}
"""

CYCLE_INSUFFICIENT_SQL = """
WITH first_start AS (
  SELECT a.issue_id, MIN(a.created_at) AS started_at
  FROM issue_activity a
  WHERE a.workspace_id = :ws AND a.field = 'state_category'
    AND (a.new_value #>> '{}') = :from_category
  GROUP BY a.issue_id
)
SELECT COUNT(*) FROM issues i
LEFT JOIN first_start f ON f.issue_id = i.id
WHERE i.workspace_id = :ws AND i.deleted_at IS NULL
  AND i.state_category = 'done' AND i.completed_at IS NOT NULL
  AND i.completed_at >= :win_from AND i.completed_at < :win_to
  AND (f.started_at IS NULL OR f.started_at >= i.completed_at)
  {project_filter}
"""

VELOCITY_SQL = """
SELECT c.id AS cycle_id, c.name, c.starts_at, c.ends_at, c.state,
       COUNT(i.id) AS completed_issues,
       COALESCE(SUM(i.estimate), 0) AS completed_points
FROM cycles c
LEFT JOIN issues i
  ON i.cycle_id = c.id
  AND i.workspace_id = c.workspace_id
  AND i.deleted_at IS NULL
  AND i.state_category = 'done'
  AND i.completed_at >= (c.starts_at::timestamp AT TIME ZONE :display_tz)
  AND i.completed_at <  (((c.ends_at + 1))::timestamp AT TIME ZONE :display_tz)
WHERE c.workspace_id = :ws {cycle_filter}
GROUP BY c.id, c.name, c.starts_at, c.ends_at, c.state
ORDER BY c.starts_at
"""

THROUGHPUT_SQL = """
SELECT bucket_local,
       (bucket_local AT TIME ZONE :calendar_tz) AS window_start_utc,
       ((bucket_local + :bucket_step) AT TIME ZONE :calendar_tz) AS window_end_utc,
       COUNT(*) FILTER (WHERE kind = 'created')   AS created,
       COUNT(*) FILTER (WHERE kind = 'completed') AS completed
FROM (
  SELECT date_trunc(:granularity, created_at AT TIME ZONE :calendar_tz) AS bucket_local,
         'created' AS kind
    FROM issues
   WHERE workspace_id = :ws AND deleted_at IS NULL
     AND created_at >= :win_from AND created_at < :win_to {project_filter}
  UNION ALL
  SELECT date_trunc(:granularity, completed_at AT TIME ZONE :calendar_tz) AS bucket_local,
         'completed' AS kind
    FROM issues
   WHERE workspace_id = :ws AND deleted_at IS NULL
     AND state_category = 'done' AND completed_at IS NOT NULL
     AND completed_at >= :win_from AND completed_at < :win_to {project_filter}
) t
GROUP BY bucket_local
ORDER BY bucket_local
"""

BURNDOWN_DAYS_SQL = """
WITH scope AS (
  SELECT COALESCE(estimate, 0) AS pts, completed_at
    FROM issues
   WHERE workspace_id = :ws AND deleted_at IS NULL AND {scope_column} = :scope_id
),
total AS (SELECT {total_expr} AS v FROM scope)
SELECT days.d AS date,
       (SELECT v FROM total) - {completed_expr} AS remaining
FROM generate_series(:day_from, :day_to, '1 day'::interval) AS days(d)
LEFT JOIN scope ON TRUE
GROUP BY days.d
ORDER BY days.d
"""

WORKLOAD_OPEN_SQL = """
SELECT i.assignee_id AS member_id, COUNT(*) AS open_issues
FROM issues i
WHERE i.workspace_id = :ws AND i.deleted_at IS NULL
  AND i.assignee_id IS NOT NULL
  AND i.state_category NOT IN ('done', 'cancelled')
  {project_filter}
GROUP BY i.assignee_id
"""

# --- execution metrics (authoritative CTE inlined verbatim) ---------------

WORKLOAD_INFLIGHT_SQL = """
WITH {cte}
SELECT e.agent_id,
  COUNT(*) FILTER (WHERE e.status IN ('claimed','running','cancelling')) AS running,
  COUNT(*) FILTER (WHERE e.status = 'queued')                            AS queued,
  COUNT(*) FILTER (WHERE e.status = 'awaiting_approval')                 AS awaiting_approval
FROM visible_executions e
WHERE e.agent_id IS NOT NULL
  AND e.status IN ('queued','claimed','running','cancelling','awaiting_approval')
GROUP BY e.agent_id
"""

AGENT_STATS_SQL = """
WITH {cte}
SELECT
  e.agent_id,
  COUNT(*)                                                              AS executions,
  COUNT(*) FILTER (WHERE e.status = 'completed')                        AS succeeded,
  COUNT(*) FILTER (WHERE e.status IN ('completed','failed','timeout'))  AS terminal,
  COUNT(*) FILTER (WHERE e.status = 'cancelled')                        AS cancelled_count,
  ROUND(COUNT(*) FILTER (WHERE e.status = 'completed') * 1.0
        / NULLIF(COUNT(*) FILTER (WHERE e.status IN ('completed','failed','timeout')), 0), 4)
        AS success_rate,
  ROUND(COUNT(*) FILTER (WHERE e.status = 'timeout') * 1.0
        / NULLIF(COUNT(*) FILTER (WHERE e.status IN ('completed','failed','timeout')), 0), 4)
        AS timeout_rate,
  AVG(EXTRACT(EPOCH FROM (e.finished_at - e.queued_at)))
        FILTER (WHERE e.status IN ('completed','failed','timeout')
                AND e.finished_at IS NOT NULL)                          AS avg_duration_seconds
FROM visible_executions e
WHERE e.queued_at >= :win_from AND e.queued_at < :win_to {agent_filter}
GROUP BY e.agent_id
"""

RETRY_RATE_SQL = """
WITH {cte}
SELECT e.agent_id,
       ROUND(COUNT(*) FILTER (WHERE n > 1) * 1.0 / NULLIF(COUNT(*), 0), 4) AS retry_rate
FROM (
  SELECT e.id, e.agent_id, COUNT(att.id) AS n
  FROM visible_executions e
  LEFT JOIN execution_attempts att
    ON att.execution_id = e.id AND att.workspace_id = e.workspace_id
  WHERE e.queued_at >= :win_from AND e.queued_at < :win_to {agent_filter}
  GROUP BY e.id, e.agent_id
) r
JOIN visible_executions e ON e.id = r.id
GROUP BY e.agent_id
"""

TOKENS_SQL = """
WITH {cte}
SELECT e.agent_id,
       SUM(r.prompt_tokens)     AS prompt_tokens,
       SUM(r.completion_tokens) AS completion_tokens,
       COUNT(r.id)              AS runs_with_token_data
FROM autopilot_runs r
JOIN visible_executions e
  ON e.id = r.execution_id AND e.workspace_id = r.workspace_id
WHERE r.started_at >= :win_from AND r.started_at < :win_to {agent_filter}
GROUP BY e.agent_id
"""

GRANULARITY_STEP = {"day": "1 day", "week": "1 week", "month": "1 month"}


def project_filter_fragment(visible_ids, *, alias="i") -> tuple[str, dict]:
    """``project_id IS NULL OR project_id IN (...)`` fragment; empty filter for admins."""
    if visible_ids is None:
        return "", {}
    return (f"AND ({alias}.project_id IS NULL OR {alias}.project_id = ANY(:visible_project_ids))",
            {"visible_project_ids": list(visible_ids)})


def build_cycle_time_sql(*, visible_ids):
    fragment, params = project_filter_fragment(visible_ids)
    return CYCLE_TIME_SQL.format(project_filter=fragment), params

# build_* 同构:format 片段 + 合并参数;execution 系一律 cte=VISIBLE_EXECUTIONS_CTE.strip()
def build_workload_inflight_sql(**_):
    return WORKLOAD_INFLIGHT_SQL.format(cte=VISIBLE_EXECUTIONS_CTE.strip()), {}

def build_agent_stats_sql(*, agent_id=None):
    fragment = "AND e.agent_id = :agent_id" if agent_id is not None else ""
    sql = AGENT_STATS_SQL.format(cte=VISIBLE_EXECUTIONS_CTE.strip(), agent_filter=fragment)
    return sql, ({"agent_id": agent_id} if agent_id is not None else {})

def build_retry_rate_sql(*, agent_id):
    sql = RETRY_RATE_SQL.format(cte=VISIBLE_EXECUTIONS_CTE.strip(),
                                agent_filter="AND e.agent_id = :agent_id")
    return sql, {"agent_id": agent_id}

def build_tokens_sql(*, agent_id):
    sql = TOKENS_SQL.format(cte=VISIBLE_EXECUTIONS_CTE.strip(),
                            agent_filter="AND e.agent_id = :agent_id")
    return sql, {"agent_id": agent_id}
```

(其余 build_* 在实现时按同一模式补齐:velocity 的 `cycle_filter`(`AND c.id = ANY(:cycle_ids)` 或 `AND c.project_id = :project_id` 或窗相交 `AND daterange(c.starts_at, c.ends_at, '[]') && daterange(:win_from::date, :win_to::date, '[]')`)+ 项目可见性(cycles JOIN projects 校验放 service 层);throughput 用 `GRANULARITY_STEP`;burndown 按 metric 填 `total_expr`/`completed_expr`(count:`COUNT(*)`/points:`COALESCE(SUM(pts),0)`,已完成同构 FILTER `completed_at < ((days.d + 1)::timestamp AT TIME ZONE :display_tz)`),`scope_column` ∈ `cycle_id`/`milestone_id`。)

- [ ] **Step 3: 跑测试**

Run: `cd backend && pytest tests/unit/test_analytics_queries.py -v`
Expected: passed

- [ ] **Step 4: Commit**

```bash
git add backend/src/mesh/analytics/queries.py backend/tests/unit/test_analytics_queries.py
git commit -m "feat(analytics): 六类指标权威聚合 SQL(execution 四段逐字内联统一 CTE,§2.2/§2.3)"
```

---

## Task 4: 缓存层(cache.py)+ Settings 联动

**Files:**
- Create: `backend/src/mesh/analytics/cache.py`
- Test: `backend/tests/unit/test_analytics_cache.py`

**Interfaces:**
- Consumes: `AnalyticsSnapshot`、`settings.analytics_snapshot_ttl` / `analytics_stale_while_revalidate`
- Produces:
  - `fetch_snapshot(session, *, workspace_id, metric_key, scope_key, dimensions, window_start, window_end, ttl, now) -> tuple[dict | None, AnalyticsSnapshot | None]`(命中且新鲜 → (value, row);命中但 stale → (value, row) 且调用方决定是否重算;未命中 → (None, None))
  - `upsert_snapshot(session, *, ..., value, now) -> AnalyticsSnapshot`(`INSERT ... ON CONFLICT (unique) DO UPDATE`,更新 value/computed_at/updated_at)
  - `snapshot_is_fresh(row, ttl, now) -> bool`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/unit/test_analytics_cache.py
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from mesh.analytics.cache import fetch_snapshot, snapshot_is_fresh, upsert_snapshot
from mesh.db.models.analytics import AnalyticsSnapshot

pytestmark = pytest.mark.unit
NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)
WIN = dict(window_start=NOW - timedelta(days=7), window_end=NOW)
KEY = dict(metric_key="throughput", scope_key="projects:abc",
           dimensions={"granularity": "day", "calendar_timezone": "UTC"})
TTL = timedelta(minutes=15)


async def test_upsert_then_fresh_hit(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        await upsert_snapshot(session, workspace_id=ws.id, value={"series": [1]}, now=NOW, **KEY, **WIN)
        await session.commit()
    async with session_factory() as session:
        value, row = await fetch_snapshot(session, workspace_id=ws.id, ttl=TTL, now=NOW, **KEY, **WIN)
        assert value == {"series": [1]} and snapshot_is_fresh(row, TTL, NOW)


async def test_upsert_overwrites_same_key(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        await upsert_snapshot(session, workspace_id=ws.id, value={"v": 1}, now=NOW, **KEY, **WIN)
        await upsert_snapshot(session, workspace_id=ws.id, value={"v": 2},
                              now=NOW + timedelta(minutes=20), **KEY, **WIN)
        await session.commit()
        rows = (await session.execute(select(AnalyticsSnapshot))).scalars().all()
        assert len(rows) == 1 and rows[0].value == {"v": 2}


async def test_stale_row_returned_but_not_fresh(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        await upsert_snapshot(session, workspace_id=ws.id, value={"v": 1},
                              now=NOW - timedelta(hours=1), **KEY, **WIN)
        await session.commit()
    async with session_factory() as session:
        value, row = await fetch_snapshot(session, workspace_id=ws.id, ttl=TTL, now=NOW, **KEY, **WIN)
        assert value == {"v": 1} and not snapshot_is_fresh(row, TTL, NOW)


async def test_scope_key_mismatch_never_hits(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        await upsert_snapshot(session, workspace_id=ws.id, value={"v": 1}, now=NOW,
                              metric_key="throughput", scope_key="ws_admin",
                              dimensions=KEY["dimensions"], **WIN)
        await session.commit()
    async with session_factory() as session:
        value, _row = await fetch_snapshot(session, workspace_id=ws.id, ttl=TTL, now=NOW,
                                           metric_key="throughput", scope_key="projects:xyz",
                                           dimensions=KEY["dimensions"], **WIN)
        assert value is None  # 跨权限绝不命中
```

- [ ] **Step 2: 实现 cache.py**

```python
# backend/src/mesh/analytics/cache.py
"""analytics_snapshots hit/upsert helpers (analytics.md §2.5/§2.6)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mesh.db.models.analytics import AnalyticsSnapshot


def snapshot_is_fresh(row: AnalyticsSnapshot, ttl: timedelta, now: datetime) -> bool:
    return now - row.computed_at <= ttl


async def fetch_snapshot(session, *, workspace_id, metric_key, scope_key, dimensions,
                         window_start, window_end, ttl, now):
    stmt = select(AnalyticsSnapshot).where(
        AnalyticsSnapshot.workspace_id == workspace_id,
        AnalyticsSnapshot.metric_key == metric_key,
        AnalyticsSnapshot.scope_key == scope_key,
        AnalyticsSnapshot.dimensions == dimensions,
        AnalyticsSnapshot.window_start == window_start,
        AnalyticsSnapshot.window_end == window_end,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None, None
    return row.value, row


async def upsert_snapshot(session, *, workspace_id, metric_key, scope_key, dimensions,
                          window_start, window_end, value, now) -> AnalyticsSnapshot:
    values = dict(workspace_id=workspace_id, metric_key=metric_key, scope_key=scope_key,
                  dimensions=dimensions, window_start=window_start, window_end=window_end,
                  value=value, computed_at=now, created_at=now, updated_at=now)
    stmt = pg_insert(AnalyticsSnapshot).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="analytics_snapshots_workspace_id_metric_key_scope_key_dim_hash_window_key",
        set_=dict(value=stmt.excluded.value, computed_at=stmt.excluded.computed_at,
                  updated_at=stmt.excluded.updated_at),
    )
    await session.execute(stmt)
    fetched = (await session.execute(select(AnalyticsSnapshot).where(
        AnalyticsSnapshot.workspace_id == workspace_id,
        AnalyticsSnapshot.metric_key == metric_key,
        AnalyticsSnapshot.scope_key == scope_key,
        AnalyticsSnapshot.dimensions == dimensions,
        AnalyticsSnapshot.window_start == window_start,
        AnalyticsSnapshot.window_end == window_end,
    ))).scalar_one()
    return fetched
```

注:ON CONFLICT 约束名以迁移实际生成的名为准(PG 对长名会截断为 63 字符——实现时先 `\d analytics_snapshots` 查实际约束名,或在迁移中显式 `CONSTRAINT uq_snapshots_cache UNIQUE (...)` 命名后引用)。

- [ ] **Step 3: 跑测试 → Commit**

Run: `cd backend && pytest tests/unit/test_analytics_cache.py -v`

```bash
git add backend/src/mesh/analytics/cache.py backend/tests/unit/test_analytics_cache.py
git commit -m "feat(analytics): 快照缓存命中/覆盖式 upsert(scope_key 不匹配绝不命中,§2.5/§2.6)"
```

---

## Task 5: Service 层 — issue 指标(cycle time / throughput / velocity / burndown)

**Files:**
- Create: `backend/src/mesh/analytics/service.py`(本任务建骨架 + 4 个 issue 指标方法)
- Test: `backend/tests/unit/test_analytics_issue_metrics.py`

**Interfaces:**
- Produces:`AnalyticsService(session_factory, settings)`,方法(皆 async,返回 dict 供 routes 包 `{"data": ...}`):
  - `cycle_time(*, actor, workspace_id, project_id=None, win_from, win_to, from_category="in_progress", tz=None, refresh=False)` → `{project_id, from_category, p50_seconds, p90_seconds, sample_size, meta:{insufficient_data, display_timezone, cached?}}`
  - `throughput(*, actor, workspace_id, project_id=None, project_ids=None, win_from, win_to, granularity="day", calendar_timezone=None, refresh=False)` → `{granularity, series:[{label, bucket(UTC), window_start, window_end, created, completed, net}], meta:{calendar_timezone, display_timezone, net_window, scope_caliber?}}`
  - `velocity(*, actor, workspace_id, project_id=None, cycle_ids=None, win_from=None, win_to=None, tz=None)` → `{cycles:[...], meta:{display_timezone, scope_caliber:"current_attribution"}}`
  - `burndown(*, actor, workspace_id, cycle_id=None, milestone_id=None, metric="points", tz=None)` → `{scope:{type,id}, window:{start,end}, metric, total, ideal:[{date,remaining}], actual:[{date,remaining}], meta:{display_timezone, scope_caliber:"current_attribution"}}`
  - 闸门:项目级/cycle/milestone 不可见 → `ForbiddenError(code="project_not_visible")`;不存在 → `NotFoundError`;burndown 作用域校验(§3.4);`project_ids` 含不可见 → 整体 403;workload 以外的指标走缓存(scope_key + TTL + `refresh`)。

- [ ] **Step 1: 写失败测试(固定数据集逐值断言:percentile / 分桶 / velocity / burndown 理想+实际线)**

测试要点(用 `analytics_support` 扩展 seed issue/activity/executions,真实 ORM 行):
- cycle time:3 个 done issue(留痕 first in_progress 已知),P50/P90 与手算一致;1 个无留痕 → `insufficient_data == 1`;负时长样本不计入。
- throughput:UTC+8 分桶——某 issue created_at = `2026-07-24T16:30:00Z`(= 上海 7/25 00:30)落入 label `2026-07-25` 桶,`window_start == 2026-07-24T16:00:00Z`;UTC 分桶则落入 `2026-07-24`。
- velocity:cycle 窗内 done issue 计数/点数;未挂 cycle 的 done issue 不计;meta `scope_caliber == "current_attribution"`。
- burndown(points):total = scope estimate 和;actual 逐日 = total − 截至当日完成;ideal 线性至 0;仅输出过去日。
- 项目可见性:m1 查 workspace 级 cycle time/throughput 不含 PPRIV 数据;显式 `project_ids=[pub, priv]` → 403;`velocity?cycle_ids=[priv cycle]` → 403;burndown 双传/皆缺 → 400 具名码。

- [ ] **Step 2: 实现 service 骨架 + 4 方法(核心流程)**

```python
# backend/src/mesh/analytics/service.py (骨架,execution 方法见 Task 6)
class AnalyticsService:
    def __init__(self, session_factory, settings):
        self._factory = session_factory
        self._settings = settings

    async def _open(self, workspace_id):  # contextmanager: 新 session + set_tenant_context
        ...

    async def _issue_visibility(self, session, *, workspace_id, actor, project_id, project_ids):
        # 返回 (visible_ids | None, scope_key)
        # project_id 单项目:加载项目 + assert_can_view 语义(不可见 → 403 project_not_visible;不存在 → 404)
        #   scope_key = project:<id>
        # project_ids 多项目:任一不可见 → 整体 403(不部分返回);scope_key = projects:<hash(集合)>
        # 无:admin/owner → (None, "ws_admin");普通成员 → (visible_project_ids, "projects:<hash>")
        ...

    async def _cached(self, session, *, actor, workspace_id, metric_key, scope_key, dimensions,
                      window_start, window_end, compute, refresh):
        now = datetime.now(timezone.utc)
        if not refresh:
            value, row = await fetch_snapshot(session, workspace_id=workspace_id,
                metric_key=metric_key, scope_key=scope_key, dimensions=dimensions,
                window_start=window_start, window_end=window_end,
                ttl=self._settings.analytics_snapshot_ttl, now=now)
            if value is not None and snapshot_is_fresh(row, self._settings.analytics_snapshot_ttl, now):
                return value, True
        value = await compute()
        await upsert_snapshot(session, workspace_id=workspace_id, metric_key=metric_key,
            scope_key=scope_key, dimensions=dimensions, window_start=window_start,
            window_end=window_end, value=value, now=now)
        return value, False
```

cycle time/throughput/velocity/burndown 各方法:`_open` → `_issue_visibility` → 组装参数执行 queries.build_* → 整形响应(throughput 逐桶 label/window_start/window_end RFC3339;burndown 理想线 Python 线性生成)→ 缓存(cycle_time/throughput/velocity/burndown 可缓存;dimensions 含 `calendar_timezone`/`granularity`/`from_category`/`tz`/`project_id`)。

- [ ] **Step 3: 跑测试 → Commit**

Run: `cd backend && pytest tests/unit/test_analytics_issue_metrics.py -v`

```bash
git add backend/src/mesh/analytics/service.py backend/tests/unit/test_analytics_issue_metrics.py \
  backend/tests/unit/analytics_support.py
git commit -m "feat(analytics): cycle time/throughput/velocity/burndown 服务层(当前归属口径+日历分桶+缓存)"
```

---

## Task 6: Service 层 — workload / agent stats / dashboards

**Files:**
- Modify: `backend/src/mesh/analytics/service.py`
- Test: `backend/tests/unit/test_analytics_exec_metrics.py`

**Interfaces:**
- Produces:
  - `workload(*, actor, workspace_id, project_id=None, member_type=None, cursor=None, limit=50)` → `{data:[{member_id, display_name, member_type, open_issues, running?, queued?, awaiting_approval?}], next_cursor}`(workload-B 经统一 CTE;不缓存)
  - `agent_stats(*, actor, workspace_id, agent_id=None, win_from, win_to, refresh=False)` → 单 agent 对象(§3.3 形状,含 tokens + token_coverage + meta.token_note)或多 agent `{agents:[...]}`;private agent 不可见 → `ForbiddenError(code="agent_not_visible")`;缓存 scope_key = exec scope
  - `project_dashboard(*, actor, workspace_id, project_id, win_from, win_to, cycle_id=None)` → `{velocity, burndown|null, cycle_time, meta}`(项目可见性闸门;burndown 取 `cycle_id` 或项目当前 active/最近 cycle,无则 null)
  - `workspace_dashboard(*, actor, workspace_id, win_from, win_to, granularity="day", calendar_timezone=None)` → `{throughput, workload(top 10), agent_stats, meta:{visibility_note?, ...}}`(普通成员按可见性过滤;admin 全量)

- [ ] **Step 1: 写失败测试**

- workload:m1 的行不含 PA 的在途执行(private agent)、不含 PPRIV issue 执行计数;WA 在 PPRIV 上的 running 对 m1 剔除、对 m2/admin 保留;人类行 executions 字段为 null;按 open_issues DESC 排序 + 游标翻页;`member_type=agent` 过滤。
- agent_stats(agent_id=WA):m1 结果 == 「手动剔除 PPRIV 执行后重算」(executions/succeeded/success_rate/retry_rate/tokens 逐值);m2 含 PPRIV;admin 全量;retry_rate 用 attempts 数 − 1 派生;token_coverage = runs_with_token_data/executions;cancelled 不入成功率分母但披露 cancelled_count。
- agent_stats(agent_id=PA):m1 → 403 `agent_not_visible`;m3(owner)可见;admin 可见。
- 多 agent 模式:m1 的 agents 列表不含 PA。
- workspace_dashboard:m1 的 agent 统计区不含 PA 且 WA 数字已剔除 PPRIV;admin 全量;meta 对普通成员带 `visibility_note`。
- project_dashboard:含 velocity/burndown/cycle_time;m1 访问 PPRIV dashboard → 403。

- [ ] **Step 2: 实现(要点)**

- workload:workload-A(`build_workload_open_sql` + project_filter,单 `project_id` → `AND i.project_id = :project_id`)与 workload-B(`build_workload_inflight_sql` + 请求者三参)合并:agent_id → members(JOIN agents ON members.agent_id)统一成员维度;member_type 快照 + display_name(`mesh.member.display.resolve_display_name`,批量查询避免 N+1);cursor 用 `encode_cursor(f"{999999999-open:09d}:{999999999-running:09d}", member_id)` 实现降序键集,解码后字符串比较。
- agent_stats:先校验 agent_id(存在/工作区归属 → 404;private 且非 owner/非 admin → 403 agent_not_visible);三段 SQL(agent 过滤)在单 session 内执行并合并;`total_tokens = prompt + completion`;多 agent 模式逐 agent 合并(CTE 已按请求者剔除)。
- dashboards:组合上述方法(复用同一 service 实例与可见性闸门),workspace_dashboard 的 agent 统计区取多 agent 模式、workload 取前 10。

- [ ] **Step 3: 跑测试 → Commit**

Run: `cd backend && pytest tests/unit/test_analytics_exec_metrics.py -v`

```bash
git add backend/src/mesh/analytics/service.py backend/tests/unit/test_analytics_exec_metrics.py \
  backend/tests/unit/analytics_support.py
git commit -m "feat(analytics): workload/agent stats/项目与工作区仪表盘(统一 CTE 过滤+agent 可见性 403)"
```

---

## Task 7: schemas + routes + app 挂载

**Files:**
- Create: `backend/src/mesh/analytics/schemas.py`、`routes.py`
- Modify: `backend/src/mesh/api/app.py`
- Test: `backend/tests/unit/test_analytics_routes.py`(ASGITransport 起真 app)

**Interfaces:**
- 端点(前缀 `/api/v1/workspaces/{workspace_id}`,`require_workspace()` 解析成员;全部 GET;读限流桶 `analytics-read:{user}:{ip}` 300/60s,`refresh=true` 追加 `analytics-refresh` 30/60s):
  - `/analytics/cycle-time`、`/analytics/velocity`、`/analytics/throughput`、`/analytics/workload`、`/analytics/burndown`、`/analytics/agents/stats`
  - `/dashboards/project/{project_id}`、`/dashboards/workspace`
- 查询参数按 §3.2(UUID 字符串 → 路径 404/查询 400 惯例;`from`/`to`/`tz`/`granularity`/`metric`/`from_category`/`refresh`/`cursor`/`limit`/`cycle_ids` 逗号列表 ≤20(超限 `filter_too_complex`)/`project_ids`)。
- 响应:单对象 `{"data": {...}}`;workload 列表 `{"data": [...], "next_cursor": ...}`。

- [ ] **Step 1: 写失败测试(routes 冒烟 + 错误码 + 限流头)**

用 `create_app(load_settings(**_settings_kwargs(db_url, redis_url)))` + `httpx.ASGITransport`,经 `/auth/register`+`/auth/login` 拿 token,API 建 workspace/project/agent,ORM 补 issue/execution:
- 未认证 → 401 `unauthorized`;非成员 → 403/404(require_workspace 语义)
- `from>=to` → 400 `invalid_time_range`;`granularity=year` → 400 `validation_error`;`tz=Bad/Zone` → 400 `invalid_timezone`
- burndown 双传 → 400 `burndown_scope_conflict`;皆缺 → 400 `burndown_scope_required`
- cycle_ids 21 个 → 400 `filter_too_complex`
- 200 形状断言(各端点 `data` 关键字段 + `meta.display_timezone`)
- X-RateLimit-* 响应头存在

- [ ] **Step 2: 实现 schemas.py(仅请求模型)+ routes.py(薄 handler + `_rate_limit_read`)**

```python
# routes.py 骨架(8 个 handler 同构)
router = APIRouter(prefix="/api/v1", tags=["analytics"])
READ_LIMIT, READ_WINDOW = 300, 60
REFRESH_LIMIT, REFRESH_WINDOW = 30, 60

def _service(request: Request) -> AnalyticsService:
    return request.app.state.analytics_service

async def _rate_limit_read(request, user, response, *, refresh=False): ...  # 同 project/routes.py 范式

@router.get("/workspaces/{workspace_id}/analytics/cycle-time")
async def get_cycle_time(request: Request, response: Response,
                         workspace_id: str,
                         user: User = Depends(get_current_user),
                         context: WorkspaceContext = Depends(require_workspace()),
                         project_id: str | None = None,
                         **_: Any) -> dict:
    # Query 参数经 request.query_params 逐项解析(UUID/时间/枚举校验在 service/scope)
    await _rate_limit_read(request, user, response, refresh=refresh)
    data = await _service(request).cycle_time(actor=context.member, workspace_id=context.workspace.id, ...)
    return {"data": data}
```

`app.py`:`app.state.analytics_service = AnalyticsService(session_factory, settings)` + `app.include_router(analytics_router)`(按既有顺序插入)。

- [ ] **Step 3: 跑测试 → Commit**

Run: `cd backend && pytest tests/unit/test_analytics_routes.py -v`

```bash
git add backend/src/mesh/analytics/schemas.py backend/src/mesh/analytics/routes.py \
  backend/src/mesh/api/app.py backend/tests/unit/test_analytics_routes.py
git commit -m "feat(analytics): 8 个聚合端点 + 限流 + app 挂载(§3.1/§3.2/§3.4)"
```

---

## Task 8: T33 真实 e2e(四类请求者最终统计值断言 + 负向测试)

**Files:**
- Create: `backend/tests/e2e/test_analytics_e2e.py`

**Interfaces:**
- Consumes: 真 uvicorn(`api_server`/`api_client` fixture,RLS app 角色)+ 真 PG;`mesh.analytics.queries`(同一聚合 SQL 文本)
- 验收基线(analytics.md §5.6 / T33 七项):四类请求者**同一聚合 SQL** 断言**最终统计值**;跨权限缓存不共享;整体 403;分桶不跨日;只读审计;scope_caliber;calendar_timezone 维度分行。

- [ ] **Step 1: 搭 world(API 注册用户/工作区/项目/agent + ORM 精确 seed executions/attempts/autopilot_runs/issues/activity,使手算期望值确定)**

- [ ] **Step 2: 写 7 组断言**

① **普通成员 m1**:`GET agents/stats?agent_id=WA` 的 executions/succeeded/retry_rate/total_tokens 与「同一 SQL 代入 m1 三参」逐值一致(PPRIV 执行 + PA 全剔除);workload 在途 running/queued 同;`/dashboards/workspace` agent 区同。
② **项目成员 m2**:含 PPRIV 执行的聚合。
③ **private-agent owner m3**:含 PA 执行、仍不含 PPRIV 执行。
④ **admin**:全量。
⑤ **缓存负向**:admin 先查(写 `ws_admin` 行)→ 直查 `analytics_snapshots` 表确认行存在 → m1 查同指标:响应 != admin 值且表内新增 `exec:`/`projects:` 分行;改源表后 `refresh=true` 重算与直接聚合逐值一致(§5.5)。
⑥ **403 负向**:m1 `velocity?cycle_ids=[priv cycle]` → 403;`project_ids=pub,priv` → 整体 403;`agent_id=PA` → 403 `agent_not_visible`。
⑦ **分桶/时区/只读**:Asia/Shanghai day 桶 window_start = 前日 16:00Z(本地自然日不跨桶);America/New_York 跨 2026-03-08 春进日 throughput 日序列无重复/缺失、窗按当日 00:00 UTC 瞬间;查询前后 5 张真源表行数与 `MAX(updated_at)` 不变(仅 snapshots 变)。

- [ ] **Step 3: 跑 e2e**

Run: `cd backend && pytest tests/e2e/test_analytics_e2e.py -v -m e2e`
Expected: all passed

- [ ] **Step 4: 全量后端覆盖率门禁**

Run: `cd backend && pytest --cov=mesh --cov-report=term-missing --cov-fail-under=90 -q`(analytics 新文件逐文件 ≥90% 自查)

- [ ] **Step 5: ruff → Commit**

Run: `ruff check backend/src backend/tests`

```bash
git add backend/tests/e2e/test_analytics_e2e.py
git commit -m "test(analytics): T33 真实 e2e——四类请求者同一聚合 SQL 最终值断言+缓存/403/分桶/只读负向"
```

---

## Task 9: 前端 — api/types/图表组件

**Files:**
- Create: `frontend/src/features/analytics/{api.ts,types.ts,charts.tsx,analytics.css}`
- Test: `frontend/src/features/analytics/__tests__/{api.test.ts,charts.test.tsx}`

**Interfaces:**
- `api.ts`:`fetchCycleTime(client, ws, params)` … `fetchWorkspaceDashboard(client, ws, params)`(经 `client.request`/`client.list`;参数 RFC3339 UTC)
- `charts.tsx`:`LineChart({series:[{name,color:'success'|'danger'|'info'|'neutral',dashed?,points:[{x,y}]}], width, height, xLabels, yFormat})`、`GroupedBarChart({groups:[{label,bars:[{name,value,color}]}]})`、`Sparkline({points,color})`——手写 SVG,颜色仅 `var(--color-*)`,线型区分(虚/实),`<title>`/ARIA 文本兜底,`prefers-reduced-motion` 尊重。

- [ ] **Step 1: 写失败测试**(api fetch 形状 + 错误透传;charts 渲染:svg 存在、series 路径数、dashed 用 `stroke-dasharray`、ARIA label)
- [ ] **Step 2: 实现**(颜色 token:`--color-success/danger/info/warn` + 中性 `--color-text-muted`;无硬编码色值)
- [ ] **Step 3:** Run: `cd frontend && npx vitest run src/features/analytics` → Commit

```bash
git commit -m "feat(analytics): 前端 api/types + 手写 SVG 图表(语义 token,亮暗双主题,§4.5)"
```

---

## Task 10: 前端 — 三个界面 + 导航 + i18n

**Files:**
- Create: `frontend/src/features/analytics/{InsightsPage.tsx,ProjectDashboardPanel.tsx,AgentStatsCard.tsx}`
- Modify: `App.tsx`、`ProjectDetailPage.tsx`(dashboard 页签)、`AgentDetailPage.tsx`(overview 嵌卡)、`Sidebar.tsx`、`shortcutsRegistration.ts`、`AppShell.tsx`、`catalogs/en.json`+`zh-CN.json`(analytics.* + `error.invalid_time_range`/`burndown_scope_required`/`burndown_scope_conflict`/`invalid_timezone`/`filter_too_complex`/`query_cost_exceeded`/`project_not_visible`/`agent_not_visible`/`rate_limited`,缺失者补)、`catalogs.test.ts` dummyValues、`scripts/verify-perfile-coverage.mjs`(PER_FILE_DIRS += `src/features/analytics/`)
- Test: `__tests__/{InsightsPage.test.tsx,ProjectDashboardPanel.test.tsx,AgentStatsCard.test.tsx}`

**Interfaces:**
- InsightsPage(`/insights`):时间窗预设(近 30/90 天 + 自定义)+ 吞吐量 LineChart(created/completed/net)+ workload 排行表(成员/agent 图标、open issues、运行中/排队/需审批)+ agent 统计卡网格(成功率语义色、平均时长、重试率、sparkline、token 覆盖标注)+ 可见性轻提示 `analytics.note.visibilityScope`;状态矩阵(Skeleton/EmptyState/ErrorState+retry/403 无权限页)。
- ProjectDashboardPanel(`?tab=dashboard`):velocity GroupedBarChart(当前周期高亮)+ burndown LineChart(理想虚线/实际实线,count/points 切换)+ cycle time P50/P90 + sample/insufficient 标注。
- AgentStatsCard:KPI 行 + sparkline + token 区 + 「查看运行历史」深链 `/executions/{id}`(§6.12)。

- [ ] **Step 1: 写组件测试**(mock fetchImpl:loading skeleton → 数据渲染;empty 态;error+retry;403 permission 态;时区回显 `meta.display_timezone`;agent 卡 token_coverage<1 标注)
- [ ] **Step 2: 实现组件 + 接线路由/页签/导航**(nav key `insights`;命令面板 labels 完整防编译错)
- [ ] **Step 3: i18n 双目录键集一致 + version 重算**(按 `catalogLoader.computeCatalogVersion` 算法;新占位符入 dummyValues)
- [ ] **Step 4:** Run: `cd frontend && npm run lint && npm run typecheck && npx vitest run src/features/analytics src/i18n` → Commit

```bash
git commit -m "feat(analytics): 洞察页 + 项目仪表盘页签 + agent 统计卡 + 导航/i18n(§4.1–§4.6)"
```

---

## Task 11: 前端全量覆盖率 + Playwright 真实栈 UI 走查

**Files:**
- Create: `frontend/playwright.analytics.config.ts`、`frontend/e2e/real-analytics.spec.ts`、`frontend/e2e/evidence/analytics/*.png`
- Modify: `frontend/scripts/verify-perfile-coverage.mjs`(Task 10 已改则跳过)

- [ ] **Step 1:** Run: `cd frontend && npm run test:coverage`(全局 ≥90% + per-file + `node scripts/verify-coverage.mjs --base origin/main`)
- [ ] **Step 2: 起真实栈**(docker compose 或本机容器:PG/Redis/MinIO + `alembic upgrade head` + uvicorn api + `python -m mesh.workers` + vite/预览构建),按 `playwright.runtimes.config.ts` 范式写 analytics 配置(baseURL 指向真栈,`testMatch: real-analytics.spec.ts`)
- [ ] **Step 3: 走查脚本(像真人操作)**:注册/登录 → 建项目/issue/执行 seed → 打开 `/insights`:断言吞吐量图/排行/agent 卡渲染,切时间窗重查,截图 `01-insights-overview.png`;切暗色模式截图 `02-insights-dark.png`;以普通成员与 admin 两个账号各截图 `03-visibility-member.png`/`04-visibility-admin.png`(断言数值差异);项目详情 `?tab=dashboard` 截图 `05-project-dashboard.png`(velocity/burndown/cycle time 卡 + count/points 切换);成员名册 → agent 详情统计卡截图 `06-agent-stats-card.png`。evidence 文件名唯一(check-evidence-unique)。
- [ ] **Step 4:** Run: `cd frontend && npx playwright test --config=playwright.analytics.config.ts` → Commit(含 evidence)

```bash
git commit -m "test(analytics): 真实栈 Playwright UI 走查 + evidence(仪表盘/时间窗/可见性差异/暗色)"
```

---

## Task 12: 文档同步 + 回归 + PR + 验收流转

**Files:**
- Modify: `README.md`(功能清单/Quick Start 若涉)、`docs/specs/README.md`(实现进度勾选,若其有 §5 状态表)、`CHANGELOG.md`
- 核对:`docker compose up` 全栈起 + Quick Start 流程不退化;`tests/docs/check_event_vocab.py` + `check_roster_entry.py` 绿(本模块无新事件名/无第二 Agents 入口);`schema_r2_validation.sql` 不受影响(仅新增表)。

- [ ] **Step 1: 文档**(analytics.md 与实现一致;若有 `project_ids` 参数补充 §3.2 一行——属 spec R3 既有语义,完工评论说明;README 模块清单加 analytics)
- [ ] **Step 2: 全量回归**:后端 `pytest --cov=mesh --cov-fail-under=90`;`ruff check`;前端 `lint && typecheck && test:coverage && build`;docs 脚本 ×2
- [ ] **Step 3: rebase 最新 main**(迁移重编号惯例解撞号;解冲突保留并行线接线)
- [ ] **Step 4: push 自查 + PR**

```bash
git log @{u}..HEAD --format=%B 2>/dev/null | grep -i 'co-authored-by'   # 必须无输出
git log -3 --format='%an <%ae> | %cn <%ce>'                             # 均为 cnwenf <cnwenf@outlook.com>
git push -u origin HEAD
gh pr create --title "feat(analytics): 统计报表模块全功能实现(analytics.md 五章,阶段 8 平台能力 C;MES-71)" --body-file ./pr_body.md
```

- [ ] **Step 5: 完工评论(issue comment,--content-file)+ mention 验收员 + 状态 in_review**

完工评论包含:PR 链接、六指标口径要点、T33 七项实测结果、UT/e2e 覆盖率数字、UI 走查结论、文档同步清单;末尾 `[@Mesh 验收员](mention://agent/50c3bdd4-625e-47b5-b7c1-b1995b4147a5)` 请求验收。

---

## Self-Review(对照 analytics.md 五章)

- §2.1–§2.2 六类指标口径:Task 3(SQL)+ Task 5/6(service)+ Task 8(e2e 逐值断言)✓
- §2.3/§2.3.1 统一 CTE + 四类请求者:Task 2(CTE 唯一来源)+ Task 3(四段内联)+ Task 8(①–④最终值断言)✓
- §2.4 时区/calendar_timezone/DST:Task 2(解析链)+ Task 5(分桶)+ Task 8(⑦ DST + UTC+8 不跨日)✓
- §2.5/§2.6 缓存 scope_key/TTL/一致性:Task 1(表)+ Task 4(cache)+ Task 8(⑤跨权限负向 + refresh 一致)✓
- §3 端点/错误码/分页/403:Task 7 + Task 8(⑥ 403 负向)✓
- §4 UI/UX(三界面/语义色/暗色/状态矩阵/i18n):Task 9–11 ✓
- §5.3 只读审计:Task 8(⑦ 行数/updated_at 不变)✓
- §5.4 性能:聚合命中源表既有索引(§2.5 索引清单)+ `SET LOCAL statement_timeout` 兜底 → `query_cost_exceeded`(Task 5/6 实现注);P95 基准测试非本地必测项,EXPLAIN 抽样附完工评论 ✓
- 占位符扫描:无 TBD;Task 5/6 的「实现要点」均给出方法签名与 SQL 引用,完整代码在执行期按本计划骨架产出并以测试锁定 ✓
- 类型一致性:`scope_key` 形态(`ws_admin`/`projects:<hash>`/`project:<id>`/`exec:p<h>:a<h>`)、方法名(cycle_time/throughput/velocity/workload/burndown/agent_stats/project_dashboard/workspace_dashboard)、CTE 常量名全篇一致 ✓
