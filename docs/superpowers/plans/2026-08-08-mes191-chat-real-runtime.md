# MES-191 聊天接入真实 runtime 执行链路 实施计划

> Issue: MES-191【聊天页】聊天不触发真实执行（恒回模板文案）+ 消息交替展示对齐
> 分支: `agent/mesh/mes191-chat-real-runtime`
> 日期: 2026-08-08
> 状态: 进行中

## 1. 问题与根因

**现象**：与 agent 聊天恒回同一模板文案（"收到你的问题:…初步建议:…"），不触发真实执行；聊天消息交替展示需对齐。

**根因（已确认）**：
1. `backend/src/mesh/chat/engine.py::ScriptedGenerationProvider` 是进程内占位生成器，返回常量模板。
2. `backend/src/mesh/runtime/claim.py` 第 151-156 行将 `TaskExecution.trigger != "chat"` 写入 daemon 认领过滤条件——聊天入队的执行永远不会被 daemon 认领。
3. `ChatGenerationEngine.schedule()` 在 API 进程内直接跑占位生成循环，绕过 runtime 链路。

真实 runtime 链路（issue 执行已验证可用）：`execution.enqueue` outbox → `task_executions` 行（queued）→ daemon `POST /api/v1/daemon/runtimes/{id}/executions:claim` → attempt（lease_seq fencing）→ daemon provider（Claude Code）→ `TextDelta` → `LogUploader.submit("stdout", text)` → `POST /api/v1/daemon/attempts/{id}/logs` → 终态 `PATCH /api/v1/daemon/attempts/{id}`。

**关键事实**（研究阶段确认）：
- daemon `LogUploader.submit` 把每个 TextDelta chunk 原样作为一行日志追加（`buf.append(result.text)`），不按换行拆分——聊天内容 = stdout 行拼接，无损。
- `serialize_untrusted_context` 对纯字符串原样透传；daemon 包在 `<<<mesh-untrusted-context>>>` 围栏内，经 stdin 投递。
- daemon 固定 provider 路径 `BudgetLimits.from_snapshot(..., require_usd=True)`：`budget.max_cost_usd` 为 None 会抛错——聊天快照必须保证有值。
- `issue_task_token(issue_id=None)` 合法（聊天无需 issue 级 token）。
- SSE 协议：POST 建消息返回 `{message_id, generation_id, stream_url}`；`generation_event_stream` 从 Redis Stream `chat:gen:{id}:events` 回放 + pubsub 跟随，终态事件 `message.done/message.interrupted/error`。

## 2. 设计（已与需求对齐）

聊天执行与 issue 执行走同一条真实 runtime 链路；差异仅在：
- `trigger="chat"`、`issue_id=None`（隐私 + 天然绕过 issue 观测副作用，如上下文 issue 自动翻 in_progress）；
- stdout 日志行额外镜像为 SSE `message.delta` 帧（写 Redis Stream/pubsub，不进日志存储语义）；
- 执行终态同事务收口 chat_message（内容 = 缓冲拼接，回退 result.summary）；
- 不发 `executions` workspace 频道帧、不发失败通知、不进 result_sink/squad（已天然跳过 chat）。

### 2.1 后端改动清单

