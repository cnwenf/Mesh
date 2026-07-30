# DingTalk 出站与互动卡片(ack · OpenAPI · 卡片生命周期)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Mesh 钉钉连接器的出站与互动卡片切片(MES-89,integrations.md §3.8/§3.10/§4.4/§5.6):accessToken 多副本单飞出站适配器、群/单聊消息发送、at-most-once ack 确认(leading-edge 合并)、互动卡片投放/更新/回调鉴权与生命周期、verbosity 与长文分段、测试出站与接收诊断分离。

**Architecture:** 新增四个聚焦模块 —— `dingtalk_api.py`(OpenAPI 传输层:令牌单飞 + 消息/卡片 API + 错误分类 + 脱敏)、`im_outbound.py`(出站语义层:用户键编码、分段、verbosity、im.send 消费与 notification_delivery 台账)、`ack.py`(§3.8 窗口选主 + T1/T2 快 relay)、`dingtalk_cards.py`(卡片投放/回调/生命周期);复用既有 outbox relay(`RetryableDelay`/`available_at`)、cards.py 鉴权链、notification fanout 与凭据保险箱。所有外部副作用经 outbox(`im.send` 内部事件),入队侧选主函数为 MES-88 队列流程预留唯一集成点。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 async / asyncpg / PostgreSQL 16 / redis.asyncio / httpx / pytest + pytest-cov(≥90%)/ Alembic(本切片无新表,DDL 已在 0033)。

## Global Constraints

- Spec 权威:`docs/specs/features/integrations.md`(§3.8 ack / §3.10 出站 / §4.4 卡片生命周期 / §5.6 验收)+ README §6.5/§6.6/§6.10/§6.13/§6.16;行为冲突以 Spec 为准,发现缺漏先反馈 Leader,不擅改需求。
- UT 覆盖率 ≥90%(整体与新增代码,`pytest --cov=mesh --cov-fail-under=90`);每个端点真实 e2e(真实起服 + 真实 worker 子进程 + 本地钉钉 OpenAPI 测试替身,非 mock 走过场)。
- 合规零参考来源:代码/注释/文档/提交信息/分支名不得出现任何暴露参考来源的字样。
- Git 提交 author/committer 一律 `cnwenf <cnwenf@outlook.com>`;提交信息绝无 co-author 署名行;`core.hooksPath=/dev/null`。
- 凭据只存密文(`secret_ref`,同 `runtime_credentials` 契约);解密值进 `redact_in_logs` 黑名单;accessToken/appSecret 永不回显响应/日志(出站失败台账仅 method/url/status)。
- 外部可见副作用一律经 outbox(README §6.6);`im.send` 为内部 outbox 事件类型(非 §6.7 实时事件名,不进 vocab)。
- 钉钉出站目标固定平台官方域(`api.dingtalk.com`/`oapi.dingtalk.com`);测试替身基址仅部署期环境变量(`MESH_DINGTALK_API_BASE`/`MESH_DINGTALK_OAPI_BASE`),不经运行期配置/管理 API 可读写。
- 测试运行于专用库:`MESH_TEST_DATABASE_URL=postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/mesh_test_mes89`、`MESH_TEST_REDIS_URL=redis://127.0.0.1:6390/3`(避开共享库的迁移状态漂移)。
- 跨切片契约(MES-87 接收 / MES-88 队列):本切片只暴露稳定服务函数,不改 `inbound.py` 摄取流;集成点在 PR 描述中逐条列明。

## File Structure

**Create:**
- `backend/src/mesh/integrations/dingtalk_api.py` — OpenAPI 传输层:13 msgKey 目录、限流错误码集、请求体脱敏、`DingTalkTokenManager`(Redis 共享缓存 + 随机 owner 锁 + Lua 释放 + follower 有界等待 + busy/invalid 分类)、`DingTalkClient`(群/单聊发送、卡片 createAndDeliver/update/streaming、令牌失效强制刷新一次、错误分类异常)。
- `backend/src/mesh/integrations/im_outbound.py` — 出站语义层:外部用户键编码(`staffId` 直通 / `x=<base64url(senderId)>`)、markdown 分段(段落/代码块边界、UTF-8 安全)、`DingTalkIMAdapter`(会话消息 + 通知投递:verbosity 闸门、分段幂等、超 `MESH_IM_MAX_CHUNKS` 截断 + 站内深链)、`IMSendRelay`(消费全部 `im.send`:ack 走 T1/T2、notification 分段计数回写台账、feedback 简单发送)、`derive_im_deliveries_from_fanout`(链入 `notification.fanout`:integration 触发执行的通知 → `notification_delivery(channel='im')` 台账 + 分段/卡片 im.send 事件)。
- `backend/src/mesh/integrations/ack.py` — §3.8:`elect_ack_leader`(入队事务内按锁序 `ack_window_at` 选主、follower 不写事件、`ack_template=''` 跳过全部)、`position_hint`、`IM_SEND_EVENT_TYPE` 常量与载荷契约。
- `backend/src/mesh/integrations/dingtalk_cards.py` — 互动卡片:`derive_out_track_id`(approval 派生)、`open_space_id`(IM_GROUP/IM_ROBOT)、`build_approval_card_data`(§4.4 字段)、`assert_not_action_card`(审批卡路径严禁 sampleActionCard6,代码断言)、`push_approval_card`(createAndDeliver + callbackType)、生命周期状态表(loading/终态禁用/重复/过期/无权/失败/[回 Mesh 处理])、`handle_dingtalk_card_callback`(userId staffId 归一 → external_identities → members → decide_approval → cardUpdateOptions/userPrivateData 回写;未映射/无权 403 + 审批不变 + 留痕;重复点击幂等)、HTTP 回调签名校验(`verify_callback_signature`,§3.2 钉钉行,±3600s)。
- `backend/tests/unit/test_dingtalk_api.py`
- `backend/tests/unit/test_im_outbound.py`
- `backend/tests/unit/test_ack.py`
- `backend/tests/unit/test_dingtalk_cards.py`
- `backend/tests/unit/integrations_dingtalk_support.py` — 共享 fake:可注入 httpx.MockTransport 的钉钉 OpenAPI 替身(accessToken 计数、限流码注入、卡片态记录)+ 世界播种辅助。
- `backend/tests/e2e/test_dingtalk_outbound_e2e.py` — 真实 e2e:真起 API + 真 `python -m mesh.workers` 子进程 + 本地钉钉 OpenAPI 测试替身(`ThreadingHTTPServer`,`MESH_DINGTALK_API_BASE` 指向之)。
- `backend/tests/e2e/dingtalk_fake_server.py` — e2e 用钉钉 OpenAPI 测试替身(线程安全请求计数、可脚本化响应)。

