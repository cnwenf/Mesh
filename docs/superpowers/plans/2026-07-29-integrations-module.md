# Integrations Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the full integrations platform module (integrations.md five chapters): integration registry/bindings, inbound ingestion reusing the autopilot `webhook_events` paradigm, outbound IM/VCS adapters + developer webhook subscriptions with delivery ledger, `external_identities` global identity table, and the §4 management UI.

**Architecture:** New `mesh/integrations/` package with per-provider connector adapters (verify-signature / normalize-event / outbound-send three adaptation points) around ONE shared ingestion pipeline (signature → dedup `UNIQUE(integration_id, external_event_id)` → audit → binding match → `execution.enqueue` outbox). Outbound developer webhooks are outbox-driven: relay derives `webhook.dispatch` from `realtime.publish`, a supervised delivery worker POSTs with `Mesh-Signature` HMAC + exponential backoff + subscription-level circuit breaker. `external_identities` is a global table (no `workspace_id`, exempt from workspace RLS) mapping `(provider, provider_tenant_key, external_user_key) → users.id`; unlink is owner-only (no admin bypass).

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 async / asyncpg / Alembic / PG16 (column-level `ON DELETE SET NULL (col)`), pytest + pytest-cov (90% gate), React 19 + react-router + react-intl + zustand frontend.

## Global Constraints

- Spec authority: `docs/specs/features/integrations.md` + `docs/specs/README.md` §6 (§6.1/§6.2/§6.5/§6.6/§6.7/§6.9/§6.10/§6.14/§6.15/§6.16/§6.17).
- Realtime event names ONLY from §6.7 registry: `integration.updated`, `integration.event_ingested` (already in `mesh/events/vocab.py`).
- Ingestion reuses autopilot paradigm (`autopilot/webhook.py`): constant-time compare, ±300s replay window, `rejected:<sha256(raw_body)>` pre-occupation-proof namespace, invalid/missing → 401 never dispatched, dedup → idempotent 200.
- All externally visible side effects carry §6.5 idempotency keys: enqueue = `sha256(agent_id|integration_binding_id|external_event_id)`; outbound delivery ledger = `UNIQUE(subscription_id, event_ref)`.
- Credentials: Fernet ciphertext via `mesh.auth.security.encrypt_secret/decrypt_secret` (key = `settings.jwt_secret`), same contract as `runtime_credentials.encrypted_value`; plaintext never in responses/logs; `config` JSONB holds only `*_ref` references, never secrets.
- Outbound URLs: https-only (`400 invalid_url_scheme`) + SSRF guard reusing `mesh.runtime.checkout.is_forbidden_host` (`422 ssrf_blocked`; private/link-local/`169.254.169.254`).
- Multi-tenancy: every referenced table gets `UNIQUE(workspace_id, id)`; all cross-references composite FK; PG16 column-level `ON DELETE SET NULL (col)`; RLS fail-closed on tenant tables; `external_identities` exempt (global table, already in `mesh/db/tenant.py GLOBAL_TABLES`).
- Inbound endpoints: platform signature auth (NOT Bearer), bare JSON responses (no §6.14 envelope).
- Git identity `cnwenf <cnwenf@outlook.com>`, no `Co-Authored-By`, `git config core.hooksPath /dev/null`.
- Test env: `MESH_TEST_DATABASE_URL=postgresql+asyncpg://mesh:mesh@127.0.0.1:54399/mesh_test`, `MESH_TEST_REDIS_URL=redis://127.0.0.1:6399/1`, `MESH_TEST_STORAGE_ENDPOINT=http://127.0.0.1:9100`. Run: `cd backend && . .venv/bin/activate && pytest`.
- Anonymization: no reference-product names in code/comments/docs/commits.

## File Structure

