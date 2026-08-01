# MES-90 钉钉前端 UI 实施计划

> 按 writing-plans → test-driven-development → systematic-debugging →
> verification-before-completion → requesting-code-review 的顺序执行。

**目标:** 对齐 `docs/specs/features/integrations.md` §4 与 §5.6，在现有集成模块中完成
钉钉企业内部应用机器人的创建/连接、双接收诊断、消息队列、命令体验、互动卡片预览、
权限隔离与真实浏览器验收。

**架构:** 复用现有 `/integrations` 与 `/integrations/:id` 信息架构；在契约层登记
`im_dingtalk`、Stream 状态、队列/summary/cancel/audit、test-send 类型；用三个聚焦组件承载
钉钉详情、队列和卡片预览。`integration.queue_updated` 只作为失效通知：workspace 项按
`conversation_key` 重拉，project 项不读取会话键并重拉完整授权切片，绝不本地 patch 位置。
普通队列端点永不展示孤儿；audit 入口仅 admin/owner 渲染。所有密钥仅通过顶层 `secret`
提交，由后端落 `secret_ref`，`config` 只持有非密字段。

**技术栈:** React 19 / TypeScript 5.7 / Vite / Vitest + Testing Library /
Playwright / 现有 FastAPI + PostgreSQL + Redis 真实栈。

## Task 1：契约层与纯函数（测试先行）

**修改:**

- `frontend/src/features/integrations/types.ts`
- `frontend/src/features/integrations/api.ts`
- `frontend/src/features/integrations/format.ts`
- 对应 `__tests__/types.test.ts`、`api.test.ts`、`format.test.ts`

**步骤:**

- [x] 先写失败测试：`im_dingtalk` 目录、非 OAuth、provider/identity `dingtalk`、Stream 状态、
      queue item/summary/audit/test-send 契约。
- [x] 实现 API：`testDingTalkSend`、`getDingTalkStreamStatus`、`listQueueItems`、
      `getQueueSummary`、`cancelQueueItem`、`listQueueAudit`、HTTP callback URL builder。
- [x] 实现纯函数：状态收窄/色调、会话名、摘要控制字符净化并限 120 字、身份三元组、
      运行时长与队列分组。
- [x] 定向 Vitest 转绿。

## Task 2：钉钉创建流程与列表连接状态（测试先行）

**修改:**

- `frontend/src/features/integrations/IntegrationsPage.tsx`
- `frontend/src/features/integrations/integrations.css`
- `frontend/src/features/integrations/__tests__/IntegrationsPage.test.tsx`

**步骤:**

- [x] 先写失败组件测试：第六张钉钉卡、专用 app_key/app_secret/corp_id、默认 Stream、
      HTTP/Stream 切换、默认 `final_only`、明文 secret 不进 config。
- [x] 钉钉专用结构化表单；HTTP 模式说明回调地址，Stream 模式说明免公网；提交配置固定
      `inbound_queue='serial_conversation'`，scope 凭据仍由后端密文存储。
- [x] 已连接列表对 Stream 集成读取持久状态并显示 connected/reconnecting/down 文本状态点；
      HTTP 模式显示接收模式而不伪造连接态。
- [x] `integration.updated(subject='stream_channel')` 沿既有整列重拉路径刷新。

## Task 3：详情诊断与结构化钉钉设置（测试先行）

**新增:**

- `frontend/src/features/integrations/DingTalkOverviewPanel.tsx`
- `frontend/src/features/integrations/__tests__/DingTalkOverviewPanel.test.tsx`

**修改:**

- `frontend/src/features/integrations/IntegrationDetailPage.tsx`
- 对应详情页测试。

**步骤:**

- [x] 先写失败测试：Stream 状态卡、最近心跳、退避、down/reconnecting/disabled、HTTP callback
      复制、test-send 与 receive diagnostic 两个独立动作及各自结果态。
- [x] Stream 首屏调用 `stream-status`；503 `stream_channel_unavailable` 从错误 details 恢复状态体，
      只在接收诊断显示，不污染 test-send。
- [x] test-send 对话框支持群/单聊目标；单聊要求 user_key。
- [x] 结构化编辑 receive mode / verbosity（默认 final_only）/ ack template，并保留 app_key、corp_id
      等非密 config；凭据轮换仍走专用密文入口。

## Task 4：消息队列、实时失效与权限（测试先行）