**Modify:**
- `backend/src/mesh/config.py` — 新增 settings(env 名对齐 Spec):`im_ack_coalesce_window`(5.0)、`token_follower_wait`(12.0)、`im_max_chunks`(5)、`im_ack_send_timeout`(3.0)、`im_send_poll_interval`(0.2)、`im_send_batch_size`(20)、`im_delivery_max_attempts`(5)、`im_rate_limit_base_seconds`(2.0)、`im_rate_limit_max_seconds`(60.0)、`dingtalk_api_base`、`dingtalk_oapi_base`、`dingtalk_token_refresh_timeout`(10.0)、`dingtalk_token_lock_ttl`(30)、`dingtalk_request_timeout`(10.0)。
- `backend/src/mesh/runtime/credentials.py` — `load_redaction_blacklist` 增补:同工作区活跃 `integrations.secret_ref` 解密值进黑名单(集成凭据解密值登记 redact_in_logs)。
- `backend/src/mesh/integrations/cards.py` — 抽出共享鉴权链辅助 `resolve_clicker_member(session, *, provider, tenant_key, external_user_key, workspace_id)`(供钉钉卡片复用);`extract_clicker` 增 `im_dingtalk` 分支。
- `backend/src/mesh/integrations/routes.py` — `POST /workspaces/{ws}/integrations/{id}/test-send`(出站诊断,失败 502 upstream_error,绝不 503)、`GET /workspaces/{ws}/integrations/{id}/stream-status`(只读 `stream_state`;down → 503 stream_channel_unavailable,disabled/其他 → 200)。
- `backend/src/mesh/integrations/inbound_routes.py` — `POST /api/v1/integrations/dingtalk/cards`(callbackType='HTTP' 的卡片回调;签名校验同 §3.2 钉钉行;裸 JSON 响应含 cardUpdateOptions;与 MES-87 的 `/dingtalk/events` 互不相干)。
- `backend/src/mesh/workers/main.py` — 新增受监督任务 `im-send-relay`(IMSendRelay);`FANOUT_EVENT_TYPE` 处理链追加 `derive_im_deliveries_from_fanout`(先基线 fanout 后派生 IM 台账)。
- `backend/README.md` — worker 任务表增 `im-send-relay`;集成模块说明补出站/卡片。
- `README.md` — 模块总表 integrations 行追加本切片能力(版本随发布)。
- `CHANGELOG.md` — 新版本节(与发布号对齐,先看现存最高版本)。

**Cross-slice contracts(本切片暴露、他人消费,PR 描述列明):**
- MES-88 入队事务(持 `imq_seq` 咨询锁、已取 `ack_window_at=clock_timestamp()`)在 INSERT 队列项后调用 `await elect_ack_leader(session, item=item, integration=..., now=...)` —— 唯一 ack 集成点。
- MES-88 命令平面反馈经 `emit_event(..., event_type='im.send', payload={kind:'feedback', ...}, idempotency_key=<唯一>)` 发送,本切片 IMSendRelay 负责投递。
- MES-87 Stream worker 将 topic `/v1.0/card/instances/callback` 帧载荷交 `handle_dingtalk_card_callback(...)`;HTTP 卡片回调经本切片 `/integrations/dingtalk/cards` 端点(签名函数 `verify_callback_signature` 可被 MES-87 的 `dingtalk_verify` 复用,避免双实现)。

---

### Task 1: Settings + 钉钉 API 常量/脱敏(地基)

**Files:**
- Modify: `backend/src/mesh/config.py`(webhook 块之后追加 IM/钉钉块)
- Create: `backend/src/mesh/integrations/dingtalk_api.py`(本任务仅常量 + 异常 + 脱敏)
- Test: `backend/tests/unit/test_dingtalk_api.py`(先建,常量/脱敏用例)

**Interfaces:**
- Produces(后续任务依赖):
  - `MSG_KEYS: frozenset[str]`(13 个:`sampleText`,`sampleMarkdown`,`sampleImageMsg`,`sampleLink`,`sampleAudio`,`sampleVideo`,`sampleFile`,`sampleActionCard`,`sampleActionCard2..6`)
  - `RATE_LIMIT_CODES: frozenset[str]`(`send.too.fast`,`too.many.group`,`too.many.people`,`send.byToken.tooFast`)
  - `INVALID_TOKEN_CODES: frozenset`(`40014`,`88`,`invalidAuthentication`)
  - `class DingTalkError(Exception)`(基类,`code`/`http_status`)、`class TokenRefreshBusy(DingTalkError)`、`class InvalidCredentials(DingTalkError)`、`class DingTalkRateLimited(DingTalkError)`(`retry_hint`、`flow_controlled_staff_ids: tuple[str,...]`)、`class DingTalkUpstreamError(DingTalkError)`
  - `redact_body_for_log(body: dict | None) -> dict`(结构性将 `appSecret`/`clientSecret`/`accessToken` 值替换 `***`)
  - `REDACT_HEADERS: frozenset`(`x-acs-dingtalk-access-token`)
