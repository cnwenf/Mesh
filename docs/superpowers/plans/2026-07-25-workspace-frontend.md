# Workspace §4 前端接通 Implementation Plan (MES-26)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 接通 workspace.md §4 全量前端 UI —— 工作区切换器/创建向导、工作区设置页(基本信息/邀请/角色/危险区)、邀请接受页(各 reason UI 态)、RBAC 角色呈现,消费已合入 main 的 workspace 后端 v0.4.0 与前端脚手架 v0.3.0(+ MES-24 PR#15 接通成果)。

**Architecture:** React 18 + react-router 路由扩展(`/w/:workspaceSlug/*` 工作区上下文路由 + `/invite/:token` 公开接受页);新建 `src/workspace/` feature 目录(Provider/组件/页面);API 层延续 free-function-takes-client 约定扩展 `api/workspace.ts`,新增 `api/invitations.ts`、`api/members.ts`、`api/auth.ts`;realtime 经 AppShell 既有 RealtimeClient 订阅 `workspace:{id}` 频道(WS 首帧鉴权;JWT 需后端补 chained authenticator);文案 100% 外部化(zh-CN + en 双目录)。

**Tech Stack:** React 18.3 / react-router-dom 6.28 / zustand 5 / react-intl 7 / Vitest 3 + Testing Library(覆盖率 90% 门槛)/ Playwright(真实后端 e2e)/ FastAPI 后端(docker compose 真实栈)。

## Global Constraints