**新增:**

- `frontend/src/features/integrations/IntegrationQueuePanel.tsx`
- `frontend/src/features/integrations/__tests__/IntegrationQueuePanel.test.tsx`

**修改:**

- `frontend/src/features/integrations/IntegrationDetailPage.tsx`
- `frontend/src/features/integrations/integrations.css`

**步骤:**

- [x] 先写失败测试：会话分组、summary、在途/停止中/终态、排队位置、target agent AI 徽标、
      sender 未连接、摘要净化、执行深链、空态与错误重试。
- [x] 初次拉取授权队列 + summary + 当前用户 external identities；cancel 仅 admin/owner 或当前用户
      完整身份三元组匹配的 pending 项可见，后端继续权威兜底。
- [x] workspace `queue_updated` 携 conversation_key → 只 refetch 该会话；project payload → 忽略任何
      会话字段、重拉完整授权切片；位置全部取 refetch 响应，绝不本地增减。
- [x] realtime 不可用时 4 秒轮询；卸载清理。
- [x] audit 入口仅 admin/owner；普通视图不调用 audit，孤儿仅 audit 子面板显示。

## Task 5：命令体验与互动卡片预览（测试先行）

**新增:**

- `frontend/src/features/integrations/DingTalkInteractionGuide.tsx`
- `frontend/src/features/integrations/__tests__/DingTalkInteractionGuide.test.tsx`

**步骤:**

- [x] `/btw <补充>`、`/stop [原因]`、`/help` 输入 affordance 与复制反馈；明确命令在钉钉内输入、
      不进任务队列。
- [x] 展示 ack `✅ 已接收，处理中`、排队位置、`正在停止`/`已停止` 两段状态文案。
- [x] 审批卡预览覆盖 loading、批准/拒绝成功并禁按钮、重复 no-op、过期、无权、失败；过期/失败
      提供 `[回 Mesh 处理]`；通知卡显示 final_only/progress 差异。
- [x] 卡片预览只呈现状态，不发审批写请求，不伪造成审批真源。

## Task 6：i18n、样式与文档

**修改:**

- `frontend/src/i18n/catalogs/zh-CN.json`
- `frontend/src/i18n/catalogs/en.json`
- `frontend/src/features/integrations/integrations.css`
- `frontend/README.md`
- `README.md`

**步骤:**

- [x] 中英文键集同步，所有用户可见文案外部化。
- [x] 桌面/平板/手机响应式队列卡与预览；语义色只用 design tokens；状态不只靠颜色。
- [x] 文档补钉钉 UI、诊断分离、队列失效 refetch 与权限边界。

## Task 7：真实 e2e、覆盖率、评审与交付

**新增/修改:**

- `frontend/e2e/real-dingtalk-ui.spec.ts`
- 专用 Playwright 配置/真实栈测试门（如需要）
- `frontend/e2e/evidence/mes90-dingtalk/*`

**步骤:**

- [x] 真实起服务 + PostgreSQL + Redis + worker，浏览器像真人操作：创建 Stream、切换 HTTP、
      绑定 workspace/project、test-send、receive diagnostic、签名入站/排队、真实 runtime claim、
      `/btw` 上下文追加、`/stop` 两阶段状态、签名卡片回调与 queue realtime refetch；逐步截图。
- [ ] 使用真实钉钉测试企业复验 Stream 建连、平台侧 ack/最终结果和卡片点击（本地测试不伪称该外部平台门禁已完成）。
- [x] 私有项目负向：无项目可见性的普通成员 API/UI 均看不到队列项；project WS payload 不携
      conversation_key；普通成员无 audit 入口。
- [x] 运行 integration 模块定向 Vitest，再运行 `npm run lint`、`npm run typecheck`、
      `npm run test:coverage`、变更覆盖率校验、`npm run build`、关键 Playwright。
- [x] 完成前系统化审查：密钥泄露、项目隔离、失效通知、本地 patch、全文泄露、可访问性、
      移动端、i18n、无硬编码颜色/emoji 图标。
- [x] 请求代码评审；处理反馈后重新验证。
- [ ] 配置 cnwenf 提交身份与禁用 hooks，提交、检查无 co-author，push 并创建含 `Closes MES-90`
      的 draft PR；将 PR URL 写入 issue metadata，发布唯一最终评论，状态转 `in_review`。
