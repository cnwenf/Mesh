# Onboarding 模块实现计划(MES-69,阶段 8·平台能力 A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/specs/features/onboarding.md` 五章全量实现上手引导模块:`onboarding_states` / `onboarding_state_steps` 进度真源、入册播种 + 成熟工作区全量 reconcile(R3/R4)、aha 末步 `notification.read` 阅读证据四元组、REST API、`onboarding.progress` / `onboarding.completed` 实时事件、前端清单卡片 + 六页空状态 + i18n。

**Architecture:** 后端新模块 `backend/src/mesh/onboarding/`(models/service/consumers/routes/schemas/channels)。播种走成员入册事务同事务钩子(workspace 创建 / 邀请兑换 / 直接添加)+ GET 惰性兜底;自动完成走 outbox relay 的 `realtime.publish` 合成处理器(投影 → autopilot 匹配 → onboarding 消费,沿用 autopilot.matcher 模式);完成守卫以条件 UPDATE 实现幂等;派生实时事件经 `emit_realtime` 走 outbox 唯一路径。前端新模块 `frontend/src/features/onboarding/`,清单卡片挂载于 AppShell,空状态升级六页既有 EmptyState。

**Tech Stack:** FastAPI / SQLAlchemy 2.x async / PostgreSQL 16 / Alembic(raw SQL 迁移)/ pytest + pytest-cov(真 PG + 真 Redis)/ React 19 + TS + Vite + Vitest + react-intl / Playwright(真栈证据截图)。

## Global Constraints

- Spec 权威:`docs/specs/features/onboarding.md` + README §6.1/§6.2/§6.6/§6.7/§6.9/§6.12/§6.13/§6.14/§6.16 + §9 T34。
- 迁移号 `0027`,`down_revision="0026"`,单头链;raw SQL `op.execute`;RLS fail-closed + GRANT mesh_app。
- 实时事件名只用已登记的 `onboarding.progress` / `onboarding.completed`(vocab 已有,无需改词汇表);本模块**不**新增 outbox 内部 event_type(消费既有 `realtime.publish` 载荷事件)。
- 存储层无 `*_type` 判别列;人类/agent 判别 JOIN `members.member_type`;`onboarding_states.(workspace_id, member_id)` 复合 FK → `members(workspace_id, id)`。
- 末步仅 `notification.read` 驱动;严格按 `trigger_member_id` 归属;evidence 四元组 `{execution_id, comment_id, notification_id, trigger_member_id}`;aha 仅触发者置位、仅置一次。
- agent 成员不播种清单。
- UT 覆盖率整体与新增代码均 ≥ 90%(backend `pytest --cov-fail-under=90`;frontend `test:coverage` 90% 门)。
- git 身份 `cnwenf <cnwenf@outlook.com>`;提交无 Co-Authored-By。
- i18n:en + zh-CN 键集 parity,djb2 `version` 重算写入两个 catalog JSON。
- agent 创建入口唯一为成员名册(`tests/docs/check_roster_entry.py` CI 防回归)。

## 关键实现事实(探查结论,供各任务引用)