- Consumes: `mesh.config.Settings` 新字段(MESH_ 前缀自动映射)。

**核心实现(脱敏):**
```python
_SENSITIVE_BODY_KEYS = frozenset({"appSecret", "clientSecret", "accessToken"})

def redact_body_for_log(body: dict | None) -> dict:
    if not isinstance(body, dict):
        return {}
    return {k: ("***" if k in _SENSITIVE_BODY_KEYS else v) for k, v in body.items()}
```

- [ ] **Step 1:** config.py 增字段(Field 默认值 + ge 约束如上 Global Constraints 所列;`dingtalk_api_base`/`dingtalk_oapi_base` 默认官方域)。
- [ ] **Step 2:** 写失败测试:`test_msg_keys_are_the_13_official_types`(精确集合断言)、`test_rate_limit_codes`、`test_redact_body_replaces_secrets_only`(非敏感键不动、缺键不报错、非 dict 返回 {})、`test_settings_defaults`(load_settings 后逐字段断言)。
- [ ] **Step 3:** `pytest tests/unit/test_dingtalk_api.py -q` 确认失败(模块缺失)。
- [ ] **Step 4:** 实现常量/异常/脱敏 → 测试转绿。
- [ ] **Step 5:** 提交 `feat(integrations): 钉钉出站地基——settings + msgKey 目录 + 请求体脱敏(MES-89)`。

---

### Task 2: DingTalkTokenManager(多副本单飞,§3.10 核心)

**Files:**
- Modify: `backend/src/mesh/integrations/dingtalk_api.py`
- Test: `backend/tests/unit/test_dingtalk_api.py`(追加)、`backend/tests/unit/integrations_dingtalk_support.py`(建 fake)

**Interfaces:**
- Produces:
  - `class DingTalkTokenManager(redis, *, http_client, app_key, app_secret, integration_id, api_base, refresh_timeout=10.0, lock_ttl=30, follower_wait=12.0, clock=None, jitter=None)`
  - `async def get_token() -> str`(本地 LRU ≤30s → Redis 共享键 → 刷新协议;过期窗 ≤5 分钟触发主动刷新)
  - `async def invalidate() -> None`(平台失效码后作废旧缓存)
  - 共享键 `dingtalk:access_token:<integration_id>` 值 JSON `{token, expires_at}` TTL `7200-300±60s`;锁键 `dingtalk:token_lock:<integration_id>` 值随机 owner token。
- Consumes: Task 1 异常类;`redis.asyncio.Redis`;注入的 `httpx.AsyncClient`(测试 MockTransport)。

**关键算法(严格对齐 §3.10):**
```python
REFRESH_LUA_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
# leader: SET lock <owner> NX EX 30 → 抢到 → 双检 Redis expires_at 仍临近
#   → POST /v1.0/oauth2/accessToken {appKey, appSecret}(超时 10s < 租约 30s)
#   → SET 共享缓存(TTL 7200-300+jitter(±60)) → EVAL Lua 条件释放(仅 owner 匹配)
# follower: 500ms 双检重读共享缓存,循环至 follower_wait(12s)→ 耗尽 → 重抢一次
#   → 仍未得 → raise TokenRefreshBusy(可重试,不终态)
# 刷新端点 invalidAuthentication/errcode ∈ INVALID_TOKEN_CODES → InvalidCredentials(终态)
# 网络/5xx → DingTalkUpstreamError(relay 失败预算)
```

- [ ] **Step 1:** 建 `integrations_dingtalk_support.py`:`FakeDingTalkPlatform`(记录 accessToken 调用次数、可注入延迟/错误码的 MockTransport handler)+ 世界播种辅助(工作区/成员/集成/绑定,复用 `integrations_support.py` 模式)。
- [ ] **Step 2:** 失败测试(真 Redis db3):
  - `test_token_cached_in_redis_with_ttl_jitter_bounds`(TTL ∈ [7200-300-60, 7200-300+60])
  - `test_local_lru_short_circuits_redis`(30s 内不再读 Redis)
  - `test_concurrent_refresh_single_flight`(两个 manager 并发 get_token → 平台端点恰 1 次)
  - `test_follower_waits_and_gets_token`(leader 注入 2s 延迟 → follower ≤~2.5s 得令牌、零失败、端点恰 1 次)
  - `test_follower_wait_exhausted_raises_busy`(leader 延迟 > follower_wait(测试用小值)→ TokenRefreshBusy)
  - `test_lease_takeover_and_stale_owner_release_refused`(模拟持锁者"崩溃"——占锁后不刷新直至租约过期(测试用 ttl=1s)→ 第二副本接管刷新成功 → 旧 owner 迟到释放被 Lua 拒绝(锁值仍为新 owner 或已随新流程正确释放,断言 DEL 未误删新锁))
  - `test_invalid_credentials_terminal`(刷新端点 errcode=40014 → InvalidCredentials)
  - `test_refresh_timeout_under_lease`(实现断言:refresh_timeout < lock_ttl)
- [ ] **Step 3:** 实现 TokenManager → 转绿。
- [ ] **Step 4:** 提交 `feat(integrations): 钉钉 accessToken 多副本单飞——随机 owner 锁 + Lua 释放 + follower 有界等待(MES-89 §3.10)`。

---

### Task 3: DingTalkClient(消息发送 + 错误分类 + 失效强刷)

**Files:**
- Modify: `backend/src/mesh/integrations/dingtalk_api.py`
- Test: `backend/tests/unit/test_dingtalk_api.py`(追加)