**Backend (create):**
- `backend/src/mesh/db/models/integration.py` — ORM models: `Integration`, `IntegrationBinding`, `IntegrationEvent`, `ExternalIdentity`, `WebhookSubscription`, `WebhookSubscriptionDelivery`, `VcsLink`.
- `backend/migrations/versions/0028_integrations.py` — DDL (mirrors integrations.md §2.8 verbatim) + RLS + SECURITY DEFINER bootstrap lookup for signature-auth endpoints + `external_identity_unlink_allowed(identity_id, member_id)` SQL reference function.
- `backend/src/mesh/integrations/__init__.py`
- `backend/src/mesh/integrations/connectors.py` — adapter protocol + `feishu`/`slack`/`github`/`gitlab`/`webhook_outbound` adapters: `verify()`, `normalize_event()`, `tenant_key()`, outbound send helpers (token cache for feishu).
- `backend/src/mesh/integrations/inbound.py` — shared ingestion pipeline (mirror of `autopilot/webhook.py`).
- `backend/src/mesh/integrations/matching.py` — binding matcher (`trigger_on`/`mention_agents`/keywords/`vcs_events`/`branch_pattern`).
- `backend/src/mesh/integrations/identities.py` — external identity link/link-confirm/unlink + one-time verification codes + `external_identity_unlink_allowed()` Python reference.
- `backend/src/mesh/integrations/cards.py` — card callback auth chain → `mesh.runtime.approvals.decide_approval` forwarding.
- `backend/src/mesh/integrations/vcs_links.py` — vcs_links CRUD + identifier (`WEB-123`) resolution + auto status flow.
- `backend/src/mesh/integrations/outbound.py` — subscriptions CRUD + `webhook.dispatch` derivation handler + delivery worker (HMAC/retry/breaker) + SSRF/https validation.
- `backend/src/mesh/integrations/oauth.py` — OAuth authorization-code + PKCE (Redis state, dev mock exchange).
- `backend/src/mesh/integrations/schemas.py` — request models.
- `backend/src/mesh/integrations/service.py` — `IntegrationService`: integrations/bindings CRUD, secret rotate, credential store.
- `backend/src/mesh/integrations/routes.py` — admin CRUD (§3.1) + external identities + OAuth + vcs links.
- `backend/src/mesh/integrations/inbound_routes.py` — 4 signature-auth event endpoints + 2 card endpoints.
- `backend/src/mesh/integrations/channels.py` — `integration:{id}` channel checker registration.
- Tests: `backend/tests/unit/test_integration_*.py` (per area), `backend/tests/e2e/test_integrations_e2e.py` (T29 + §5 acceptance), `backend/tests/e2e/test_integrations_outbound_e2e.py`.

**Backend (modify):**
- `backend/src/mesh/auth/rbac.py` — add `"integration:manage": frozenset({"owner","admin"})`.
- `backend/src/mesh/api/app.py` — build `IntegrationService`, include routers, register channel checkers.
- `backend/src/mesh/realtime/app.py` — register channel checkers (gateway parity).
- `backend/src/mesh/workers/main.py` — register `webhook.dispatch` handler; add `WebhookDeliveryWorker` supervised task.
- `backend/src/mesh/config.py` — delivery settings (`webhook_delivery_*`, `integration_signature_tolerance`, `identity_code_ttl`).
- `backend/src/mesh/db/models/__init__.py` — export new models.
- `backend/README.md` — module bullet + endpoints.

**Frontend (create under `frontend/src/features/integrations/`):**
- `api.ts`, `types.ts`, `IntegrationsPage.tsx` (connector catalog + list + detail tabs: overview/bindings/events), `BindingDrawer.tsx`, `EventLedger.tsx`, `WebhooksPage.tsx` (subscriptions + delivery timeline + resume/retry), `ExternalIdentitiesCard.tsx`, `oauth.ts` helpers; issue sidebar VCS-links block in `features/issues/` detail.
- Routes in `App.tsx`: `w/:workspaceSlug/integrations`, `.../integrations/:id`, `.../webhooks`.
- i18n keys: `frontend/src/i18n/messages/zh-CN.json` + `en.json` (`integrations.*` namespace).

---

## Task 1: Data model + migration 0028

**Files:** Create `backend/src/mesh/db/models/integration.py`, `backend/migrations/versions/0028_integrations.py`; modify `backend/src/mesh/db/models/__init__.py`; test `backend/tests/unit/test_integration_models.py`, `backend/tests/e2e/test_integrations_schema_e2e.py`.

**Interfaces:**
- Produces: ORM classes `Integration(id, workspace_id, kind, name, status, config, secret_ref, created_by, deleted_at, timestamps)`, `IntegrationBinding(id, workspace_id, integration_id, provider, provider_tenant_key, scope, project_id, external_ref, match_config, bound_agent_id, status, timestamps)`, `IntegrationEvent(id, workspace_id, integration_id, external_event_id, event_type, payload, signature_status, process_status, received_at, timestamps)`, `ExternalIdentity(id, provider, provider_tenant_key, external_user_key, user_id, created_in_workspace_id, verified_at, timestamps)` — **no workspace_id column**, `WebhookSubscription(id, workspace_id, integration_id, url, secret_ref, event_types, status, fail_count, created_by, timestamps)`, `WebhookSubscriptionDelivery(id, workspace_id, subscription_id, event_ref, state, attempts, next_retry_at, response_status, last_error, created_at)`, `VcsLink(id, workspace_id, integration_id, provider, provider_tenant_key, external_object_type, external_object_ref, mesh_entity_type, mesh_entity_id, link_source, status, external_state, created_by, timestamps)`.

