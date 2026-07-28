# MES-66 Autopilot R3 整改计划(验收员两轮合并清单 M1–M5)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:writing-plans(本文件即其归档)→ test-driven-development(每条修复先写失败回归用例)→ systematic-debugging → verification-before-completion(实测门禁,逐文件贴覆盖率,不估算)→ requesting-code-review(完工请验收员 R3 复验)。

**Goal:** 闭合验收员第 1/2 轮权威合并清单的全部阻塞项(M1–M5),rebase 最新 main 解除合入冲突,补齐必查项证据(计划归档、实测覆盖率、真实 e2e 红线),使 PR #53 达到第 3 轮复验可放行状态。

**Context:** R1/R2 已确认核心安全红线全过(数据模型 §2 逐字对齐、REST §3.1、入站 Webhook HMAC/去重/防预占、审批 §6.10、定时原子抢占、前端 H1–H3);R2 打回原因为 4 项后端/流程阻塞(M1–M4)零改动 + 1 项新增覆盖率回退(M5)。

## 必修项与设计决策

### M1 — 切断 `create_issue ↔ issue_created` 自环(§1.1 / §5.3 / P11)

- **根因**:matcher 级联谱系仅对 comment 产物回溯(`_parent_run_for_comment` 只查 `artifact_type='comment'`);issue 类触发 `cascade_depth` 恒 0(每轮生成**新** issue,`loop_target` 也恒新),两道抗环机制对该环均失效,仅 rate_limit/daily_budget 兜底。
- **修法**(`matcher.py`):提取通用 `_parent_run_for_artifact(artifact_type, ref_table, ref_id)`,comment/issue 各为薄包装;issue 类触发(`issue_created`/`issue_status_changed`/`issue_field_changed`)经 `artifact_type='issue', ref_table='issues'` 回溯 `parent_run_id`/`cascade_depth+1`(executor 的 `create_issue` 动作已记录该产物,接线即可)。深度沿产物链累加直至 `cascade_max_depth` 拒建下游 run。
- **回归用例**:① 单元(`test_autopilot_matcher.py`):issue 产物锚点 → 子 run `parent_run_id` 接线、`cascade_depth=父+1`;下调 `cascade_max_depth` 后同事件不再建 run(链被截断)。② 真实 e2e(`test_autopilot_e2e.py`):`issue_created` 触发 + `create_issue` 动作(关审批门、`rate_limit_max=50`/`concurrency_limit=10` 排除兜底干扰)→ 恰 4 个 run(深度 0..3)、稳定后不再增长、仅种子 run 无 parent——证明是级联截断而非频率兜底。

### M2 — rebase 最新 main 解冲突(阻塞必查项 8 合入主干)

- `git rebase origin/main`;冲突文件:`CHANGELOG.md`(双方各占 0.16.0 → autopilot 条目重编号 0.16.2/0.16.3)、`README.md`(状态表双方行并存)、`backend/src/mesh/api/app.py`(skill + autopilot 路由并存)、`frontend/src/i18n/catalogs/{en,zh-CN}.json`(键集取并集,djb2 版本哈希按 `catalogLoader.computeCatalogVersion` 语义——UTF-16 码元遍历——重算,en/zh 键集 parity)。
- **迁移重编号**(后合方惯例):autopilot 迁移 `0020 → 0021`(`down_revision="0020"`,链于 main 的 `0020_skill` 之后,单 head 链 0001→0021);同步 README/CHANGELOG/代码注释中的迁移号引用。
- 验收:`gh pr view 53 --json mergeable,mergeStateStatus` = MERGEABLE / CLEAN。

### M3 — `run_all` 默认并发下补跑失效(§4.5)

- **根因**:scheduler 逐 slot 调 `evaluate_trigger`,同事务内已建 pending run 被 `_in_flight_count` 计为在途,`concurrency_limit=1` 默认下第 2 个 slot 起全被 `concurrency_limited` 拒——「每个错过槽位一次运行」名存实亡(旧测试以 `concurrency_limit=100` 掩盖)。
- **修法**:`evaluate_trigger` 增 `bypass_concurrency: bool=False`;scheduler 对 run_all catch-up slot(首槽之外)置 True——**仅豁免触发期并发闸门**,频率/去重/成环/级联/预算护栏照常(执行期仍由 executor 串行化)。
- **回归用例**:① 闸门级:在途占满时 `bypass_concurrency=True` 放行、默认拒。② 调度级:**默认 `concurrency_limit=1`** 下 N=4 个到期 slot 产 4 run。

### M4 — 回环检测键错配(§2.6 / §5.3)

- **根因**:`_loop_hit` WHERE 按 `autopilot_id` 过滤,跨规则同 executor×同目标互提漏检。
- **修法**:键改为 `(executor_agent, target)`——JOIN `autopilots` 按 `executor_agent_id`(同租户)过滤 + `trigger_snapshot.loop_target` 窗口匹配;`evaluate_trigger` 传 `workspace_id` 取代 `autopilot_id`。
- **回归用例**:规则 A(agent X)对 target T 有窗内 run → 规则 B(不同规则、同 agent X)对同 T 触发被 `agent_loop_detected` 拒;另一 agent 对同 T 放行(非误伤)。

### M5 — 新增代码覆盖率回退(`service.py` 89%)

- **根因**:`list_webhook_events` 游标/`has_more` 分支无测试。
- **修法**:补「插入 >limit 行 → 首页 `next_cursor` 非空 → 跟翻页 <limit 行 → `next_cursor=None`」用例,覆盖 `service.py` 游标 WHERE 与 `encode_cursor` 分支;`pytest --cov` 实测逐文件 ≥90% 并在完工评论逐文件贴数(不估算)。