**Interfaces:**
- Produces:
  - `class DingTalkClient(token_manager, *, http_client, api_base, robot_code, request_timeout=10.0)`
  - `async def send_group(open_conversation_id: str, msg_key: str, msg_param: dict) -> dict` → POST `/v1.0/robot/groupMessages/send` `{robotCode, openConversationId, msgKey, msgParam: json(msg_param)}`
  - `async def send_direct(user_ids: list[str], msg_key: str, msg_param: dict) -> dict` → POST `/v1.0/robot/oToMessages/batchSend` `{robotCode, userIds, msgKey, msgParam}`
  - (Task 7 复用)`async def post(path, body) / put(path, body)` 通用带令牌请求
  - 响应头 `x-acs-dingtalk-access-token` 携带令牌;错误分类:HTTP 429 或 body code ∈ RATE_LIMIT_CODES → `DingTalkRateLimited`(解析 `flowControlledStaffIdList`);令牌失效码 → invalidate + 强刷一次 + 重试原请求仅一次;其余非 2xx → `DingTalkUpstreamError`(仅记 method/url/status)。
- Consumes: Task 1/2。

- [ ] **Step 1:** 失败测试:
  - `test_send_group_wire_format`(断言请求 JSON 精确形状,msgParam 为 JSON 字符串)
  - `test_send_direct_wire_format`
  - `test_rate_limit_code_raises_with_flow_controlled_list`(注入 `send.too.fast` + `flowControlledStaffIdList` → 异常携带两字段)
  - `test_each_rate_limit_code_classified`(四码逐一)
  - `test_invalid_token_forced_refresh_once_retry_success`(首次 40014 → invalidate → 刷新 → 重试成功;accessToken 端点 +1、业务端点恰 2 次)
  - `test_upstream_5xx_raises_upstream_error`(错误对象不含 body 秘钥)
- [ ] **Step 2:** 实现 → 转绿 → 提交 `feat(integrations): 钉钉群/单聊发送客户端——限流分类 + 令牌失效强刷一次(MES-89 §3.10)`。

---

### Task 4: 外部用户键编码 + markdown 分段(出站语义基础)

**Files:**
- Create: `backend/src/mesh/integrations/im_outbound.py`(本任务:编码 + 分段)
- Test: `backend/tests/unit/test_im_outbound.py`(建)

**Interfaces:**
- Produces:
  - `normalize_dingtalk_user_key(*, sender_staff_id: str | None, sender_id: str | None) -> str`(staffId 直通;无 staffId → `x=<base64url(senderId 原值字节, 无填充)>`;两者皆无 → `''`)
  - `encode_external_contact_key(sender_id: str) -> str`(`x=` + base64url)
  - `is_external_contact_key(key: str) -> bool`(前缀 `x=`)
  - `split_markdown_chunks(text: str, max_bytes: int = 15000) -> list[str]`(优先段落 `\n\n` 边界 → 行边界 → UTF-8 安全硬切;每段字节数 ≤ max_bytes;空文本 → `[]`)
- §5.6 N-1/E-1 断言直接落本任务测试。

- [ ] **Step 1:** 失败测试:
  - `test_staff_id_passthrough`(`014728255240768602` 原值)
  - `test_external_contact_encoding_official_sample`(`$:LWCP_v1:$6GYsn+zrv5WZ77xc2v4zsyXfBv1MhAv9` → `x=` + base64url,键不含 `:`/`$`/`+`)
  - `test_key_spaces_structurally_disjoint`(编码键第 2 字符恒 `=`;构造 staffId 字符集 `[A-Za-z0-9._-]` 千例随机键 ≠ 任一编码键;`=` 不在 staffId 字符类)
  - `test_distinct_sender_ids_distinct_keys`(编码前缀相同的两个 senderId → 键不等)
  - `test_raw_sender_id_with_colon_rejected_by_conversation_key_validator`(服务层校验:原值含冒号不得作身份段)
  - `test_split_under_limit_single_chunk` / `test_split_on_paragraph_boundary` / `test_split_falls_back_to_line` / `test_split_utf8_safe_hard_cut`(中文多字节不截半) / `test_each_chunk_under_max_bytes`(15000B 上限,构造 80KB 混合内容)
- [ ] **Step 2:** 实现 → 转绿 → 提交 `feat(integrations): 钉钉外部用户键编码(x=base64url,键空间不相交)+ markdown 分段(MES-89 §3.10)`。

---

### Task 5: DingTalkIMAdapter(会话消息 + verbosity/分段通知发送)