- [ ] **Step 1: Migration 0028** — DDL exactly per integrations.md §2.8 (all 7 tables, constraints `uq_binding_external_identity UNIQUE(provider, provider_tenant_key, external_ref)`, `ck_binding_scope` XOR CHECK, `uq_integration_event_dedup UNIQUE(integration_id, external_event_id)`, `uq_external_identity UNIQUE(provider, provider_tenant_key, external_user_key)`, `uq_delivery_subscription_event`, partial unique indexes `uq_vcs_links_external_object` / `uq_vcs_links_mesh_entity` WHERE status='active'), composite FKs with PG16 column-level `ON DELETE SET NULL (bound_agent_id)` / `(integration_id)` / `(created_by)` / `(created_in_workspace_id)`, RLS fail-closed on the six tenant tables (NOT external_identities) with `mesh_app` grants, plus SQL function `external_identity_unlink_allowed(p_identity_id uuid, p_member_id uuid) RETURNS boolean` comparing ONLY `users.id` via `members.user_id` (role不参与). Chain `revision="0028"`, `down_revision="0027"`.
- [ ] **Step 2: ORM models** matching DDL (follow `db/models/autopilot.py` style; `ExternalIdentity` without workspace_id).
- [ ] **Step 3: Schema e2e test** (real DB): run migrations; assert T29 structure negatives — `information_schema.columns`: `external_identities` has NO `workspace_id`; no FK from `external_identities` to `workspaces` with `DELETE_RULE='CASCADE'`; `pg_policies` has no policy on `external_identities`; constraint existence checks for global binding key, scope CHECK, dedup key, vcs_links partial uniques.
- [ ] **Step 4: Model↔migration drift test** — extend the existing drift-check pattern (see `tests/e2e/test_schema_validation.py`) to cover the new tables' unique constraints.
- [ ] **Step 5: Run** `pytest tests/e2e/test_integrations_schema_e2e.py tests/unit/test_integration_models.py -v` → green. Commit `feat(integrations): 数据模型 + 迁移 0028(七表 + T29 结构约束)`.

## Task 2: RBAC + IntegrationService CRUD + credential security

**Files:** Create `backend/src/mesh/integrations/{__init__,schemas,service}.py`; modify `backend/src/mesh/auth/rbac.py` (add `integration:manage`); tests `backend/tests/unit/test_integration_service.py`.

**Interfaces:**
- Consumes: `encrypt_secret/decrypt_secret` (`mesh.auth.security`), models from Task 1.
- Produces: `IntegrationService(session_factory, signing_secret)` with `create_integration(workspace_id, creator: Member, payload) -> dict`, `list_integrations(...)`, `get_integration(...)`, `update_integration(...)`, `soft_delete_integration(...)`, `rotate_secret(...) -> dict(plaintext once)`; serialization helper `public_integration(row)` NEVER includes `secret_ref` plaintext (field omitted entirely; `has_secret: bool` only); `store_secret(...) -> secret_ref` (`cred:<uuid>` style reference or direct ciphertext column — store ciphertext in `secret_ref` itself, matching autopilot `encrypted_secret` contract); `load_secret_plaintext(session, integration) -> str|None`.

- [ ] **Step 1: Failing tests** — create integration (kind whitelist validation → 400 invalid_request on bad kind); name unique within workspace (409 conflict); secret round-trip: create with `secret` field → response has NO secret, `has_secret=true`; `rotate_secret` returns new plaintext once, old ciphertext replaced (decrypt(old) fails or differs); `config` containing a raw secret-looking key is rejected OR stored only as `*_ref` (service asserts no `secret`/`token`/`password` plaintext keys in config → 422).
- [ ] **Step 2: Implement** service + schemas (pydantic, `config` validated non-secret per §2.7).
- [ ] **Step 3:** Run unit tests → green. Commit `feat(integrations): 集成定义 CRUD + 凭据密文契约(§6.16)`.

## Task 3: Bindings CRUD with global external-identity key

**Files:** Extend `service.py`; tests `backend/tests/unit/test_integration_bindings.py`.

**Interfaces:**
- Produces: `create_binding(...)`, `list_bindings(integration_id)`, `update_binding(...)`, `delete_binding(...)`; provider normalization `KIND_TO_PROVIDER = {"im_feishu":"feishu","im_slack":"slack","vcs_github":"github","vcs_gitlab":"gitlab","webhook_outbound":"webhook"}`; `provider_tenant_key` normalized from `integrations.config` at insert (slack `team_id`, feishu `tenant_key`, github `installation_id`, gitlab instance host, webhook `''`).

- [ ] **Step 1: Failing tests** — provider mismatch with integration kind → 422; scope XOR: `scope='workspace'` + `project_id` → 422; `scope='project'` missing `project_id` → 422; cross-workspace binding conflict: ws-A and ws-B each own an integration instance, bind same `(provider, tenant_key, external_ref)` → second insert `409 binding_conflict` (catch IntegrityError `uq_binding_external_identity`); disabled binding still occupies the key (delete required first).
- [ ] **Step 2: Implement** + map IntegrityError constraint names to named codes.
- [ ] **Step 3:** green → commit `feat(integrations): 绑定模型 CRUD + 外部身份全局唯一键(R3)`.