- 分支基于 `agent/mesh/a4f3b6c4`(MES-24 PR#15),其合入 main 后 rebase;**不重复实现** workspaceDefaultLocale 读取/偏好写入接通(MES-24),但修正其 `fetchWorkspaceDefaultLocale` 读列表 `settings` 的缺陷(列表响应不含 settings,须读 detail)并在 MES-24 评论标注吸收。
- 邀请重加入语义按 MES-14 pin 的 Leader 裁决:接受页 UI 视「重加入」为成功态(200),非新增错误 reason;MES-14 未合入前消费 v0.4.0 现行行为。
- 错误信封 `{"error":{"code","message","details?"}}`;成功包络单对象 `{"data":{...}}` / 列表 `{"data":[...],"next_cursor"}`。accept 失败 reason 在 `error.details.reason ∈ {not_found,expired,exhausted,revoked}`;preview 恒 200,`valid:false` 时 reason 同枚举。
- 角色 `owner/admin/member/guest`;设置页 admin+ 可见,危险区 owner-only,邀请管理 admin+;非成员一律 404(无存在性泄漏)。
- UI 文案一律经 i18n 消息目录外部化(zh-CN + en 键集一致,目录 version 须重算);错误码经 `errorToI18nKey` → `t('error.<code>')`。
- 新增/变更代码覆盖率 ≥90%(全局 90/90/90/90 门槛 + CI changed-line ≥90%)。
- git 身份 `cnwenf <cnwenf@outlook.com>`;`core.hooksPath=/dev/null`;提交无 Co-Authored-By;不暴露任何参考来源。
- 后端仅补 realtime JWT 鉴权链(前端消费 realtime 的必要管道),不重复造 workspace/invitation 业务 API。

## 后端契约摘要(消费依据,不重复定义)

- `GET /api/v1/workspaces` → 短项 `{id,name,slug,logo_url,my_role,created_at}`(**无 settings**);`GET /workspaces/{id}` 与 `/workspaces/by-slug/{slug}` → 全量 `{...settings,my_role,updated_at}`;by-slug 解析历史 slug 返回**当前** slug(重定向语义)。
- `POST /workspaces` `{name,slug,timezone?,logo_url?,settings?}` 201;`PATCH /workspaces/{id}`(admin+)浅合并 settings;`DELETE` body `{confirm_slug}`(owner);`POST /restore`(owner)。
- `POST /workspaces/{id}/invitations` `{emails?|link,role∈admin/member/guest,max_uses?,expires_in_hours?}` 201 → `data[]` 含 `invite_link`(仅创建返回);`GET .../invitations`(admin+)无 invite_link,惰性 expired;`DELETE .../invitations/{invId}` 撤销(非 active → 409 `conflict {status}`)。
- `GET /invitations/preview?token=` 公开恒 200;`POST /invitations/accept` `{token}` → 200 `{member,workspace}` 或 422 `invitation_invalid` + `details.reason`。
- `PATCH /workspaces/{id}/members/{memberId}` `{role}`(admin+)→ `{id,member_type,role,status}`;409 `last_owner`/`agent_owner_not_allowed`。**无成员列表端点**(MES-14 在途)→ 名册 UI 优雅降级。
- auth:`POST /auth/register` `{email,password,display_name}` 201;`POST /auth/login` `{email,password}` → `{access_token,refresh_token,expires_in:900}`;`GET /me`。错误:`invalid_credentials`(422)、`weak_password`(400,`details.reason`)、`conflict {field:email}`(409)。
- realtime:WS 首帧 `{op:'auth',token}` → `auth_ok`;`subscribe {channel:'workspace:<uuid>',resume_from}`;事件 `workspace.updated {workspace_id,changes}`、`workspace.deleted`、`member.added {member_id,member_type,role}`、`member.role_changed {member_id,old_role,new_role}`、`invitation.redeemed {invitation_id,member_id,used_count}`。REST 对账 `GET /api/v1/realtime/events?channel=&since=`(Bearer)。
- **缺口**:api/gateway 的 `current_principal`/WS 鉴权器仅 dev-token(dev)/Null(production),不接受会话 JWT → 本增量补 `JwtPrincipalAuthenticator` + `ChainedAuthenticator`(realtime/auth.py),两端 app 接线。

## File Structure

**后端(仅鉴权管道):**
- Modify `backend/src/mesh/realtime/auth.py` — 新增 `JwtPrincipalAuthenticator(session_factory, secret, algorithm)`(解码 access JWT → 查 user + active members → `Principal`)与 `ChainedAuthenticator(*authenticators)`;保持 `Authenticator` 协议不变。
- Modify `backend/src/mesh/api/app.py`、`backend/src/mesh/realtime/app.py` — 接线:production = JWT 鉴权器;dev = JWT + dev-token 链。
- Modify `backend/tests/unit/test_realtime_auth.py` — JWT/链式鉴权器单测(含过期/无效/非 active 用户/无成员)。
- Modify `backend/tests/e2e/test_realtime_gateway_e2e.py` — 补 JWT 首帧鉴权 e2e(真实用户订阅自己工作区频道成功;跨租户 forbidden)。

**前端 API/类型层:**
- Modify `frontend/src/types/entities.ts` — `WorkspaceRole`、`WorkspaceListItem`、`WorkspaceDetail`、`InvitationStatus`、`Invitation`、`InvitationPreview`、`AcceptInvitationResult`、`MemberSummary`、`SessionTokens`、`CurrentUser`。
- Modify `frontend/src/api/workspace.ts` — `listWorkspaces`、`fetchAllWorkspaceSummaries`、`getWorkspace`、`getWorkspaceBySlug`、`createWorkspace`、`updateWorkspace`、`deleteWorkspace`、`restoreWorkspace`;**修正** `fetchWorkspaceDefaultLocale`(列表→detail)。
- Create `frontend/src/api/invitations.ts` — `createInvitations`、`listInvitations`、`revokeInvitation`、`previewInvitation`、`acceptInvitation`。
- Create `frontend/src/api/members.ts` — `listMembers`(member.md §3.2 契约,MES-14 合入后生效)、`updateMemberRole`。
- Create `frontend/src/api/auth.ts` — `login`、`register`、`fetchMe`。
- Modify `frontend/src/api/index.ts` — barrel 导出。
- Tests:`api/__tests__/workspace.test.ts`(扩展)、`invitations.test.ts`、`members.test.ts`、`auth.test.ts`。

**前端 workspace feature(`src/workspace/`):**
- `permissions.ts` — `canViewSettings/canManageInvitations/canDeleteWorkspace(role)`、`INVITATION_ROLES`、`ROLE_ORDER`。
- `WorkspaceProvider.tsx` — 上下文(by-slug 加载 + 规范化 slug 路由替换 + patch + refresh + realtime 帧合并 + 404/loading/error 态);导出 `useWorkspace()` 与 null-safe `useOptionalWorkspace()`。
- `WorkspaceSwitcher.tsx` — TopBar 下拉(Dialog 列表 + 当前标记 + 创建入口 + 切换导航)。
- `CreateWorkspaceWizard.tsx` — 三步 Dialog(名称 → slug 实时校验 → 可选邮箱邀请)。
- `EmailChipsInput.tsx` — 邮箱 chip 输入(回车/粘贴批量,去重,格式校验)。
- `InvitationCreatePanel.tsx` — 模式切换(邮箱/链接)、角色选择、max_uses/expires_in_hours(默认 10/168,上限提示)、caps 错误呈现、invite_link 复制卡。
- `InvitationList.tsx` — 列表(游标分页 load-more)、状态徽标(4 态 + used/max)、过期时间本地化、撤销。
- `RolesMatrix.tsx` — 角色矩阵表 + 名册区(消费 listMembers;端点缺失优雅降级)+ 行内角色变更(updateMemberRole;last_owner/agent_owner_not_allowed 错误呈现)。
- `DangerZone.tsx` — slug 二次确认删除(owner-only)。
- `pages/WorkspaceHomePage.tsx` — 工作区概览(名称/slug/my_role/默认 locale;admin+ 设置入口)。
- `pages/WorkspaceSettingsPage.tsx` — 节区组装(基本信息表单内联:名称/slug/logo_url/timezone/default_locale + 各 422/409/400 呈现;admin+ 门控,member 见 permission-denied 态)。
- `pages/InviteAcceptPage.tsx` — preview 加载 → 有效卡(登录门控 `?next=` 回跳;接受 → 成功态/重加入成功态;4 种 reason UI 态)。
- Tests:`workspace/__tests__/*.test.tsx`(每组件/页一个测试文件)。

**前端 shell/路由/登录:**
- Modify `src/App.tsx` — 新增路由:`/w/:workspaceSlug`(WorkspaceHomePage)、`/w/:workspaceSlug/settings`(WorkspaceSettingsPage)、`/invite/:token`(InviteAcceptPage)。
- Modify `src/shell/AppShell.tsx` — `useMatch('/w/:workspaceSlug/*')` 命中时以 `WorkspaceProvider` 包裹布局子树(TopBar/Sidebar/Outlet 均可消费上下文)。
- Modify `src/shell/TopBar.tsx` — 挂载 `WorkspaceSwitcher`。
- Modify `src/shell/Sidebar.tsx` — 工作区上下文内 admin+ 显示「工作区设置」入口。
- Modify `src/shell/pages/LoginPage.tsx` — 真实邮箱/密码登录 + 注册切换(消费 auth.ts),保留 dev token 输入(既有 mock e2e 依赖 `login-token`/`login-submit` testid);支持 `?next=` 登录后回跳。
- Modify `src/hooks/useWorkspaceLocale.ts` — 仅当 workspace.ts 修正影响签名时随动(预期不变)。

**i18n:** 两份目录新增 `workspace.*`、`wsCreate.*`、`invitations.*`、`invite.*`、`roles.*`、`danger.*`、`auth.*` 键(~110 条,zh-CN + en 对齐),version 经 `computeCatalogVersion` 重算。

**E2E / 文档:**
- Create `frontend/e2e/workspace-flow.spec.ts`(真实后端全流程;psql 直驱构造 expired 等态)。
- Modify `frontend/playwright.config.ts`(testIgnore 追加)、`frontend/playwright.real.config.ts`(testMatch 追加)。
- Modify `README.md`、`CHANGELOG.md`(v0.5.0 条目)。

---

## Task 1: 后端 realtime JWT 鉴权链

**Files:** Modify `backend/src/mesh/realtime/auth.py`、`backend/src/mesh/api/app.py`、`backend/src/mesh/realtime/app.py`;Test `backend/tests/unit/test_realtime_auth.py`、`backend/tests/e2e/test_realtime_gateway_e2e.py`

**Interfaces:** 产出 `JwtPrincipalAuthenticator(session_factory, *, jwt_secret, algorithm)` 与 `ChainedAuthenticator(authenticators: Sequence[Authenticator])`,均实现 `async authenticate(token) -> Principal | None`;两端 app 的 `app.state.authenticator` 改为:dev = `ChainedAuthenticator([Jwt..., DevToken...])`,production = `JwtPrincipalAuthenticator(...)`。JWT 解码复用 `mesh.auth.jwt.decode_access_token`(失败 → None);`Principal.subject=str(user.id)`,`workspace_ids` = 该 user 的 active members 的 workspace_id 集合(仅 `status='active'`)。

- [ ] 单测先行:有效 JWT → Principal(含工作区集合);过期/无效签名/非 JWT → None;user 不存在/非 active → None;无成员 → 空集合。
- [ ] e2e:注册用户 + 建工作区 → WS 首帧 JWT 鉴权 `auth_ok`,订阅 `workspace:{id}` 成功收 `subscribed`;订阅非成员工作区 → `error forbidden`;REST `GET /api/v1/realtime/events?channel=workspace:{id}` JWT 200。
- [ ] `cd backend && python -m pytest tests/unit/test_realtime_auth.py tests/e2e/test_realtime_gateway_e2e.py -q` 全绿。
- [ ] Commit:`feat: realtime 鉴权链接入会话 JWT(api/gateway 首帧与 REST 对账,workspace.md §3.5/§6.16)`

## Task 2: 前端类型与 API 层(auth + workspace 扩展 + invitations + members)

**Files:** Modify `src/types/entities.ts`、`src/api/workspace.ts`、`src/api/index.ts`;Create `src/api/auth.ts`、`src/api/invitations.ts`、`src/api/members.ts`;Tests 对应 `__tests__`。

**Interfaces:**
- `WorkspaceRole = 'owner'|'admin'|'member'|'guest'`;`WorkspaceListItem {id,name,slug,logo_url,my_role:WorkspaceRole,created_at}`;`WorkspaceDetail {id,name,slug,logo_url,timezone,settings:WorkspaceSettings,my_role,created_at,updated_at}`。
- `InvitationStatus = 'active'|'revoked'|'expired'|'exhausted'`;`Invitation {id,email:string|null,role,status,max_uses,used_count,expires_at,token_prefix,invited_by,created_at,invite_link?}`;`InvitationPreview = {valid:true,workspace_name,workspace_logo_url,role,expires_at} | {valid:false,reason:InvitationRejectReason}`;`InvitationRejectReason = 'not_found'|'expired'|'exhausted'|'revoked'`;`AcceptInvitationResult {member:{id,role,status},workspace:{id,name,slug}}`。
- `MemberSummary {id,member_type:'human'|'agent',role,status,display_name?,joined_at?}`(member.md §3.2 消费契约)。
- `SessionTokens {access_token,token_type,expires_in,refresh_token}`;`CurrentUser {id,email,display_name,timezone,settings:{locale?,theme?},...}`。
- workspace.ts:`listWorkspaces(client, query?): Promise<ListEnvelope<WorkspaceListItem>>`、`fetchAllWorkspaceSummaries(client)`(fetchAllPages)、`getWorkspace(client, id)`、`getWorkspaceBySlug(client, slug)`、`createWorkspace(client, input)`、`updateWorkspace(client, id, patch): Promise<WorkspaceDetail>`、`deleteWorkspace(client, id, confirmSlug)`、`restoreWorkspace(client, id)`;`fetchWorkspaceDefaultLocale` 改为取首个列表项后 `getWorkspace` 读 detail `settings.default_locale`(列表无 settings;无工作区/失败 → null)。
- auth.ts:`login(client, {email,password})`、`register(client, {email,password,display_name})`、`fetchMe(client)`。
- invitations.ts:`createInvitations(client, wsId, {emails?,role,max_uses?,expires_in_hours?}): Promise<Invitation[]>`(201 data 数组)、`listInvitations(client, wsId, query?)`、`revokeInvitation(client, wsId, invitationId)`、`previewInvitation(client, token)`(query token)、`acceptInvitation(client, token)`。
- members.ts:`listMembers(client, wsId, query?)`、`updateMemberRole(client, wsId, memberId, role): Promise<MemberSummary>`。

- [ ] 每个函数:fetchStub 单测(URL/方法/body/包络解包/错误 code+details 透传,含 invitation_invalid reason、invitation_limits_exceeded caps、slug_taken、conflict、last_owner)。
- [ ] `fetchWorkspaceDefaultLocale` 修正测试:列表 → detail 两次调用;detail 失败 → null。
- [ ] `npm run test -- api` 全绿。
- [ ] Commit:`feat: workspace/invitation/member/auth API 客户端与实体类型(workspace.md §3,消费 v0.4.0 后端)`

## Task 3: i18n 目录扩充(zh-CN + en)

**Files:** Modify `src/i18n/catalogs/en.json`、`zh-CN.json`。

- [ ] 新增键集(两语言 1:1):`workspace.*`(switcher/create/home/settings 节区/字段/提示/状态)、`wsCreate.*`(向导步骤/slug 校验绿勾红叉/邀请步骤)、`invitations.*`(创建表单/chips/角色/上限/链接卡/列表列/4 状态徽标/撤销/ redeemed 提示)、`invite.*`(接受页:加载/有效卡/登录提示/成功/重加入成功/4 reason 态)、`roles.*`(矩阵行与能力列/名册区/角色变更/降级态/last_owner 等错误)、`danger.*`(删除区/确认输入/错误)、`auth.*`(登录/注册表单/错误码文案)、`error.invitation_invalid`/`error.invitation_limits_exceeded`/`error.slug_taken`/`error.last_owner`/`error.agent_owner_not_allowed`/`error.invalid_credentials`/`error.weak_password`/`error.account_locked`(补 §6.14 具名码)。
- [ ] `version` 字段经 `computeCatalogVersion(messages)` 重算写入。
- [ ] `npm run test -- i18n` 全绿(键集一致性 + version 校验)。
- [ ] Commit:`feat: workspace §4 文案目录(zh-CN + en 外部化,README §6.18)`

## Task 4: permissions + WorkspaceProvider + 路由接线

**Files:** Create `src/workspace/permissions.ts`、`src/workspace/WorkspaceProvider.tsx`;Modify `src/App.tsx`、`src/shell/AppShell.tsx`;Create `src/workspace/pages/WorkspaceHomePage.tsx` + tests。

**Interfaces:**
- `permissions.ts`:`canViewSettings(role): boolean`(owner/admin)、`canManageInvitations(role)`(同上)、`canDeleteWorkspace(role)`(owner)、`canManageMembers(role)`(同上)、`INVITATION_ROLES: readonly ['admin','member','guest']`。
- `WorkspaceProvider {slug, children}`:by-slug 加载 → 上下文值 `{workspace:WorkspaceDetail, isAdmin, isOwner, refresh(), patch(changes):Promise<WorkspaceDetail>}`;加载中 Skeleton 态;404 → not-found 呈现(与不存在同一文案,无泄漏);`workspace.slug !== slugParam` 时 `navigate(replace)` 规范化;realtime:经 `useRealtimeContext()` 订阅 `workspace:{id}`,合并 `workspace.updated`(changes 浅合并进 workspace)、`workspace.deleted`(toast + navigate('/'));导出 `useWorkspace()`(上下文外抛错)与 `useOptionalWorkspace()`(null)。
- AppShell:`const wsMatch = useMatch('/w/:workspaceSlug/*')` → 命中则以 `<WorkspaceProvider slug>` 包裹 TopBar/Sidebar/Outlet 子树。
- 路由:`/w/:workspaceSlug` → WorkspaceHomePage(概览 + admin+ 设置入口)、`/w/:workspaceSlug/settings` → WorkspaceSettingsPage(Task 6)。

- [ ] permissions 单测全矩阵。
- [ ] Provider 测试:加载成功/404/slug 规范化替换/workspace.updated 合并/workspace.deleted 导航。
- [ ] HomePage 测试:渲染字段、角色门控设置入口。
- [ ] Commit:`feat: 工作区上下文路由与 Provider(/w/:slug,by-slug 重定向,realtime 合并,workspace.md §4.1)`

## Task 5: 切换器 + 创建向导 + 登录真实化

**Files:** Create `src/workspace/WorkspaceSwitcher.tsx`、`src/workspace/CreateWorkspaceWizard.tsx`、`src/workspace/EmailChipsInput.tsx`;Modify `src/shell/TopBar.tsx`、`src/shell/pages/LoginPage.tsx`、`src/state/authStore.ts`(可选:存 displayName);tests。

**Interfaces:**
- WorkspaceSwitcher:TopBar 按钮(工作区上下文内显示当前名,否则 `t('workspace.switcher.title')`)→ Dialog 列出 `fetchAllWorkspaceSummaries`(名称/slug/my_role 徽标/当前标记)→ 点击切换 `navigate('/w/{slug}')`;顶部「创建工作区」→ 向导。
- CreateWorkspaceWizard:三步(名称 1–80 → slug `^[a-z0-9-]{2,32}$` 格式校验 + by-slug 探测占用(200 已占用/404 可用,绿勾红叉)→ 可选邮箱 chips(可跳过))→ `createWorkspace` → 有邮箱则 `createInvitations`(best-effort,失败 toast 不阻塞)→ `navigate('/w/{slug}')`。409 slug_taken/400 validation_error 呈现。
- EmailChipsInput:受控 `value:string[]` + `onChange`;回车/逗号/粘贴(逗号/分号/换行分割)成 chip;格式校验(非法红chip + 提示);去重小写归一;上限 50 提示。
- LoginPage:新增邮箱/密码登录表单(`auth.login` → `setToken(access_token)` → `navigate(next ?? '/')`)+ 注册切换(`auth.register` 成功后自动登录);错误具名码文案(invalid_credentials/weak_password 三 reason/conflict);**保留** dev token 输入(`data-testid="login-token"`/`login-submit`,mock e2e 不破);支持 `?next=` 回跳。

- [ ] 各组件交互测试(userEvent):chips 增删/去重/粘贴批量;向导步进/slug 校验态/创建导航/邀请失败不阻塞;切换器列表/切换/当前标记。
- [ ] LoginPage 测试:登录成功存 token 跳转/失败具名码/注册流/next 回跳/dev token 路径仍可用。
- [ ] Commit:`feat: 工作区切换器与创建向导(§4.2/§4.3)+ 登录页真实账号登录(auth v0.2.0 接通)`

## Task 6: 设置页(基本信息 + 邀请面板 + 角色 + 危险区)

**Files:** Create `src/workspace/InvitationCreatePanel.tsx`、`src/workspace/InvitationList.tsx`、`src/workspace/RolesMatrix.tsx`、`src/workspace/DangerZone.tsx`、`src/workspace/pages/WorkspaceSettingsPage.tsx`;tests。

**Interfaces:**
- SettingsPage:`canViewSettings(my_role)` 门控(否 → permission-denied 态,§6.12 矩阵);节区:基本信息 / 邀请(admin+)/ 成员与角色(admin+)/ 危险区(owner)。
- 基本信息表单:名称/logo_url(https-only)/slug(变更提示「旧链接将自动重定向」,成功 → 规范化导航)/timezone(Select,常用 + 浏览器时区)/default_locale(zh-CN/en Select)→ `updateWorkspace`;错误呈现:`slug_taken`、`unsupported_locale`(details.supported)、`invalid_timezone`、`validation_error`(logo/名称);成功 toast + 上下文刷新。
- InvitationCreatePanel:模式切换(邮箱 chips / 链接);角色 Select(INVITATION_ROLES);max_uses 数字(默认 10,上限 `settings.invitation_max_uses_cap ?? 100` 提示)/expires_in_hours(默认 168,上限 `invitation_max_lifetime_hours_cap ?? 720`,天/时呈现);提交 → `createInvitations`;链接模式成功 → invite_link 卡(完整 URL = origin + invite_link,复制按钮,一次性提示);`invitation_limits_exceeded`(details.max_uses/cap 或 expires_in_hours/cap 具名文案)、409 conflict(email)呈现。
- InvitationList:`listInvitations` 游标分页(load-more);列:邮箱/链接前缀(token_prefix)、角色、状态徽标(active/revoked/expired/exhausted,叠加 used_count/max_uses 文本)、expires_at(formatWithZoneAnnotation,用户时区)、撤销按钮(仅 active)→ `revokeInvitation`(409 conflict {status} → toast 并刷新);realtime `invitation.redeemed` 帧合并 used_count(达 max → exhausted 徽标)。
- RolesMatrix:角色 × 能力矩阵表(设置/邀请/成员管理/删除四列,owner/admin/member/guest 行);名册区:`listMembers`(404/405 → 降级提示「成员名册接口随 member 增量提供」,非错误态);行内角色 Select(admin+ 且非唯一 owner 表象,后端强校验兜底)→ `updateMemberRole`(409 last_owner/agent_owner_not_allowed 具名文案);realtime `member.added`/`member.role_changed` 触发刷新。
- DangerZone(owner):删除 → Dialog 输入 slug 确认 → `deleteWorkspace`(400 confirm 错误/403 非 owner 兜底)→ 成功 toast + navigate('/')。

- [ ] 每节区组件测试(含全部错误分支与 realtime 合并)。
- [ ] SettingsPage 门控测试(member 不可见节区/owner 全可见)。
- [ ] Commit:`feat: 工作区设置页(基本信息/邀请全生命周期/角色矩阵/危险区,workspace.md §4.2-§4.4)`

## Task 7: 邀请接受页(全 reason UI 态)

**Files:** Create `src/workspace/pages/InviteAcceptPage.tsx`;Modify `src/App.tsx`(路由 `/invite/:token`);tests。

**Interfaces:** token 取路径参数;挂载即 `previewInvitation`(公开):加载 Skeleton;`valid:true` → 卡片(workspace_name/logo/role 徽标/expires_at 本地化)+ 接受按钮:无 token → 导航 `/login?next=/invite/<token>`;有 token → `acceptInvitation` → 成功态(工作区名 + 「进入工作区」→ `/w/{slug}`;重加入同成功态);422 `invitation_invalid` → `details.reason` 四态 UI(not_found/expired/exhausted/revoked,各自标题+描述,不泄漏工作区信息);`valid:false` → 同四态。token 不打入日志/testid 值。

- [ ] 测试:preview 两分支 × 登录态 × accept 成功/四 reason/重加入幂等。
- [ ] Commit:`feat: 邀请接受页(preview + accept,四 reason UI 态,重加入成功态,workspace.md §4.3/§4.4)`

## Task 8: 真实后端 e2e + UI 实际操作验证

**Files:** Create `frontend/e2e/workspace-flow.spec.ts`;Modify 两个 playwright config;不改 mock 套件。

**E2E 流程(docker compose 真实栈,psql 直驱辅助):**
1. 用户 A 注册/登录(真实表单)→ 创建工作区(向导,slug 校验绿勾)→ 进入 `/w/{slug}`。
2. 设置页改名称/default_locale=zh-CN → 保存成功 toast;非法态经 API 旁路验证 422 信封消费(页面错误呈现经单测覆盖)。
3. 邀请创建:链接模式 max_uses=1 → 复制 invite_link;邮箱模式 → 列表出现 active 行。
4. 用户 B(新浏览器上下文)打开 invite_link → preview 卡 → 注册登录(经 ?next= 回跳)→ 接受 → 成功态 → 进入工作区(B 为 member)。
5. 用户 C 接受同一链接(max_uses=1 已耗尽)→ exhausted reason UI。
6. psql 将另一邀请 expires_at 置过去 → 打开链接 → expired reason UI;撤销一邀请 → revoked reason UI;随机 token → not_found UI。
7. 重加入:B 再接受新邀请 → 200 成功态。
8. 越权:B(member)导航 `/w/{slug}/settings` → permission-denied 态;设置 nav 入口不可见;B 访问 A 的**另一**工作区 slug → 404 同不存在文案。
9. 危险区:admin(B 被提权经 PATCH role——经 API)非 owner 删除 → 不可见;owner 输错 slug → 错误;输对 → 删除 → 全员 workspace.deleted(开关页跳转)。
10. zh-CN/en 切换验证目录外部化(关键页面文案随语言切换)。
11. realtime:设置页开着,A 端改名 → B 端(或同页)经轮询/WS 见更新(invitation.redeemed used_count 实时 +1)。

- [ ] `docker compose up --build -d postgres redis api worker gateway`(后端 main 镜像)→ `npx playwright test -c playwright.real.config.ts` 全绿。
- [ ] chrome-devtools MCP 真人式操作复验关键路径并截图留证(ui-visual-verification)。
- [ ] Commit:`test(e2e): workspace §4 真实后端全流程 e2e(邀请生命周期/越权负向/多语言/realtime)`

## Task 9: 质量门 + 文档 + PR + 完工报告

- [ ] `npm run lint && npm run typecheck && npm run test:coverage && node scripts/verify-coverage.mjs --base origin/main && npm run build && npm run test:e2e`(mock 套件不破)全绿;后端 `pytest` 全绿。
- [ ] README.md(前端能力矩阵补 workspace §4)+ CHANGELOG.md v0.5.0 条目;检查无参考来源字样。
- [ ] git 自查:`git log @{u}..HEAD --format=%B | grep -i co-authored-by` 无输出;author/committer 均 cnwenf。
- [ ] PR 合入流程(若 PR#15 先合入 → rebase main;CI 全绿)。
- [ ] MES-26 完工评论:覆盖率/e2e/真实操作截图证据/PR 链接,@Mesh 验收员 mention 请求验收;MES-24 评论标注 fetchWorkspaceDefaultLocale 修正吸收。

## Self-Review

- **Spec 覆盖**:W1 创建(Task 5)/ W2 列表切换(Task 5)/ W3 slug 寻址与重定向(Task 4)/ W4 设置(Task 6)/ W6 slug 历史重定向提示(Task 6)/ W7–W9 邀请全生命周期(Task 6/7/8)/ W10 软删除(Task 6)/ W11 工作区级配置 default_locale(Task 6);§4.2 组件全覆盖;§4.3 四流程(Task 5/6/7);§4.4 状态四态(Task 6/7);§4.5 realtime(Task 4/6 + 后端 Task 1);验收负向(Task 8);i18n(Task 3);覆盖率门槛(每 Task + Task 9)。
- **协调点**:MES-24 成果复用(基于 PR#15)+ 缺陷修正标注;MES-14 未合入 → 名册降级 + 重加入成功态消费 v0.4.0。
- **类型一致性**:API 签名与组件消费在 Task 2 统一定义,后续任务引用同名。