**Files:**
- Modify: `backend/src/mesh/integrations/im_outbound.py`
- Test: `backend/tests/unit/test_im_outbound.py`(追加)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) ConversationTarget`(provider_tenant_key、external_ref(conversationId)、conversation_type ∈ group/direct、sender_key(单聊目标,可空)、binding_id、integration_id)
  - `class DingTalkIMAdapter(client: DingTalkClient, *, max_chunks: int, detail_base_url: str)`
  - `async def send_conversation_text(target, text) -> SendOutcome`(群 groupMessages / 单聊 oToMessages;单聊目标为外部联系人键 → `SendOutcome(status='failed', reason='no_staff_id')`;文案不含 @)
  - `async def send_result_chunks(target, *, notification_id, markdown, msg_key='sampleMarkdown') -> list[SendOutcome]`(分段;超 max_chunks 截断 + 末段附"完整结果见 Mesh:<detail_url>";每段携带幂等键 `sha256(notification_id|'chunk'|i)` 供调用方登记)
  - `chunk_idempotency_key(notification_id, index) -> str`
  - `should_push_notification(*, notification_type: str, verbosity: str) -> bool`(final_only:仅确认/卡片/最终结果类;progress:追加进度类)
- `SendOutcome = NamedTuple`(status ∈ sent/failed、reason 可空、flow_controlled 可空)。

- [ ] **Step 1:** 失败测试(注入 FakeDingTalkPlatform):
  - `test_group_message_uses_groupMessages_send` / `test_direct_message_uses_oToMessages_batchSend`
  - `test_direct_external_contact_fails_no_staff_id`(不发请求、outcome.reason)
  - `test_result_over_15000_bytes_chunked`(80KB → 多段、每段 <15000B、按序发送)
  - `test_chunks_over_max_truncated_with_deep_link`(超 5 段 → 恰 5 段、末段含深链)
  - `test_chunk_idempotency_keys_stable`(sha256(notification_id|'chunk'|i) 精确值)
  - `test_verbosity_final_only_filters_progress`(final_only:progress 类 False、completed/failed True;progress 模式全 True)
  - `test_outbound_text_never_contains_at_mention`(构造含 @ 的输入 → 发送文案净化断言)
- [ ] **Step 2:** 实现 → 转绿 → 提交 `feat(integrations): 钉钉会话消息适配器——通道选择/分段/verbosity/外部联系人降级(MES-89 §3.10)`。

---

### Task 6: ack 选主(§3.8 入队侧)+ IMSendRelay(T1/T2 快 relay)

**Files:**
- Create: `backend/src/mesh/integrations/ack.py`
- Modify: `backend/src/mesh/integrations/im_outbound.py`(IMSendRelay 置此或 ack.py —— 放 `im_outbound.py`,ack.py 仅选主/常量)
- Test: `backend/tests/unit/test_ack.py`(建)

**Interfaces:**
- Produces(ack.py):
  - `IM_SEND_EVENT_TYPE = "im.send"`
  - `DEFAULT_ACK_TEMPLATE = "✅ 已接收,处理中"`
  - `async def elect_ack_leader(session, *, item: IntegrationMessageQueue, ack_template: str, coalesce_window: timedelta, now: datetime) -> bool`(True=本项为 leader 且已同事务写 im.send 事件;`ack_template==''` → False 且不写任何事件、ack_leader_id 保持 NULL;选主查询:本会话满足 `ack_leader_id=L.id`(自指)且 `item.ack_window_at ∈ [L.ack_window_at, L.ack_window_at+window)` 的最近项 L;命中 → follower(ack_leader_id=L.id,不写事件);未命中 → leader 自指 + `emit_event(im.send, idempotency_key=sha256(item.id|'ack'), payload={kind:'ack', integration_id, conversation_key, conversation_type, target_user_key, template, queue_item_id, position_snapshot})`)
  - `async def position_hint(session, *, item) -> int`(本会话更小 seq 的 pending 计数 + 1)
- Produces(im_outbound.py 追加):
  - `class IMSendRelay(session_factory, *, redis, signing_secret, adapter_factory, poll_interval, batch_size, ack_timeout=3.0, clock=None)`
  - `async def run_once() -> int` / `async def run_forever(stop)`
  - 语义:`kind='ack'` → **T1**(短事务:UPDATE 队列项 `ack_attempted_at=now() WHERE id=:leader AND ack_attempted_at IS NULL` + 事件置 published,**提交**)→ 事务外经适配器发送(ack_timeout)→ **T2**(成功:`ack_sent_at`(IS NULL 守卫)+ 批量回写窗口 follower `ack_represented_at`/`ack_merged_into` WHERE `ack_leader_id=:leader AND ack_represented_at IS NULL`;失败/超时:仅审计 `_mesh_ack_failed`,不重试);`kind='notification'` → 发送分段 → 成功回写台账 `notification_delivery`(sent_chunks 计数,全毕 → state='sent')→ 事件 published;限流 → 后移事件 `available_at`(不增 delivery_attempts);`kind='feedback'`/其他 → 发送后即 published(失败仅审计)。

- [ ] **Step 1:** 选主失败测试(真库):
  - `test_first_item_becomes_leader_self_referencing`(ack_leader_id=自身 + im.send 事件存在,键 sha256(id|'ack'))
  - `test_second_item_within_window_is_follower`(无新事件、指向 leader)
  - `test_item_outside_window_is_new_leader`
  - `test_empty_template_skips_all`(ack_leader_id 全 NULL、零事件、不占窗口)
  - `test_leader_order_by_seq_not_relay_arrival`(M2 先被处理仍为 follower——选主在入队事务内按 ack_window_at 先定)
  - `test_position_hint_counts_smaller_pending`
- [ ] **Step 2:** IMSendRelay 失败测试:
  - `test_ack_t1_gate_then_send_then_t2`(成功路径五字段:leader attempted+sent、follower represented+merged_into;平台恰一条)
  - `test_ack_t1_post_commit_crash_is_lost_not_duplicated`(T1 后杀"进程"(停 relay)→ 事件不再被领取(attempted ∧ ¬sent ∧ published,无重试))
  - `test_ack_t1_pre_commit_crash_resends`(T1 前失败 → 事件仍 pending → 重领、恰一次外呼)
  - `test_ack_send_failure_no_retry_audit_only`
  - `test_t1_stall_no_lost_plus_sent_ambiguity`(W1 停 T1 后 → W2 不可领取(published 不在候选);W1 续走 → attempted∧sent,平台恰一条)
  - `test_ack_leader_only_carries_position_hint`(串行排队位置措辞)
- [ ] **Step 3:** 实现 → 转绿 → 提交 `feat(integrations): ack at-most-once 快 relay——T1 闸门 + 事务外外呼 + T2 回写;入队侧选主(§3.8,MES-89)`。

---

### Task 7: 互动卡片投放/更新/流式(§3.10 card_1.0)

**Files:**
- Create: `backend/src/mesh/integrations/dingtalk_cards.py`(投放侧)
- Test: `backend/tests/unit/test_dingtalk_cards.py`(建)

**Interfaces:**
- Produces:
  - `derive_out_track_id(approval_id: uuid.UUID) -> str`(稳定派生,如 `mesh-appr-<hex>`)
  - `open_space_id(*, conversation_type: str, open_conversation_id: str | None, sender_staff_id: str | None) -> str`(`dtv1.card//IM_GROUP.<cid>` / `dtv1.card//IM_ROBOT.<staffId>`)
  - `build_approval_card_data(approval_render: dict, *, agent_name: str, expires_at, status_text: str | None, buttons_disabled: bool) -> dict`(§4.4 字段表:标题/动作/权限/影响/成本/过期/续跑提示 + cardParamMap)
  - `assert_not_action_card(msg_key_or_template: str) -> None`(命中 sampleActionCard*/传统 ActionCard → AssertionError;审批卡路径调用点强制过此断言)
  - `async def push_approval_card(client, *, approval, target: ConversationTarget, card_template_id: str, callback_type: str = 'STREAM') -> dict`(createAndDeliver:`cardTemplateId` + `outTrackId`=派生 + `openSpaceId` + `cardData` + `callbackType`;先过 `assert_not_action_card`)
  - DingTalkClient 追加:`create_and_deliver_card(body)` POST `/v1.0/card/instances/createAndDeliver`、`update_card(body)` PUT `/v1.0/card/instances`、`stream_card(body)` PUT `/v1.0/card/streaming`(guid 幂等、markdown isFull=true、isFinalize 收口;单帧 ≤1KB/总量 ≤3KB 常量)