## Task 4: Shared inbound ingestion pipeline

**Files:** Create `backend/src/mesh/integrations/{inbound,matching}.py`; tests `backend/tests/unit/test_integration_inbound.py`.

**Interfaces:**
- Consumes: models, `emit_event`/`emit_realtime` (`mesh.outbox.service`), `build_config_snapshot` (`mesh.agent.snapshot`), `set_tenant_context`, `ENQUEUE_EVENT_TYPE` (`mesh.runtime.enqueue`).
- Produces: `async def process_inbound(session, *, kind: str, raw_body: bytes, headers: dict, query: dict, signing_secret: str, now: datetime, tolerance: timedelta) -> tuple[int, dict]` — locate integration via connector adapter (`feishu: app_id/config lookup`, `slack: team_id`, `github: installation id from payload + binding route`, `gitlab: token lookup`), verify signature (adapter), on invalid/missing → store `integration_events(signature_status, process_status='rejected', external_event_id='rejected:'+sha256(raw))` in savepoint + return `401 {"error":{"code":"invalid_signature",...}}`; integration `disabled` → rejected + `401 integration_disabled` (410-mapped code in envelope details); dedup INSERT via savepoint → hit `uq_integration_event_dedup` → `200 {"received":true,"process_status":"deduped"}`; emit `integration.event_ingested` realtime on channel `workspace:{ws}:integrations`; match bindings (`matching.py`); no match/no agent → `process_status='matched'|'received'` audit only; match → same-transaction `emit_event(event_type='execution.enqueue', payload={intent:'enqueue', agent_id, issue_id: None, trigger:'integration', trigger_event_id: str(event.id), idempotency_key: sha256(f"{agent_id}|{binding_id}|{external_event_id}"), config_snapshot/required_capabilities via build_config_snapshot(agent active version + declared caps), task_spec: {"kind":"integration_event","untrusted_context":{"provider":...,"event_type":...,"payload":...}} (§6.15 structural isolation), label_requirements: {}}, idempotency_key=same)`, `process_status='dispatched'`; multiple bindings matched → audit + alert, dispatch NONE (§5.4).

- [ ] **Step 1: Failing tests** (use a stub connector registered in the adapter registry): valid signature first time → dispatched + outbox row with exact idempotency key `sha256(f"{agent}|{binding}|{ext}")` and `trigger='integration'`; duplicate → 200 deduped, no second outbox row; invalid → 401 + `rejected:` row; forgery pre-occupation: unsigned request with body X creates `rejected:<hash>`; later legit signed event with same external_event_id still dispatches; disabled integration → 401 integration_disabled; unmatched → no `execution.enqueue` row, process_status audit; untrusted marker present in task_spec.
- [ ] **Step 2: Implement** pipeline + matcher (AND across fields, OR within multi-value; `trigger_on` mention/direct_message/keyword; `mention_agents` filter; `vcs_events`; `branch_pattern` regex with `re.error` → 422 at binding create).
- [ ] **Step 3:** green → commit `feat(integrations): 入站摄取管线(签名/去重/审计/§6.9 入队)`.

## Task 5: Four connector adapters

**Files:** Create `backend/src/mesh/integrations/connectors.py`; tests `backend/tests/unit/test_integration_connectors.py`.

**Interfaces:**
- Produces per adapter (`feishu/slack/github/gitlab`): `verify(config: dict, secrets: Mapping[str,str], raw_body: bytes, headers: dict, *, now, tolerance) -> Literal["valid","invalid","missing"]`, `normalize_event(payload: dict) -> NormalizedEvent(external_event_id, event_type, external_ref, actor_key, text, extra)`, `tenant_key(config) -> str`, `locate_integration_query(payload/headers) -> SQLAlchemy filter args`.
  - Feishu: `signature = SHA256(timestamp + nonce + encrypt_key + raw_body)` (headers `timestamp`/`nonce` — feishu signs with string concat, hex compare constant-time); `url_verification` challenge handled in route layer after token verify.
  - Slack: `v0=HMAC_SHA256(signing_secret, "v0:"+ts+":"+raw)` (`X-Slack-Signature`/`X-Slack-Request-Timestamp`), replay ±300s, `url_verification` challenge echo.
  - GitHub: `X-Hub-Signature-256: sha256=HMAC_SHA256(webhook_secret, raw)`; `X-GitHub-Delivery` = external_event_id; `X-GitHub-Event` = event_type.
  - GitLab: `X-Gitlab-Token` shared secret constant-time compare (or `X-Gitlab-Signature` HMAC); `X-Gitlab-Event` type; `event_uuid` from payload.