- relay 合成处理器位置:`backend/src/mesh/workers/main.py::build_relay` 内 `_realtime_publish_with_autopilot` → 追加 `await onboarding.consume_realtime_event(session, event)`(try/except 不阻断投影,仿 autopilot matching)。
- 四个消费事件均为 `realtime.publish` 载荷事件:`member.added`(data: member_id/member_type/role)、`issue.created`(data.issue 含 id/reporter)、`execution.queued`(enqueue.py 形:execution_id/agent_id/issue_id/trigger;triggers.py 形无 execution_id → 跳过;mentions.py 形 execution_id 为 outbox 事件 id,不解析为执行行 → 跳过)、`notification.read`(data: id/read_at,频道 member:{recipient}:inbox)。
- mention 触发者归属:`comment_mentions.triggered_execution_id` 存的是 `execution.enqueue` **outbox 事件 id**(skeleton);经 `outbox_events.idempotency_key = 'ws:{ws}:{execution.idempotency_key}'` 反查 outbox 行 → `comment_mentions` → `comments.author_id`。
- assign 触发者归属:`issue_activity` 行 `field='assignee_id'`,`new_value` 为 agent **成员** id 的 JSON 字符串,`actor_member_id` = 分派操作者;`task_executions.agent_id` = agents.id,经 `members.agent_id` 关联成员。
- 通知链:`notifications.comment_id → comments(author_id → members.member_type='agent')`;`notifications.execution_id` 可为 NULL(comment_created 不带),故末步执行解析以 (issue_id, agent) 最近 completed 执行为主、`notifications.execution_id` 为优先校验。
- 入册钩子标记注释已在位:`workspace/invitations.py` `_accept_in_transaction`(member.added 之后)、`member/service.py` `add_member`(audit 之后)、`workspace/service.py` `create_workspace`(audit 之后)。agent 创建(`agent/service.py`)**不**播种,仅触发 `member.added` 消费。
- 频道授权:`realtime/auth.py` member 实体分支(现仅 `:inbox` 后缀)扩展支持 `:onboarding` 后缀(同 roster 归属解析);`comment_inbox/channels.py` 的 member checker 扩展接受两种后缀(所有权规则相同:principal 拥有该 member 行)。
- 路由鉴权:自助端点用 `resolve_workspace_context(session, user=user, workspace_id=...)`(`?workspace_id=` 查询参数)+ 清单归属 == ctx.member.id 防 IDOR;admin 重置用 `require_workspace("workspace:manage_members")` 路径参数。
- e2e 基线:`tests/e2e/conftest.py` 真 uvicorn + mesh_app RLS;relay fixture 用 `build_relay(settings, factory, None, build_object_storage(settings), mailer=None)` + `run_once()` 排空;注册/登录/建区/邀请兑换 helper 可仿 `test_comment_inbox_producers_e2e.py`。

## 文件结构

**后端新建:**
- `backend/src/mesh/db/models/onboarding.py` — `OnboardingState` / `OnboardingStateStep` 模型 + 枚举常量(`STEP_KEYS`、`STEP_STATUS_VALUES`、`COMPLETED_VIA_VALUES`、`ACTIVATION_CHECKLIST`)。
- `backend/migrations/versions/0027_onboarding.py` — 两表 + 索引 + 复合 FK + CHECK + RLS + GRANT。
- `backend/src/mesh/onboarding/__init__.py`、`service.py`(播种/reconcile/完成守卫/dismiss/restore/reset/渲染)、`consumers.py`(四事件消费 + 触发者归属解析)、`routes.py`、`schemas.py`、`channels.py`(`onboarding_channel(member_id)` helper)。
- `backend/tests/unit/test_onboarding_models.py`、`test_onboarding_service.py`、`test_onboarding_consumers.py`、`test_onboarding_routes.py`。
- `backend/tests/e2e/test_onboarding_e2e.py` — T34 四场景 + API 契约 + 跨租户/IDOR。

**后端修改:**
- `backend/src/mesh/db/models/__init__.py` — 注册新模型(若为显式导入)。
- `backend/src/mesh/api/app.py` — `app.state.onboarding_service` + `include_router(onboarding_router)`。
- `backend/src/mesh/workers/main.py` — 合成处理器追加 onboarding 消费。
- `backend/src/mesh/workspace/service.py` / `workspace/invitations.py` / `member/service.py` — 同事务播种钩子(替换标记注释)。
- `backend/src/mesh/realtime/auth.py` — member 分支支持 `:onboarding` 后缀。
- `backend/src/mesh/comment_inbox/channels.py` — member checker 接受 `:onboarding` 后缀。

**前端新建:**
- `frontend/src/features/onboarding/`:`api.ts`、`types.ts`、`realtime.ts`、`useOnboarding.ts`(工作区/成员派生 + state 获取 + 30s 轮询降级)、`OnboardingChecklist.tsx`(卡片/进度条/步骤/CTA/aha 庆祝/dismiss)、`illustrations.tsx`(六空状态 + 庆祝 SVG,语义 token)、`onboarding.css`、`__tests__/*`。
- `frontend/e2e/real-onboarding.spec.ts` + `playwright.onboarding.config.ts` + `e2e/evidence/onboarding/*.png`。