- Consumes: Task 3 client。

- [ ] **Step 1:** 失败测试:
  - `test_out_track_id_stable_per_approval` / `test_open_space_id_group_and_robot_format`
  - `test_card_data_fields_match_spec_4_4`(逐字段存在性 + 取值来源)
  - `test_push_approval_card_wire_format`(createAndDeliver 请求含四要素;callbackType='STREAM')
  - `test_sampleActionCard6_forbidden_for_approval_card`(以 actionCard 模板走审批卡路径 → AssertionError;代码路径断言)
  - `test_update_card_idempotent_by_out_track_id`(重复更新不冲突,PUT 体含 outTrackId)
  - `test_streaming_constraints`(guid 幂等键、isFull=true、isFinalize 收尾、帧大小常量断言)
- [ ] **Step 2:** 实现 → 转绿 → 提交 `feat(integrations): 钉钉互动卡片投放——createAndDeliver/update/streaming + §4.4 字段 + ActionCard 禁用断言(MES-89)`。

---

### Task 8: 卡片回调鉴权链 + 生命周期回写(§4.4)

**Files:**
- Modify: `backend/src/mesh/integrations/dingtalk_cards.py`(回调侧)、`backend/src/mesh/integrations/cards.py`(抽共享链 + im_dingtalk extract_clicker)、`backend/src/mesh/integrations/inbound_routes.py`(HTTP 卡片端点)
- Test: `backend/tests/unit/test_dingtalk_cards.py`(追加)

**Interfaces:**
- Produces:
  - `verify_callback_signature(*, app_secret: str, timestamp: str | None, sign: str | None, now: datetime, tolerance: timedelta) -> str`(§3.2 钉钉行:`Base64(HMAC_SHA256(app_secret, timestamp + "\n" + app_secret))`,恒定时间比较,±3600s;返回 valid/invalid/missing)
  - `extract_dingtalk_clicker(payload: dict, integration: Integration) -> tuple[str,str,str] | None`(userId + userIdType **按 staffId 归一**;无 staffId 回落 `x=<base64url(senderId)>`;tenant=corp_id 自集成 config)
  - `extract_dingtalk_action(payload) -> tuple[uuid.UUID, bool] | None`(content.cardPrivateData.params 取 `{approval_id, decision}`)
  - `lifecycle_response(*, state: str, ...) -> dict`(§4.4 状态表 → `{cardUpdateOptions:{updateCardDataByKey:true, updatePrivateDataByKey:true}, cardData:{cardParamMap:{status_text, buttons_disabled,...}}, userPrivateData:{<userId>:{cardParamMap:{...}}}}`;state ∈ loading/decided/duplicate/forbidden/expired/failed,过期/失败附 [回 Mesh 处理] 深链)
  - `async def handle_dingtalk_card_callback(session, session_factory, *, integration, payload, now) -> tuple[int, dict]`(链:clicker → external_identities → users.id → JOIN members(active human)→ decide_approval;未映射/无名册/无权 → 403 + 审批不变 + write_audit + 卡片回写 forbidden 态;成功 → 回写 decided 终态文本 + 按钮禁用;重复点击 → decide_approval 幂等 no-op + 终态保持;站内异常 → failed 态 + 深链 + 告警)
  - HTTP 端点 `POST /api/v1/integrations/dingtalk/cards`:定位集成(corpId/robotCode)→ verify_callback_signature → handle_dingtalk_card_callback;裸 JSON 响应(不套成功包络),与 autopilot 入站同例。
- Consumes: `cards.lookup_identity`、`runtime.approvals.decide_approval`、`auth.audit.write_audit`。

- [ ] **Step 1:** 失败测试:
  - `test_signature_valid/invalid/missing/timestamp_out_of_window`(±3600s 边界:59 分钟前放行、61 分钟拒)
  - `test_clicker_staff_id_normalization` / `test_clicker_external_contact_fallback_encoding`
  - `test_callback_unmapped_identity_403_approval_unchanged_audited`(卡片回写 forbidden + 引导文案,不泄漏详情)
  - `test_callback_no_roster_row_403` / `test_callback_no_permission_403`
  - `test_callback_approve_forwards_to_decide_approval`(审批转 approved,回写终态文本 + 按钮禁用)
  - `test_callback_repeat_click_idempotent`(二次点击 no-op、终态保持)
  - `test_callback_expired_approval`(过期态 + 深链)
  - `test_callback_internal_error_failed_state_with_deep_link`
  - `test_loading_private_data_per_clicker`(userPrivateData 仅点击者键)