## 一并处理(R1/R2 LOW,如实取舍)

| 项 | 处置 |
|---|---|
| 坏配置 `tz` 静默回退 UTC | scheduler 去掉 `or "UTC"` 兜底:缺/非法 cron/tz → 抢占置 `next_run_at=NULL` 停泊(不再扫到)+ error 日志,不静默猜时区;补单测 |
| 非 dry test-run 绕过护栏 | test-run 实际执行路径前置 kill switch 闸门(`kill_switch_paused` → 409 `kill_switch`);dry_run 无副作用保留可用;补单测 |
| `executor_busy` 503 与 §3.3 | 对齐 Spec 侧:§3.3 表注明其为**执行层**可重试错误分类(动作失败归因运行时繁忙按退避重试),触发期并发满走 429 `concurrency_limited` |
| `filterPayloadMatch` JSON.parse 未守卫 | `buildPayload` 捕获非法 JSON/非数组 → `PayloadMatchInvalidError` → 保存 catch 显示专用 i18n toast(`autopilots.editor.payloadMatchInvalid`,双语目录 + djb2 重算);补组件用例(非法输入 → 专用 toast + 不提交) |
| 编辑器顶部注释与新预览行为矛盾 | 头注改为「cron/时区变更防抖重算,经无状态 preview-schedule 端点,新建态可用,非法式提示无效」 |
| `dedup_key_template` 存而不渲染 | 取舍说明:模板默认 `{{trigger.event_id}}` 的实例化由各触发路径在闸门处具体产出(matcher `{event.id}:{rule.id}` / scheduler `schedule:{rule}:{slot}` / webhook 事件 id 或 `rejected:<hash>`),规则作用域由闸门 `autopilot_id` 过滤保证;在 `DEFAULT_GUARDRAILS` 落注释固化该契约,不改键构造(避免破坏在途去重语义) |
| `payload_match` 端到端补测 | 既有 `test_inbound_event_type_filter_and_payload_match`(真实 `process_inbound` → 路由匹配/不匹配分流)即 webhook 载荷端到端覆盖;域事件路径 payload_match 按 R1 结论 fail-closed(webhook 载荷专用),注释说明 |
| 出向 SSRF DNS-rebinding TOCTOU | 取舍保留:§5.3 仅要求拒私网段(已满足:私网/环回/link-local/元数据/多播/保留全拒 + https-only + 白名单 + 禁重定向);钉死解析 IP 直连需移植 runtime 模块的 pinned-connection 设施,列为后续加固项,完工评论如实说明 |
| 入队幂等键与 §6.5 字面 | R1 已确认「为 §4.4 重试需新 execution 而合理」;代码注释维持现状说明,不改构造 |

## 文件结构

| 文件 | 变更 |
|---|---|
| `backend/src/mesh/autopilot/matcher.py` | M1:`_parent_run_for_artifact` 通用谱系 + `_parent_run_for_issue`;issue 触发接线;模块 docstring |
| `backend/src/mesh/autopilot/guardrails.py` | M4:`_loop_hit` 键改 `(executor_agent, target)`(JOIN 规则);M3:`evaluate_trigger(bypass_concurrency)`;`dedup_key_template` 契约注释 |
| `backend/src/mesh/autopilot/scheduler.py` | M3:catch-up slot 豁免并发闸门;LOW:坏配置停泊(去 UTC 静默兜底) |
| `backend/src/mesh/autopilot/service.py` | LOW:test-run kill switch 闸门 |
| `backend/migrations/versions/0021_autopilot.py` | M2:`0020 → 0021` 重编号(`down_revision="0020"`) |
| `backend/tests/unit/test_autopilot_{matcher,guardrails,scheduler,service}.py` | M1/M3/M4/M5 + LOW 回归用例 |
| `backend/tests/e2e/test_autopilot_e2e.py` | M1:`test_create_issue_self_loop_is_cut_by_cascade_depth` |
| `frontend/src/features/autopilots/AutopilotEditorPage.tsx` | LOW:`PayloadMatchInvalidError` + 专用 toast + 头注修正 |
| `frontend/src/features/autopilots/__tests__/AutopilotEditorPage.test.tsx` | LOW:非法 payload_match 组件用例 |
| `frontend/src/i18n/catalogs/{en,zh-CN}.json` | M2 键集并集 + `payloadMatchInvalid` 键 + djb2 重算 |
| `CHANGELOG.md` / `README.md` / `docs/specs/features/autopilot.md` | 版本重编号(0.16.2/0.16.3)、迁移号、§3.3 `executor_busy` 口径 |

## 验证(verification-before-completion,实测不估算)

- [ ] 后端 `pytest tests/unit --cov=mesh`:整体 ≥90% 且 autopilot 逐文件 ≥90%(重点 `service.py` 回 ≥90%),完工评论贴 `--cov` 实测逐文件数。
- [ ] 后端真实 e2e(`tests/e2e/test_autopilot_e2e.py`,真实 api + worker + PostgreSQL):原 9 项红线 + 新增 create_issue 自环截断,全绿。
- [ ] 前端 `vitest run --coverage`:全量绿 + per-file 90% 门禁(`verify-perfile-coverage.mjs`)通过;djb2 哈希与 `computeCatalogVersion` 一致、en/zh parity。
- [ ] `gh pr view 53 --json mergeable,mergeStateStatus` = MERGEABLE / CLEAN。
- [ ] 匿名化:diff/提交/分支无参考来源字样;提交身份 `cnwenf <cnwenf@outlook.com>`、无 Co-Authored-By(`core.hooksPath /dev/null` + push 前 grep 自查)。
- [ ] 完工评论附 PR 最新 SHA + 实测覆盖率,mention 验收员请求 R3 复验。