- [ ] **Step 1: Failing tests** per platform — valid signature fixture (computed in-test with the same algorithm) → valid; tampered body → invalid; missing header → missing; stale timestamp (>300s) with otherwise-valid sig → invalid (replay); normalization fixtures: feishu `im.message.receive_v1` (chat_id, open_id, text), slack `message.channels` (channel, user, team, event_ts), github `pull_request` (installation_id, repo full_name, PR number/title/state, `X-GitHub-Delivery`), gitlab `Merge Request Hook` (event_uuid, project path_with_namespace).
- [ ] **Step 2: Implement** adapters with `hmac.compare_digest` everywhere.
- [ ] **Step 3:** green → commit `feat(integrations): 四连接器签名校验 + 载荷归一`.

## Task 6: Inbound routes (signature-auth, bare JSON)

**Files:** Create `backend/src/mesh/integrations/inbound_routes.py`; wire in `api/app.py`; tests `backend/tests/e2e/test_integrations_inbound_e2e.py`.

**Interfaces:**
- Routes: `POST /api/v1/integrations/feishu/events`, `/slack/events`, `/github/events`, `/gitlab/events` (+ `/feishu/cards`, `/slack/cards` in Task 8). Reads raw `Request.body()`; calls `process_inbound`; returns `JSONResponse(status, bare_json)`. Feishu/Slack `url_verification`: verify token/signature → `{"challenge": ...}`; bad token → `401 invalid_challenge`.

- [ ] **Step 1: e2e tests** (real server in-process per `tests/e2e/conftest.py` patterns): for each of 4 endpoints — valid signed event with bound agent → 200 dispatched + `task_executions` row `trigger='integration'` (assert DB); bad signature → 401 invalid_signature + `integration_events` rejected row + ZERO task_executions; replay (old timestamp) → 401; duplicate → 200 deduped; feishu challenge → echo; slack challenge → echo; challenge bad token → 401 invalid_challenge.
- [ ] **Step 2: Implement** routes + app wiring (`IntegrationService` on `app.state`, router include).
- [ ] **Step 3:** green → commit `feat(integrations): 入站回调端点 ×4(平台签名,裸 JSON)`.

## Task 7: external_identities — link/link-confirm/unlink

**Files:** Create `backend/src/mesh/integrations/identities.py`; routes in `routes.py`; tests `backend/tests/unit/test_integration_identities.py` + e2e negatives in `test_integrations_e2e.py`.