**前端修改:**
- 六页空状态升级(inbox/projects/board/members/chat/autopilots):插画 + 新文案键 + 主操作深链既有向导。
- `frontend/src/shell/AppShell.tsx` — 挂载 `<OnboardingChecklist />`。
- `frontend/src/shortcuts/ShortcutHelp.tsx` + `App.tsx` — 帮助菜单「重新显示上手清单」+ 命令面板命令。
- `frontend/src/features/members/MembersPage.tsx` — admin/owner「重置该成员上手进度」入口(二次确认)。
- `frontend/src/i18n/catalogs/{en,zh-CN}.json` — `onboarding.*` 键集 + djb2 version 重算。

**文档:**
- `README.md` 状态表新增 onboarding 行(`✅ v0.18.0`)。
- `CHANGELOG.md` 顶部 `[0.18.0] - 2026-07-29` 条目。

## 任务序列

### Task 1: 数据模型 + 迁移 0027(TDD)
- 模型按 Spec §2.2/§2.3:两表、`UNIQUE(workspace_id, id)`×2、`UNIQUE(workspace_id, member_id, checklist)`、`UNIQUE(workspace_id, state_id, step_key)`、部分索引 `idx_onboarding_steps_pending`(status <> 'completed')与 `idx_onboarding_states_ws_aha`(aha_reached_at IS NULL)、CHECK `(status='completed') = (completed_at IS NOT NULL)`、`completed_via` CHECK、step_key CHECK、复合 FK → members / → onboarding_states,ON DELETE CASCADE。
- 迁移 raw SQL + RLS + GRANT + downgrade;测试:漂移测试套件自动覆盖(`test_model_migration_drift.py`),另写 CHECK/FK 违规单测(跨租户 INSERT 拒绝、completed_at 一致性)。

### Task 2: service 播种 + reconcile + 守卫(TDD)
- `seed_checklist(session, workspace_id, member)`:幂等 INSERT(ON CONFLICT DO NOTHING via pg_insert)+ 五步批量;`create_workspace` 步即 completed(auto);随后全量 reconcile:
  - step2:历史名册 agent 存在或 human≥2 → evidence `{member_added_id}`(触发条件的成员)。
  - step3:工作区已有 issue 或该成员 report 过 → evidence `{issue_id(, reporter_member_id)}`。
  - step4:成员历史触发 assign(issue_activity)/ mention(comment_mentions→outbox→executions)→ evidence `{execution_id, trigger_member_id}`;无则 pending。
  - step5:成员历史已读且满足末步条件 → completed + aha + 四元组;否则 pending。
- 守卫:`complete_step`(条件 UPDATE,0 行 no-op)、`dismiss`/`restore` 条件 UPDATE、`aha` WHERE aha_reached_at IS NULL、`reset`(DELETE + 重播种 + reconcile)。
- 单测:播种幂等/并发唯一约束、reconcile 各分支、守卫 no-op、reset 语义。

### Task 3: consumers 四事件 + 触发者归属 + 派生实时(TDD)
- `consume_realtime_event(session, event)`:载荷事件分发;`set_tenant_context`;每分支:
  - `member.added` → step2 批量完成 pending(evidence member_added_id)。
  - `issue.created` → 工作区首 issue 批量 / reporter 即时。
  - `execution.queued` → 仅解析到真实 execution 行且 trigger ∈ assign/mention;归属解析(mention: outbox 幂等键反查;assign: issue_activity 最近行);仅完成触发者本人清单。
  - `notification.read` → 末步证据链校验(read_at 非空、agent 作者、completed 执行、触发者 == recipient)→ 完成 + aha + 四元组。
- 每步完成同事务 `emit_realtime` `onboarding.progress`(频道 `member:{member_id}:onboarding`,载荷含 state_id/checklist/step_key/status/completed_via/progress);aha 首置追加 `onboarding.completed`;幂等键 `onboarding:{state_id}:{step_key}:{via}` / `onboarding:{state_id}:completed`。
- workers/main.py 合成处理器接线。
- 单测:四事件逐分支 + 错误触发者拒绝 + 重复消费幂等 + 未读不完成。

