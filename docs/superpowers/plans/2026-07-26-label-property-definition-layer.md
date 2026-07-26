# label-property 定义层切片(label-property.md §2–§4)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:writing-plans(本文件即其产物)→ superpowers:test-driven-development 实施 → superpowers:verification-before-completion 收尾 → superpowers:requesting-code-review 提验收。Steps 用 checkbox(`- [x]` 已落地)追踪,并附 RED→GREEN / 实测证据。

**Goal:** 落地 label-property 的**定义层**切片——标签、自定义字段定义、枚举选项三表的模型/迁移、独立 CRUD 接口、实时事件、工作区与项目设置管理 UI——且与 issue 模块**零耦合**,可与 MES-31 并行。issue 关联(`issue_labels`/字段值/选择器/合并/issue 侧事件/§2.8 性能)显式延后(门控 MES-31)。

**Architecture:** 新增叶子模块 `backend/src/mesh/labels/`(routes/schemas/service)+ 模型 `backend/src/mesh/db/models/label.py` + 迁移 `0008_labels.py`;前端新增 feature `frontend/src/features/labels/`(api/types/ColorPicker/LabelsPanel/CustomFieldsPanel/pages + i18n)。作用域内命名唯一一律用 README §6.3 **部分表达式唯一索引**(`COALESCE(project_id,'0000…')`,禁表级 UNIQUE);三表 `UNIQUE(workspace_id,id)` + 同租户复合 FK + fail-closed RLS;workspace-less 路径经窄 `SECURITY DEFINER` 解析函数解析租户并授 `mesh_app`。事件名取自 §6.7 注册表,经 outbox → projector 唯一写入路径。

**Tech Stack:** Python 3.12 / SQLAlchemy 2.x async(asyncpg)/ Alembic / PostgreSQL 16 / Redis / FastAPI + uvicorn 子进程真实 e2e / pytest + pytest-cov(≥90%)/ ruff;前端 React 18 + TS + Vite + Vitest/RTL + react-intl / Playwright(真实后端走查)。

## Global Constraints

- **Spec 权威** = `docs/specs/features/label-property.md` + README §6(§6.2/§6.3/§6.7/§6.14);不擅自改需求;延后项在 Spec/CHANGELOG 显式登记。
- **多租户**:同租户复合 FK + `UNIQUE(workspace_id,id)` + RLS;负向(跨租户读空/写拒/复合 FK 拒)必有真实 SQL 实测(§9 T1)。
- **覆盖率**:后端 pytest-cov `fail_under=90`(整体 + 新增双达标);前端整体门禁 ≥90% **且** `verify-coverage.mjs` 新增代码 ≥90% **且** 新增面板 per-file 分支/函数 ≥90%(vite 目录级阈值兜底)。
- **测试纪律**:契约路径无 mock;e2e 真实 uvicorn 子进程(mesh_app 角色,RLS 生效)+ 真实 HTTP + DB 落库;前端真实后端 Playwright 走查 + 截图存证。
- **提交纪律**:git 身份 `cnwenf <cnwenf@outlook.com>`;无 `Co-Authored-By`;`core.hooksPath=/dev/null`;匿名化;ruff / eslint / typecheck 全绿。
- **本地测试隔离**(本机特有):共享 site-packages 的 `mesh` 可能指向别 worktree;后端测试用独立库 `mesh_test_mes42` + redis db 4/5,经显式 `MESH_TEST_DATABASE_URL`/`MESH_TEST_REDIS_URL` 运行,勿裸跑污染他 agent。

---

## File Structure

- **Create** `backend/src/mesh/db/models/label.py` — `Label` / `CustomFieldDef` / `CustomFieldOption`;§6.3 表达式唯一索引 `uq_labels_name`/`uq_cfdefs_key`、`uq_*_ws_id`、复合 FK、CHECK。
- **Modify** `backend/src/mesh/db/models/__init__.py` — 登记三模型(供 `Base.metadata` / 漂移测试 / clean_tables)。
- **Create** `backend/migrations/versions/0008_labels.py` — DDL(镜像 `schema_r2_validation.sql`)+ RLS + `mesh_app` GRANT + 三个窄 SECURITY DEFINER(`REVOKE … FROM PUBLIC`)。
- **Create** `backend/src/mesh/labels/{__init__,schemas,service,routes}.py` — 定义层 CRUD + 校验 + 事件 + 审计 + 限流。
- **Modify** `backend/src/mesh/api/app.py` — 注入 `label_service` + 注册路由。
- **Create** `backend/tests/unit/test_label_service.py`、`test_label_api.py`、`backend/tests/e2e/test_label_e2e.py`。
- **Create** `frontend/src/features/labels/{api,types,index,labels.css,ColorPicker,LabelsPanel,CustomFieldsPanel}.tsx` + `pages/Workspace{Labels,CustomFields}Page.tsx`。
- **Modify** `frontend/src/App.tsx`(两子页路由)、`workspace/pages/WorkspaceSettingsPage.tsx`(入口链接)、`features/projects/ProjectSettingsPage.tsx`(项目级面板)。
- **Modify** `frontend/src/i18n/catalogs/{en,zh-CN}.json`(+83 键,含 4 具名错误码)+ `__tests__/catalogs.test.ts`。
- **Create** `frontend/src/features/labels/__tests__/{api,ColorPicker,LabelsPanel,CustomFieldsPanel,pages}.test.tsx` + `*.coverage.test.tsx`(分支/函数补强)。
- **Modify** `frontend/vite.config.ts` — `features/labels/**/*.tsx` 目录级阈值。
- **Modify** `frontend/playwright.real.config.ts` + **Create** `frontend/e2e/real-labels.spec.ts` + 截图 `e2e/evidence/labels/`。
- **Modify** `README.md`(实现状态行 v0.11.0)、`CHANGELOG.md`(`[0.11.0]`)。