- [ ] **Step 2:** 实现(cards.py 重构保持 feishu/slack 既有测试全绿)→ 转绿 → 提交 `feat(integrations): 钉钉卡片回调鉴权链 + §4.4 生命周期回写 + HTTP 回调端点(MES-89)`。

---

### Task 9: test-send 与 stream-status(诊断分离,§3.9/§3.5)

**Files:**
- Modify: `backend/src/mesh/integrations/routes.py`、`schemas.py`
- Test: `backend/tests/unit/test_integration_routes_dingtalk.py`(建;路由级单测,注入 fake client)

**Interfaces:**
- `POST /workspaces/{ws}/integrations/{id}/test-send`(body `{conversation_ref, conversation_type, user_key?}`;经 OpenAPI 发测试文本;**失败仅 502 upstream_error / 凭据失效提示,绝不返回 503 stream_channel_unavailable**,即使 stream_state='down';RBAC admin/`integration:manage`,复用现有写权限依赖)
- `GET /workspaces/{ws}/integrations/{id}/stream-status`(只读 `integrations.stream_state` + status;`state='down'` → **503 stream_channel_unavailable**(仅此端点、仅接收语境);`disabled`/connected/reconnecting → 200 + 状态体;kind 非 im_dingtalk → 422)

- [ ] **Step 1:** 失败测试:`test_test_send_success_wire`、`test_test_send_while_stream_down_still_succeeds`(stream_state down + 出站成功 → 200,响应不含 stream_channel_unavailable)、`test_test_send_upstream_failure_502`、`test_stream_status_connected_200`、`test_stream_status_down_503`、`test_stream_status_non_dingtalk_422`、`test_test_send_rbac_member_forbidden`。
- [ ] **Step 2:** 实现 → 转绿 → 提交 `feat(integrations): test-send/stream-status 诊断分离——出站不依赖接收信道(§3.9/§3.5,MES-89)`。

---

### Task 10: worker 接线 + fanout 派生(IM 台账生产侧)

**Files:**
- Modify: `backend/src/mesh/integrations/im_outbound.py`(`derive_im_deliveries_from_fanout`)、`backend/src/mesh/workers/main.py`(TaskSpec + FANOUT 链)、`backend/src/mesh/runtime/credentials.py`(黑名单增补)
- Test: `backend/tests/unit/test_im_outbound.py`(追加派生用例)、`backend/tests/unit/test_credentials_redaction.py`(若无则建)

**Interfaces:**
- `async def derive_im_deliveries_from_fanout(session, event) -> None`(链于 NotificationFanoutHandler 之后:对本次 fanout 创建的 `notifications`(execution_id 非空)→ `task_executions.trigger='integration'` → 经 execution_id 反查 `integration_message_queue`(binding/conversation/sender)→ 集成 kind='im_dingtalk' 且 active → verbosity 闸门(§Task5 should_push_notification)→ INSERT `notification_delivery(channel='im', provider='dingtalk', destination_key='dingtalk:<binding_id>:<cid>', integration_id, binding_id, external_target=JSON{conversation_type, conversation_key, sender_key, robot_code, chunks_total})`(UNIQUE 幂等)→ 按类型发 im.send 事件:审批类 → 单事件 `{kind:'card', approval_id,...}`(键 `sha256(approval_id|'card')`);文本结果 → 每段一事件 `{kind:'notification', delivery_id, chunk_index, text}`(键 `sha256(notification_id|'chunk'|i)`))
- credentials.py:`load_redaction_blacklist` UNION `integrations.secret_ref`(同工作区、未软删、非空;解密失败跳过同既有语义)。
- workers/main.py:`TaskSpec("im-send-relay", lambda: im_send_relay.run_forever(stop))`;`FANOUT_EVENT_TYPE` 改为组合处理函数(基线 + 派生,派生异常仅日志不阻基线)。

- [ ] **Step 1:** 失败测试:
  - `test_fanout_derives_im_delivery_for_integration_execution`(台账行 + 分段事件齐;destination_key 格式)
  - `test_fanout_skips_non_integration_execution` / `test_fanout_verbosity_final_only_drops_progress`
  - `test_fanout_approval_notification_derives_card_event`
  - `test_delivery_unique_idempotent`(重复 fanout 不重行)
  - `test_redaction_blacklist_includes_integration_secrets`(集成 secret_ref 解密值进黑名单 → redact_text 命中)
- [ ] **Step 2:** 实现 → 转绿(含 `tests/unit/test_integration_*` 既有套件回归)→ 提交 `feat(integrations): IM 台账派生链入 notification.fanout + im-send-relay 受监督任务 + 集成凭据进脱敏黑名单(MES-89)`。

---

### Task 11: 真实 e2e(§5.6 出站/卡片验收断言)

**Files:**
- Create: `backend/tests/e2e/dingtalk_fake_server.py`、`backend/tests/e2e/test_dingtalk_outbound_e2e.py`

**e2e 形态:** 真起 API 子进程(`api_server` fixture 同构)+ 真 `python -m mesh.workers` 子进程(env `MESH_DINGTALK_API_BASE/OAPI_BASE` 指向本地替身、轮询间隔调小)+ `ThreadingHTTPServer` 钉钉替身(线程安全计数、可脚本化响应:限流码/5xx/延迟)。