### Task 4: REST API 五端点(TDD)
- `GET /api/v1/onboarding/state?workspace_id=`(惰性播种兜底)、`POST /onboarding/steps/{step_key}/complete?workspace_id=`(幂等,dismissed → 422 checklist_completed)、`POST /onboarding/dismiss?workspace_id=`、`POST /onboarding/restore?workspace_id=`、`POST /api/v1/workspaces/{workspace_id}/onboarding/reset`(body member_id/checklist,admin,403/404 矩阵)。
- 错误码:validation_error / forbidden / not_found / step_not_found / checklist_completed;Idempotency-Key 支持(复用守卫即 no-op 语义)。
- app.py 接线;单测覆盖鉴权/IDOR/枚举校验;e2e 冒烟。

### Task 5: T34 四真实场景 e2e(真栈)
- 场景 ①入册播种:建区(owner 清单 step1 completed)→ 邀请兑换(受邀者同事务播种)→ agent 创建不播种(DB 断言)。
- 场景 ②成熟工作区 reconcile:成熟区(agent/issue/历史执行齐备)邀请新成员 → step2/3 带证据 completed,step4(该成员未触发过)保持 pending,evidence 关联真实存在。
- 场景 ③未读不完成:assign 触发 → 执行完成(真 runtime daemon:激活/claim/attempt completed)→ agent 回评(CommentService 真服务)→ 通知生成(relay fanout)→ 不标读 → step5 pending、aha NULL。
- 场景 ④错误触发者:成员 B(订阅同 issue)标读 agent 回评通知 → B 末步不完成;触发者 A 标读 → A 末步完成 + aha + evidence 四元组 + `onboarding.completed` 落 realtime_events;`notification.read` 事件经 relay 消费。
- 附:跨租户 403/404、IDOR、admin reset、dismiss/restore 幂等、并发首访恰一行。

### Task 6: 前端 onboarding 模块(TDD)
- types/api/realtime/useOnboarding(仿 useInboxContext 派生 workspace/member;WS 订阅 member:{member}:onboarding;30s 轮询降级)。
- OnboardingChecklist:进度条(success token)、五步勾选(✓ + 文字,非颜色唯一信号)、CTA 深链(成员名册 / 看板 / 收件箱等既有入口)、来源角标、首未完成步高亮、dismiss、aha 庆祝卡(文字 + 图标 + 深链收件箱,reduced-motion 安全)、dismissed/全完成隐藏。
- 六页空状态四要素(插画 SVG + §1.2.2 文案键 + 主操作深链 + 推进步骤乐观 UI);帮助菜单 restore 入口 + 命令面板;成员页 admin 重置入口。
- i18n `onboarding.*` + 空状态新键(en/zh parity,djb2 重算);Vitest 覆盖 ≥90%。

### Task 7: 真栈 UI 验证 + 文档 + PR
- docker compose 起全栈 + Quick Start 跑通;Playwright 真栈 spec(清单渲染/深链/空状态/dismiss/restore/庆祝)+ 证据截图唯一性(check-evidence-unique)。
- README 状态行 + CHANGELOG 0.18.0;`tests/docs/check_event_vocab.py` / `check_roster_entry.py` 通过;ruff + 前端 lint/typecheck 通过。
- 覆盖率双达标实测;rebase main;纯净提交(cnwenf 身份,无 co-author);PR;完工评论 @验收员。

## Self-Review

- Spec 五章逐条:§1 功能点 O1–O9 → Task 2/3/6;§2 数据模型 → Task 1;§3 接口/幂等/消费/实时 → Task 3/4;§4 UI/UX → Task 6;§5 验收 → Task 5/7。T34 四场景 → Task 5 逐场景。R3/R4 归属写死 → Task 2(reconcile)+ Task 3(消费)。
- 占位符扫描:无 TBD;关键 SQL/载荷形状/钩子位置均在「关键实现事实」中固化。
- 类型一致性:`consume_realtime_event(session, event)` / `seed_checklist(session, workspace_id, member)` / `onboarding_channel(member_id)` 全计划统一。