| # | 文件 | 改动 |
|---|------|------|
| 1 | `backend/src/mesh/chat/prompt.py`（新增） | 提示词组装：system 消息行插入（保留原副作用）、历史（parent 之前的 done+selected 轮次，≤16）、issue 上下文围栏（随机 token）、最新用户消息 → 组成 untrusted-context 字符串 |
| 2 | `backend/src/mesh/chat/service.py::_enqueue_execution` | 真实 `config_snapshot`（经 `build_config_snapshot`：agent.model_config provider/model/reasoning_effort、system_instructions、budget 保证 `max_cost_usd` 有值——聊天默认常量补齐）；`task_spec={kind:"chat_generation", session_id, message_id, generation_id, untrusted_context:<组装好的提示词>}`；`max_attempts=1`；`issue_id=None` |
| 3 | `backend/src/mesh/runtime/claim.py` | 删除 `TaskExecution.trigger != "chat"` 排除行；重写注释（聊天执行同样可被 daemon 认领） |
| 4 | `backend/src/mesh/runtime/logs.py::append_log_lines` | 新增可选 `redis` 参数；持久化后若执行 `trigger=='chat'`：仅 stdout 行镜像为 `message.delta` 帧（首次 SETNX 守卫补一条 `message.created`） |
| 5 | `backend/src/mesh/runtime/daemon_routes.py` | `append_logs`/`patch_attempt` 把 `request.app.state.redis` 传入 |
| 6 | `backend/src/mesh/runtime/attempts.py::transition_attempt` | 终态分支：若 chat 触发 → 同事务 `finalize_chat_generation`（条件 UPDATE generation_status='streaming'、content=缓冲拼接/回退 summary、session last_message_*、自动标题、outbox 终态 realtime 帧）；提交后 best-effort 直写 Redis SSE 终态帧（SETNX 守卫）；新增 redis 参数 |
| 7 | `backend/src/mesh/workers/main.py` | `execution.finished` 组合处新增 chat 安全网 handler（覆盖 reaper/cancel_in_flight/freeze 路径，同一幂等收口 + SETNX 帧） |
| 8 | `backend/src/mesh/chat/service.py::stop_generation` | 保留条件 DB 翻转 interrupted（L4 不缩短规则）+ realtime；`chat.generation_finished` emit 改为对该 generation 的执行行 `request_execution_cancel_tx`；直写 SSE interrupted 帧 |
| 9 | `backend/src/mesh/chat/service.py::_assert_generation_slot` | 陈旧回收（>600s）去掉 `CHAT_GENERATION_FINISHED_EVENT` emit，改为尽力取消卡住的执行 |
| 10 | `backend/src/mesh/runtime/enqueue.py` | 删除 `CHAT_GENERATION_FINISHED_EVENT`/`_CHAT_FINISH_STATUS_MAP`/`chat_generation_finished_handler`；chat trigger 跳过 workspace executions 频道帧 |
| 11 | `backend/src/mesh/chat/engine.py` | 删除 `ScriptedGenerationProvider`/`GenerationProvider`/`run()`/`schedule()`/`_build_prompt`/`_issue_context_snapshot`/`_finalize*`/`_emit_terminal`；保留缓冲原语（`append_frame` 改 INCR 原子自增 seq、`replay_frames`、`buffered_content`、`request_stop`、key 模板、频道、幂等键助手） |
| 12 | `backend/src/mesh/api/app.py` + `backend/src/mesh/config.py` | 引擎重新接线（无 provider）；删 `chat_generation_chunk_delay_seconds` 设置 |
| 13 | `backend/src/mesh/runtime/attempts.py::_emit_terminal_notification` | chat trigger 抑制失败通知 |

daemon 零改动。

### 2.2 前端改动清单

| # | 文件 | 改动 |
|---|------|------|
| F1 | `src/features/chat/components/SessionListPanel.tsx` | 底部归档区（置顶 → 最近 → 底部归档区），替换现有 status 下拉筛选（research §660） |
| F2 | 走查校验 | 交替气泡/流式打字机/历史会话在真实 e2e 中目检（后端修复后） |

### 2.3 文档

- `docs/specs/features/chat-session.md` §4.4 重写为真实 runtime 设计；§1.3/§3.3 引擎措辞同步。
- README 相关小节核对更新。
- `docs/api/openapi.yaml` 若契约变化则重生成（预计无端点增删）。

## 3. 实施顺序（TDD）

1. **prompt.py + service 入队真实快照/task_spec**（单测先行：快照含 max_cost_usd、untrusted_context 组装、issue_id=None、max_attempts=1）
2. **claim.py 放开 chat**（test_runtime_claim 改判：chat 执行可认领）
3. **logs.py 镜像 + daemon_routes 接线**（chat 执行 stdout 行→delta 帧；stderr 不镜像；非 chat 不镜像；message.created 只一次）
4. **attempts.py 终态收口 + worker 安全网**（同事务 finalize、幂等、SETNX 帧、summary 回退、失败通知抑制）
5. **stop_generation 改造 + slot 回收**（cancel_tx 接线、直写 interrupted 帧）
6. **删除占位机制**（ScriptedGenerationProvider/run/schedule/chat.generation_finished 全删，app.py 重接线，config 清理）
7. **前端 F1 归档区**（vitest + per-file 门禁）
8. **真实 e2e**：真实 PostgreSQL/Redis 起服务 → 真实 API 发消息 → 校验执行行创建（trigger=chat, queued）→ 模拟 daemon 认领/日志/终态 → SSE 帧序列 + DB 落库校验；既有 test_chat_e2e 改写
9. **门禁**：pytest-cov 总体 + 新增代码 ≥90%；前端 typecheck/lint/build/覆盖率/per-file/8 项 spec-check 全绿；openapi 契约
10. **文档** + 交付评论 @Mesh 验收员，状态 in_review

## 4. 风险与对策

- **重复 delta**（daemon 重试/日志重放）：chat 执行 `max_attempts=1`；`append_log_lines` 本身 offset 连续去重。
- **终态竞态**（stop vs 自然完成）：DB 条件 UPDATE（generation_status='streaming'）+ SETNX 帧守卫双幂等。
- **快照预算闸**：聊天 budget 常量保证 `max_cost_usd` 非 None（配置缺失即默认值兜底，生产可调）。
- **relay 延迟**：worker 安全网路径经 outbox relay（≤1s），可接受；daemon PATCH 直连路径同事务收口无延迟。