**Interfaces:**
- Produces: `external_identity_unlink_allowed(session, identity_id, member_id) -> bool` (executable reference: resolves member → `users.id`, compares to identity.user_id; role NOT consulted); `start_link(session, *, workspace_id, member, provider, integration_id, code_store, now) -> None` (sends one-time code via outbound adapter to the external account DM; dev adapter stores code in Redis `mesh:identity-code:<provider>:<tenant>:<attempt-key>` TTL 600s; code delivered ONLY to the external account — never returned in response); `confirm_link(...) -> dict` (match + unexpired + single-consume → INSERT mapping with `user_id = member→users.id`, `created_in_workspace_id = ws`; conflict → `409 identity_already_linked`); `unlink(session, *, workspace_id, member, identity_id)` → allowed iff `external_identity_unlink_allowed` else `403 identity_unlink_forbidden`.
- Routes: `GET /workspaces/{ws}/external-identities` (ONLY requester's own mappings via member→users.id), `POST /workspaces/{ws}/external-identities:link`, `POST /workspaces/{ws}/external-identities:link-confirm`, `DELETE /workspaces/{ws}/external-identities/{id}`. Link endpoints accept NO user/member target param (target fixed to requester).

- [ ] **Step 1: Failing tests** — unlink_allowed: owner via ANY workspace member row → True; other user who is ws admin → False; plain other member → False (reference impl only compares users.id); duplicate link confirm → 409; different tenants same user key coexist; user deletion cascades mapping (DB-level); link with foreign-user param rejected (schema has no such field).
- [ ] **Step 2: Implement** + audit log writes (link/unlink via `mesh.workspace.audit` helper if available, else `audit_logs` insert).
- [ ] **Step 3:** green → commit `feat(integrations): 外部身份建链/解链(全局映射,无 admin 旁路)`.

## Task 8: Card callback auth chain

**Files:** Create `backend/src/mesh/integrations/cards.py`; card endpoints in `inbound_routes.py`; tests `backend/tests/e2e/test_integrations_cards_e2e.py`.

**Interfaces:**
- Consumes: `decide_approval` (`mesh.runtime.approvals`), identities lookup.
- Produces: `async def handle_card_callback(session, *, kind, integration, raw_body, headers, signing_secret, now, tolerance, action: Literal["approve","reject"], decision_comment: str|None) -> tuple[int, dict]`: verify signature → extract clicker `(provider, tenant_key, external_user_key)` → `external_identities` lookup → `users.id` → JOIN `members(workspace_id, user_id)` active roster row → §6.10 permission check (human + (subject requester/dispatcher | agent owner | ws admin)) → on any failure `403 forbidden` with audit row, approval UNCHANGED → on success forward to `decide_approval(approval_id, member, action, decision_comment)` (idempotent repeat = no-op per §6.10).

- [ ] **Step 1: e2e tests** — mapped member with permission clicks approve → approval approved + execution resumes (queued new attempt) + `decision_comment` records IM source; unmapped external user → 403, approval stays pending; mapped user NOT member of this workspace → 403; mapped member without permission → 403; repeat click → idempotent no-op 200; multi-workspace: same mapping row serves workspace B after workspace A (link origin) deleted — `created_in_workspace_id` SET NULL, B callback still resolves (T29⑨).
- [ ] **Step 2: Implement.**
- [ ] **Step 3:** green → commit `feat(integrations): IM 卡片回调鉴权链(HIGH-1/R4)`.

## Task 9: vcs_links + identifier resolution + auto status flow

**Files:** Create `backend/src/mesh/integrations/vcs_links.py`; routes in `routes.py`; tests `backend/tests/unit/test_integration_vcs_links.py` + e2e.

**Interfaces:**
- Routes: `POST /api/v1/integrations/vcs/links`, `DELETE /api/v1/integrations/vcs/links/{id}` (status='deleted'), `GET /api/v1/issues/{issue_id}/vcs-links`, `POST /api/v1/integrations/vcs/resolve` (source_text + vcs_ref → extract `<PREFIX>-<N>` via `UNIQUE(workspace_id, identifier)` → auto link `link_source='auto_keyword|auto_branch|auto_commit'`; unresolved → 422 `identifier_not_resolved` audit, never blocks ingestion).
- Produces: `auto_link_from_event(session, *, workspace_id, integration, event)` used by the VCS ingestion path: repo must be bound (`integration_bindings` lookup by `(provider, tenant_key, owner/repo)`; unbound → audit only); identifier match → vcs_links insert (partial unique index hit → idempotent skip); `auto_status_map` → issue status transition via `IssueService` transition (validate target exists + legal; idempotent per event), refresh `external_state` + `status='stale'` on merged/closed, post comment ("PR #N 已合并,自动置为 done") via CommentService with §6.5 idempotency key.

- [ ] **Step 1: Failing tests** — manual link CRUD; duplicate active link same external object → 409 (partial unique); cross-issue steal of same PR → 409; deleted link frees the slot (re-link OK); integration kind must be vcs_* (422 `vcs_link_invalid`); identifier resolution happy path + unresolved audit; PR merged event → issue done + comment + stale + repeat event idempotent (status not re-changed, no duplicate comment).
- [ ] **Step 2: Implement.**
- [ ] **Step 3:** green → commit `feat(integrations): vcs_links 真源 + identifier 自动关联 + 状态流转`.

## Task 10: Outbound webhook subscriptions + delivery worker

**Files:** Create `backend/src/mesh/integrations/outbound.py`; modify `workers/main.py`, `config.py`; routes in `routes.py`; tests `backend/tests/unit/test_integration_outbound.py` + `backend/tests/e2e/test_integrations_outbound_e2e.py`.

**Interfaces:**
- Produces: `validate_subscription_url(url)` — scheme https else `400 invalid_url_scheme`; host forbidden (`is_forbidden_host` + DNS-resolved IPs checked at delivery) else `422 ssrf_blocked`. CRUD: `create_subscription(...)` (generates HMAC secret, stores ciphertext in `secret_ref`, returns plaintext ONCE), `list/get/patch/delete`, `resume_subscription(id)` (fail_count=0, status active), `retry_delivery(subscription_id, delivery_id)` (failed → pending, attempts preserved).
- Relay handler `webhook_dispatch_handler(session, event)`: for each subscription with matching `event_types` (empty = all) AND `status='active'`: INSERT delivery (`state='pending'`, `event_ref = event.payload['source_event_ref']`, UNIQUE conflict → skip).
- Derivation: extend the relay realtime-publish wrapper in `workers/main.py` — after projection, if any active subscription exists for the event name, `emit_event(event_type='webhook.dispatch', payload={event_type, data, source_event_ref: str(outbox_event.id)}, idempotency_key=f"webhook-dispatch:{outbox_event.id}")`.
- `WebhookDeliveryWorker(session_factory, settings, http_factory).run_forever()`: claim pending deliveries `next_retry_at IS NULL OR <= now()` FOR UPDATE SKIP LOCKED → SSRF re-check resolved IPs → `httpx.AsyncClient.post(url, content=body, headers={"Mesh-Signature": f"t={ts},v1={hmac_sha256(secret, f'{ts}.{body}')}", "Mesh-Event": event_type, "Mesh-Delivery": str(delivery_id)}, timeout=settings.webhook_delivery_timeout)` → 2xx: `state='sent'`, subscription `fail_count=0`; else `attempts+1`, `next_retry_at = now + min(base*2^attempts, max) * jitter(0.5..1.0)`, attempts > max → `state='failed'` + `subscription.fail_count+1`; fail_count > threshold → `status='disabled'` (breaker) + `integration.updated` realtime + critical log alert.
- Config: `webhook_delivery_max_attempts=8`, `webhook_delivery_base_seconds=30`, `webhook_delivery_max_seconds=3600`, `webhook_delivery_timeout_seconds=10`, `webhook_circuit_break_threshold=20`, `webhook_delivery_poll_interval=1.0`.

- [ ] **Step 1: Failing unit tests** — URL validation (http → 400; `https://10.0.0.1` → 422; `https://169.254.169.254` → 422; metadata hostname → 422); HMAC header format recomputation; backoff sequence bounded by max; breaker trips at threshold; resume clears fail_count; secret shown once (get returns no secret).
- [ ] **Step 2: e2e tests** (real local HTTPS receiver via `httpx.MockTransport` injection for unit; real `aiohttp`/`http.server` TLS NOT required — e2e uses an injected transport factory but REAL outbox→relay→worker flow and REAL DB ledger): domain event → `webhook.dispatch` derived → delivery row pending → worker POSTs (recorded) → sent; failure ×N → retries with increasing next_retry_at → failed after max; breaker: threshold consecutive failures → subscription disabled + further dispatch skipped (422 `subscription_circuit_open` on manual retry); duplicate outbox dequeue → single delivery row (UNIQUE); SSRF: subscription URL DNS-resolving to private IP (monkeypatch resolver) → delivery refused 422-state failed with `ssrf_blocked` last_error.
- [ ] **Step 3: Implement** + wire worker into supervisor (`workers/main.py` supervised task list) + handlers dict.
- [ ] **Step 4:** green → commit `feat(integrations): 出向 Webhook 订阅 + outbox 投递 + 重试退避/熔断`.

## Task 11: OAuth authorization-code + PKCE

**Files:** Create `backend/src/mesh/integrations/oauth.py`; routes in `routes.py`; tests `backend/tests/unit/test_integration_oauth.py`.

**Interfaces:**
- Routes: `GET /workspaces/{ws}/integrations/oauth/{kind}/authorize` (admin; generates PKCE pair + `state` stored in Redis `mesh:oauth-state:<state>` TTL 600s with `{ws, member_id, kind, code_verifier}`; 302 to provider authorize URL built from `config`/settings; dev mode → mock provider URL from `settings.oauth_mock_redirect_uris` pattern), `GET /integrations/oauth/{kind}/callback` (validates state → exchanges code+verifier via httpx POST (dev: mock endpoint returns canned token) → encrypts refresh token → creates/updates integration `secret_ref`, minimal scope param → 302 back to frontend integrations page with `?oauth=success`; failure → `422 oauth_failed` rendered page redirect `?oauth=error`).

- [ ] **Step 1: Failing tests** — authorize returns 302 with PKCE challenge (S256) + state; callback with unknown state → 400; callback success → integration row with encrypted secret (plaintext decryptable server-side, absent from any response); token exchange failure → oauth_failed.
- [ ] **Step 2: Implement** (reuse `mesh.auth.oauth` PKCE helpers if generic enough).
- [ ] **Step 3:** green → commit `feat(integrations): OAuth 授权码 + PKCE(最小 scope)`.

## Task 12: Admin routes + realtime channels + full wiring

**Files:** Create `backend/src/mesh/integrations/{routes,channels}.py` (routes may already partially exist from Tasks 7/9/10 — consolidate); modify `api/app.py`, `realtime/app.py`; tests `backend/tests/e2e/test_integrations_e2e.py` (management plane).

**Interfaces:**
- All §3.1 endpoints with `require_workspace("integration:manage")` writes / `require_workspace()` reads; rate limit 120/min per principal+IP on writes; `integration.updated` realtime emit (subject: integration|binding|subscription) on create/update/status/circuit-break; `integration:{id}` channel checker (workspace membership of the integration).

- [ ] **Step 1: e2e tests** — CRUD round-trips for integrations/bindings/subscriptions/deliveries-list/events-ledger with filters; RBAC: member (non-admin) write → 403; cross-tenant access → 404; events ledger shows rejected/deduped reasons; `integration.updated` received on WS channel with seq.
- [ ] **Step 2: Implement** consolidation + wiring.
- [ ] **Step 3:** green → commit `feat(integrations): 管理端点全套 + 实时事件 + 频道授权`.

## Task 13: Frontend — integrations management + webhooks + VCS sidebar

**Files:** Create `frontend/src/features/integrations/*` (api.ts, types.ts, IntegrationsPage, IntegrationDetailPage, BindingDrawer, EventLedger, WebhooksPage, SubscriptionDetail, ExternalIdentitiesCard); modify `App.tsx` (routes), settings/nav config, `features/issues/` detail sidebar (VCS links block); i18n `zh-CN.json`/`en.json` `integrations.*` keys.

- [ ] **Step 1:** API client module (envelope unwrap pattern per existing feature, e.g. `features/autopilots/api.ts`).
- [ ] **Step 2:** IntegrationsPage: connector catalog cards (Feishu/Slack/GitHub/GitLab/Outbound Webhook) + connected list (status badge, binding count, 7-day event count) + add dialog (kind → OAuth/paste-token masked `••••abcd`); states loading/empty/permission-denied per §6.12 matrix; disabled banner "已停用,入站事件将被拒绝".
- [ ] **Step 3:** IntegrationDetail: overview (read-only non-secret config + edit + rotate credential) / bindings tab (BindingDrawer: external ref selector + scope switch + match rules form + target agent selector with "留空=仅审计" hint) / events tab (signature/process status badges + payload preview labeled 不可信数据 + rejected/deduped highlight).
- [ ] **Step 4:** WebhooksPage + detail: subscription list (URL/filter/status/success rate), create dialog with one-time secret display, delivery timeline (state/attempts/response/next_retry countdown) + manual retry + breaker resume banner.
- [ ] **Step 5:** Issue detail sidebar "关联 PR / 提交" block (GET vcs-links; +关联 dialog with PR URL/SHA input; auto-flow entries marked with integration icon).
- [ ] **Step 6:** ExternalIdentitiesCard (settings/profile area: list own linked identities, link flow with code entry, unlink).
- [ ] **Step 7:** i18n keys both locales; run frontend coverage gate script (per `frontend/scripts/`) ≥90% per file; unit tests for api.ts + key components (vitest).
- [ ] **Step 8:** Commit `feat(integrations): 前端集成管理/绑定/事件台账/出向订阅/VCS 侧栏(i18n 双语)`.

## Task 14: Full e2e acceptance (T29 + §5 red lines) + docs + coverage + PR

**Files:** `backend/tests/e2e/test_integrations_e2e.py` (extend), docs (`README.md` status row, `backend/README.md`, `CHANGELOG.md`), spec sync if gaps found.

- [ ] **Step 1: T29 e2e** — ① cross-ws binding grab → 409 binding_conflict at INSERT; ② scope XOR both directions rejected; ③ project physical delete cascades project-level bindings (project delete succeeds); ④ vcs_links partial unique + composite FK cascade on integration delete; ⑤ single mapping row serves two workspaces (each JOIN members resolves); ⑥ different tenants same user key coexist; ⑦ duplicate mapping rejected even toward different users; ⑧ user deletion cascades mapping → card click 403; ⑨ link-origin workspace deletion → mapping survives (`created_in_workspace_id` NULL), other workspace callback resolves; ⑩ information_schema/pg_policies negatives; ⑪ unlink permission negatives incl. admin no-bypass via `external_identity_unlink_allowed`.
- [ ] **Step 2: §5.1–§5.5 red lines e2e** — signature reject ×4 endpoints + replay; dedup 200; pre-occupation proof; untrusted isolation; trigger idempotency (single enqueue); unmatched audit-only; disabled reject; challenge echoes; outbox crash recovery (commit then kill relay before dispatch → restart → execution queued); realtime `integration.event_ingested` replay with seq; feishu token cache/refresh (adapter-level with mock token endpoint, plaintext never logged); outbound HMAC verify at receiver, retry backoff ledger, breaker + resume, delivery idempotency, https-only + SSRF; credentials never echoed (scan GET responses + logs); cross-tenant 403/404 matrix; real DELETE behaviors (agent delete SET NULL bound_agent_id, integration hard-delete cascades bindings/events/vcs_links, soft-delete preserves).
- [ ] **Step 3: Real UI verification** — `docker compose up --build -d`; drive pages like a human (chrome-devtools): create integration, binding, view event ledger, create subscription (one-time secret), issue sidebar VCS block; screenshots attached to final report; dark-mode spot check.
- [ ] **Step 4: Coverage** — `pytest --cov=mesh --cov-report=term-missing` ≥90% overall AND new-code; frontend gate ≥90% per file.
- [ ] **Step 5: Docs** — README.md implementation-status row for integrations v0.18.0; backend/README.md module bullet + endpoint table; CHANGELOG entry; spec fix-ups ONLY if genuine gaps (report in issue comment, don't silently change requirements).
- [ ] **Step 6:** Anonymization scan on diff (reference-product names, benchmarking language) → clean. Full test suite green. Commit `test(integrations): T29 + §5 红线真实 e2e 全绿`, `docs(integrations): 文档同步 v0.18.0`.
- [ ] **Step 7:** Push branch, create PR (gh), request code review (requesting-code-review skill), post final issue comment with acceptor mention.