- [ ] **Step 1:** 实现替身服务器(/v1.0/oauth2/accessToken、groupMessages/send、oToMessages/batchSend、card/instances/createAndDeliver、card/instances PUT;请求日志 + 控制旋钮)。
- [ ] **Step 2:** e2e 用例(逐条对应 §5.6):
  - `test_e2e_token_single_flight_two_replicas`(两个 IMSendRelay 进程/循环并发触发同集成 → accessToken 端点恰 1 次)
  - `test_e2e_token_busy_not_terminal`(注入刷新 > follower_wait → 事件仅后移 available_at、delivery_attempts 不变;刷新恢复后经 available_at 到期重试成功;连续 busy 超 max_attempts 仍不终态、不热循环)
  - `test_e2e_ack_crash_points`(T1 后 SIGKILL worker → attempted∧¬sent∧published 无重试;T1 前杀 → 重启恰一次外呼)
  - `test_e2e_ack_leading_edge_merge`(5s 窗口内 3 条 → 平台恰一条确认;leader/follower 五字段落库;无尾部"N 条"消息)
  - `test_e2e_rate_limit_backoff`(替身回 send.too.fast + flowControlledStaffIdList → 指数退避重试成功、台账记限流码、不整体失败)
  - `test_e2e_result_chunking`(>15000B 结果 → 多段按序到达、各段幂等键;重复出队不重发段;超 5 段截断 + 深链)
  - `test_e2e_verbosity_final_only`(默认仅确认/卡片/最终结果出站,progress 类台账无行;切 progress 后推进度)
  - `test_e2e_approval_card_full_lifecycle`(派生审批 → createAndDeliver 收到(四要素断言)→ 卡片回调(HTTP 端点)鉴权通过 → 审批 approved + 卡片终态回写;未映射身份点击 → 403 + 审批不变 + 留痕;重复点击幂等)
  - `test_e2e_test_send_independent_of_stream`(stream_state='down' 置库 → test-send 仍成功;stream-status 独立 503)
  - `test_e2e_external_contact_direct_degradation`(x= 键单聊 → 执行不受影响、投递 failed(no_staff_id)+ 告警审计)
  - `test_e2e_secrets_never_in_logs_or_ledger`(替身 5xx → 错误台账/日志仅 method/url/status;body 秘钥 *** 或不出现)
- [ ] **Step 3:** 全量跑绿 → 提交 `test(integrations): 钉钉出站/卡片真实 e2e——§5.6 逐条(单飞/busy/ack 崩溃点/限流/分段/卡片生命周期,MES-89)`。

---

### Task 12: 覆盖率收口 + 文档 + PR

**Files:**
- Modify: `backend/README.md`、`README.md`、`CHANGELOG.md`;补测试至新增代码 ≥90%

- [ ] **Step 1:** `pytest --cov=mesh --cov-report=term-missing --cov-fail-under=90`(unit + e2e 全量);逐文件补新模块覆盖(分支覆盖)。
- [ ] **Step 2:** `ruff check src tests`;文档更新(backend README worker 表 + 模块说明;README 模块行;CHANGELOG 版本节;Spec 无需改——本切片不改契约,如发现缺漏另起评论反馈 Leader)。
- [ ] **Step 3:** 合规自查:`git log --format=%B origin/main..HEAD | grep -i 'co-authored-by'` 无输出;全分支 diff grep 参考来源字样零命中;`git log -1 --format='%an <%ae> | %cn <%ce>'` 双 cnwenf。
- [ ] **Step 4:** push + `gh pr create`(描述含:切片范围、跨切片集成点三条、§5.6 验收对照表、e2e 证据摘要)。
- [ ] **Step 5:** 发结果评论(PR URL + 验收要点);issue 状态 → in_review。

---

## Self-Review

**Spec 覆盖核对:**
- §3.10 令牌单飞(共享缓存/随机 owner/Lua 释放/TTL±60s/follower 12s 双检/busy 退避不耗预算/invalid_credentials 终态/失效强刷一次/脱敏)→ Task 2/3/11 ✓
- §3.10 发送通道(groupMessages/oToMessages/13 msgKey/msgParam≤15000B 无 @)→ Task 3/5 ✓
- §3.10 卡片(createAndDeliver 四要素/outTrackId 派生/openSpaceId 双形/callbackType STREAM 优先/PUT 更新/streaming guid+isFull+isFinalize/sampleActionCard6 严禁/回调鉴权链/cardUpdateOptions 回写)→ Task 7/8 ✓
- §3.8 ack(at-most-once/T1 闸门/崩溃三段/leading-edge 5s 锁序窗口/五字段/template='' 跳过/默认文案/位置提示/不经 notification_delivery)→ Task 6/11 ✓
- §4.4 生命周期状态表(loading/成功禁用/重复/过期/无权/失败/[回 Mesh 处理])→ Task 8 ✓
- §3.3/§3.10 verbosity 与长文(final_only 默认/分段幂等键/MESH_IM_MAX_CHUNKS 截断 + 深链)→ Task 5/10/11 ✓
- 诊断分离(test-send 不用 503/stream-status 503 限定接收语境)→ Task 9/11 ✓
- §5.6 出站/卡片验收断言 → Task 11 逐条 ✓;UT≥90% → 每任务 TDD + Task 12 ✓;合规 → Global Constraints + Task 12 ✓
- 外部联系人降级(x= 编码/no_staff_id/群聊不受限)→ Task 4/5/11 ✓
- redact_in_logs 登记 → Task 10 ✓

**类型一致性:** `ConversationTarget`(Task 5)被 Task 7/8/10 一致引用;`SendOutcome`(Task 5)被 Task 6 relay 消费;`DingTalkClient`(Task 3)方法名 send_group/send_direct/create_and_deliver_card/update_card/stream_card 全程一致;`IM_SEND_EVENT_TYPE`(Task 6)被 Task 10 派生侧引用;`elect_ack_leader` 签名(Task 6)= 跨切片契约(PR 描述)一致。