---

### Task 1: 数据模型 + 迁移(TDD/契约先行)

**Interfaces:** `Label`/`CustomFieldDef`/`CustomFieldOption`(SQLAlchemy 2.x `Mapped`);迁移 `revision="0008"`, `down_revision="0007"`。

- [x] **Step 1(契约/漂移红→绿):** 先写模型与迁移,用 `tests/unit/test_model_migration_drift.py` 作红线——首版 `custom_field_options` 的 `UNIQUE(field_def_id,name)` 在模型侧误用 `Index(unique=True)`,漂移测试报 `remove_constraint`+`add_index` 未配对(红);改为 `UniqueConstraint` 后与迁移表级 UNIQUE 配对(绿)。同时验证 §6.3 表达式索引在 compare_metadata 下**无伪漂移**(实证:`uq_labels_name`/`uq_cfdefs_key` 不进 unexplained diffs)。
- [x] **Step 2:** 迁移在独立库 `mesh_test_mes42` 应用 `alembic upgrade head` 通过;`downgrade` 完整。
- [x] **Step 3(权限实测):** `information_schema.role_table_grants` 证 `mesh_app` 对三表具 SELECT/INSERT/UPDATE/DELETE;`pg_policies` 证三表各有 `mesh_*_tenant` 策略;三个 SECURITY DEFINER `has_function_privilege('mesh_app',…,EXECUTE)=t`。

### Task 2: 服务层校验与 CRUD(TDD RED→GREEN)

- [x] **RED:** `test_label_service.py` 先写校验/冲突/鉴权/事件断言(空名/超长/非法色 → `ValidationError`;同作用域重名 → `ConflictError label_name_taken`;非 admin 且非 lead → `ForbiddenError`;事件经 outbox `realtime.publish`)。
- [x] **GREEN:** 实现 `_validate_*` + `_CONFIG_KEYS` + 按类型 `default_value` 校验;`IntegrityError` 经 `violates()` 映射具名码。
- [x] **RED→GREEN(乐观并发):** PATCH 行锁 `with_for_update()` + `If-Match` 比对 `updated_at`;测试覆盖新鲜 200 / 过期 409 / 非法 `If-Match`。
- [x] **事件渠道路由:** 工作区级 → `workspace:{ws}:labels|custom_fields`;项目级 → `project:{id}`(私有只该频道,公开双发)。测试断言 channel 集合。
- [x] **修复(死事务读):** `update_label`/`update_option` 的 rename 冲突 flush 后访问 ORM 属性触发 dead-transaction lazy refresh(`InvalidRequestError`);改为 flush 前捕获 `new_name` 局部变量(红→绿)。

### Task 3: 路由 + 真实 e2e(T1/RLS 负向)

- [x] **API 单测:** `test_label_api.py` 46 项断言(包络/游标分页两页不相交/If-Match/限流头/401/非 UUID 404/跨工作区 404/具名码)。
- [x] **e2e 真实栈:** `test_label_e2e.py` 全流 CRUD + 落库 + outbox→projector(`run_once` 后 `realtime_events` seq 单调);**T1** 跨租户复合 FK INSERT 拒 + 跨工作区 API 404;**RLS** mesh_app 跨租户读 0/写拒。

### Task 4: 前端面板 + i18n + 走查(TDD)

- [x] **api/types/ColorPicker 测试先行;** 面板 `LabelsPanel`/`CustomFieldsPanel` 列表/空态/错误态/新建/编辑/删除/选项编辑器。
- [x] **真实走查:** `real-labels` Playwright(注册/登录 → 建区 → 标签 CRUD + 内联 409 + 编辑 → 字段带枚举选项 + 非法 key + 停用 + 选项编辑器 → 项目设置项目级 → 删除二次确认)全绿,12 截图存证 + SQL 复核落库/投影。
- [x] **i18n:** en+zh-CN 键集一致(catalogs parity 测试);ICU 占位 `{index}` 入 dummyValues;正则量词语义改写以避开 ICU 花括号解析。

### Task 5: 验收打回 #3 修复(覆盖率补强,systematic-debugging)

> 验收员 REJECT(评论 `40791246`)三项硬阻塞 + 流程项,逐项闭环:

- [x] **(ruff 红):** 本地复现 CI 同款 `ruff check backend/src backend/tests` 三错(F401 `select` / I001 / F401 `UNSET`),`ruff check --fix` 后复跑 `All checks passed!`。
- [x] **(新增代码分支/函数,systematic-debugging):** 用 `coverage-final.json` 解析**精确未覆盖分支/函数行号**作 punch-list(而非猜),据此补 `*.coverage.test.tsx`:
  - 不可达分支诊断:`WorkspaceGate` 在 `status==='loading'` 拦截子树,致页面 `if(workspace===null)` 守卫**经真实 Provider 不可达** → 在 `pages.test.tsx` 改 mock `useWorkspace` 注入 `workspace:null` 覆盖该分支(分支 75%→100%)。
  - 补:校验分支(空名/非有限 position/重名选项/非法 hex→null)、各 catch 的 `error.unknown` 非 API 回退、停用↔启用双向、删除取消(取消按钮 + 关闭 X 两函数)、选项编辑器增/删/改/停用 的成功与失败、realtime 非空上下文(订阅 + 匹配帧刷新 + 非匹配帧忽略 + 卸载清理)、项目级/停用态行渲染、类型切换草稿 false 分支。
  - **结果(per-file):** CustomFieldsPanel 99.1/95.42/100;LabelsPanel 100/97.29/100;两 page 100/100/100(stmt/branch/func)。`verify-coverage --base origin/main` = **99.6% PASS**。
  - **回归闸:** `vite.config.ts` 增 `src/features/labels/**/*.tsx` 目录级 90% 阈值,防再被全局门禁掩盖。
- [x] **(writing-plans):** 本文件。
- [ ] **(合入主干):** 见 Task 6(rebase 最新 main + CI 全绿 + 合并 + 确认 `origin/main` 含提交)。

### Task 6: 收尾与重交

- [x] README 实现状态行(v0.11.0)+ CHANGELOG `[0.11.0]`(Added/Deferred/Quality,Deferred 正确列门控项)。
- [ ] rebase `origin/main`(已含 PR #30 硬化批)→ 解 CHANGELOG 冲突(`[0.11.0]` 置顶于 `[0.10.3]` 之上,无数值碰撞)→ push → 合并 PR #31 → `git branch -r --contains` 确认。
- [ ] 重交评论逐项列五 skill 落点 + mention 验收员复验。

---

## Superpower skills 落点(重交核对)

| Skill | 落点 / 证据 |
| --- | --- |
| **writing-plans** | 本文件(`docs/superpowers/plans/2026-07-26-label-property-definition-layer.md`),含 Goal/Architecture/Tech Stack/Global Constraints/File Structure/分 Task checkbox。 |
| **test-driven-development** | 服务层/面板均 RED→GREEN:漂移测试先红(UniqueConstraint 修正)再绿;校验/冲突/鉴权/并发用例先写后实现;死事务 lazy-refresh 由测试逼出修复。后端 95% / 前端新增 99.6%。 |
| **systematic-debugging** | 覆盖率缺口用 `coverage-final.json` 解析**精确行号**作 punch-list;`workspace===null` 不可达分支经读 `WorkspaceGate` 源码定位根因后改 mock 策略;ICU 花括号/union 类型/多 render DOM 污染等逐一据报错定位。 |
| **verification-before-completion** | 见下「实测清单」,每项均有命令+数值,非口述。 |
| **requesting-code-review** | 首轮完工 mention 验收员(评论 `de86ca6e`);REJECT 后本轮修复完毕再次 mention 复验。 |

## verification-before-completion 实测清单

- 后端 `ruff check backend/src backend/tests` → `All checks passed!`
- 后端 `pytest --cov=mesh --cov-fail-under=90` → exit 0,TOTAL **95%**(labels 服务 94% / 路由 90% / schema 100% / 模型 100%)。
- 后端 e2e(test_label_e2e)→ 全绿;T1 复合 FK 拒 + RLS 跨租户读 0/写拒 实测。
- 漂移 `test_model_migration_drift` 绿;`check_event_vocab.py` / `check_roster_entry.py` 绿;`schema_r2_validation.sql` PG16 绿。
- 前端 `tsc -b --noEmit` 0 错;`npm run lint` 0 错(7 既有 warning);`npm run build` 成功。
- 前端 `npm run test:coverage` → **1135 passed**,exit 0(含目录级阈值);per-file 见 Task 5。
- 前端 `node scripts/verify-coverage.mjs --base origin/main` → **99.6% PASS**。
- 前端 `real-labels` Playwright 真实后端 → 1 passed;12 截图肉眼为真实渲染;SQL 复核落库 + `realtime_events` 投影。
- docker compose `up --build -d` → api/worker/gateway/frontend/pg/redis 起;`alembic` 应用 0008;curl 注册/登录/建区/标签+字段 CRUD + 409 + 422 全通。
- 匿名化 grep `multica/linear(产品义)/…` 无泄漏;提交 author+committer 均 `cnwenf`,无 co-author。
