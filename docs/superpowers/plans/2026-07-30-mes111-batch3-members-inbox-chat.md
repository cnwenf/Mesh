# MES-111 批次③ 成员/Agent 名册 + 收件箱 + 聊天 设计对齐 实施计划(MES-126)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development(先红后绿)+ superpowers:verification-before-completion(真浏览器/真后端取证)。Steps use checkbox (`- [x]`) syntax;本计划为已落地实现的回溯式 TDD 记录,每步标注红→绿取证。

**Goal:** 按 `docs/specs/features/design-quality.md` §3.2/§4.4/§7.2/§7.6/§8.2/§9.8 与 `frontend/competitor-parity-checklist.md` 成员/通知/聊天条目,逐页落地**成员/Agent 名册 + 收件箱 + 聊天**的视觉/排版/交互升级;沉淀 `ConversationLayout`/`RunStateBadge` 至 patterns 层;收口验收/安全审核发现的读路径 500、读门越权、真浏览器级联与 CI 配置缺陷。UT 逐文件 ≥90%、真实 e2e 全绿、四组合存证 md5 全互异、CI 全绿。

**Architecture:** 设计系统分层 `features → patterns → primitives → foundations`(§11.1)。新增 patterns:`ConversationLayout`(列表/详情双栏;手机 ≤720px 经 `activePane` 单栏路由化,组件不读路由/窗口宽,断点集中于 CSS)、`RunStateBadge`(§9.8 五态统一语言 `queued/running/waiting/succeeded/failed` + 派生 `idle/unknown`,tone/图标单一事实源 + `data-state` 钩子)。聊天附件读路径在 `chat/service.py` 经 `attachment_blobs` JOIN 取快照字段,读门 `_can_read_host` 的 chat_message 分支镜像写侧 L2 属主校验。

**Tech Stack:** React 19 / react-router v8 / Vitest + RTL / Playwright(真实后端栈 + mock 契约栈双轨)/ Python FastAPI + SQLAlchemy 2.x async / pytest。

## Global Constraints

- 颜色一律语义 token;排版走 type-scale 工具类;触控目标 ≥44px;hover-only 能力在触控常驻或进 Menu(§8.2)。
- 状态可访问性:selected 同时具 ARIA 状态;`<time>` 带 `dateTime`(§9.1/§10.2)。
- 提交规范:author/committer = `cnwenf <cnwenf@outlook.com>`;绝无 `Co-Authored-By`;conventional commits;rebase 最新 main。
- 真实后端 spec 一律命名 `real-*.spec.ts`(被 `playwright.config.ts` 的 `testIgnore: ['real-*.spec.ts', ...]` 兜底排除出 mock 契约套件,杜绝真栈 spec 误跑 mock 配置致 CI 红)。
- 覆盖率:前端 `verify-perfile-coverage.mjs` 对 members/inbox/chat/agents/design 逐文件 ≥90%;后端 pytest-cov ≥90%。
- 取证:四组合(桌面/手机 × 亮/暗)真实操作存证于 `frontend/e2e/evidence/mes111-b3/`,`check-evidence-unique.mjs` md5 全互异;README 口径与实测张数一致。
- 绝不暴露外部出处。

## 文件结构(本批触及)

```
frontend/src/design/patterns/ConversationLayout.{tsx,css}      # 双栏/单栏路由化模板
frontend/src/design/patterns/RunStateBadge.{tsx,css}            # §9.8 五态徽标
frontend/src/design/index.ts                                    # 桶导出 patterns
frontend/src/features/agents/{presence,runState}.ts             # presence 订阅 + 五态归一
frontend/src/features/members/MembersPage.tsx + members.css     # Avatar/Badge/Menu/A-05 卡片/行状态矩阵
frontend/src/features/inbox/{InboxPage,InboxPreviewPane,InboxBell,quietHours}.tsx/ts + inbox.css
frontend/src/features/chat/{ChatPage,ConversationPanel,ContextBar,MessageBubble,useChatStream}.tsx/ts + chat.css
frontend/src/App.tsx                                            # /inbox/:notificationId + /chat/:sessionId
frontend/src/i18n/catalogs/{en,zh-CN}.json                      # runState.* + 新增键(djb2 重算)
frontend/e2e/real-mes111-b3-evidence.spec.ts + playwright.mes111b3.config.ts  # 真栈四组合走查
frontend/e2e/mes111-reachability.spec.ts                        # 消重 phone-home-dark
backend/src/mesh/chat/service.py                                # 读路径 blob JOIN + 读门 L2
backend/tests/unit/test_chat_service.py                         # 往返 + 读门回归
docs/superpowers/plans/2026-07-30-mes111-batch3-members-inbox-chat.md  # 本文件
```

## Tasks(TDD:先红后绿)

- [x] **T1 patterns 层**:`ConversationLayout`/`RunStateBadge` + 单测(状态映射、data-state、reduced-motion、双栏/单栏 activePane)。红:缺组件;绿:设计桶导出 + 单测过。
- [x] **T2 名册**:`MembersPage` 头像→底座 `Avatar`、AI 徽标→`Badge accent`、主次分行、行操作进 `Menu`、A-05 手机卡片无横向溢出、agent 行 presence→五态;`members.css` 行状态矩阵 + 平板/粗指针 ≥44px。红:旧 testid e2e 失效;绿:`real-members.spec.ts` 迁 menuitem + 单测过。
- [x] **T3 收件箱**:`ConversationLayout` 双栏 + `InboxPreviewPane` + 手机单栏路由化;优先级/来源/未读一致;选中即标已读(真实落库)+ 归档 + 深链;`unknownId` 改「未找到/已归档」非误报源删除;`quietHours` 横幅 + 分钟 tick;铃铛 `IconButton` + 外部点击/Esc + `role=region` + `aria-controls`;错误态优先于骨架;乐观失败回滚 + toast;`aria-current` + `dateTime`。
- [x] **T4 聊天**:`/chat/:sessionId` 路由化选中 + 手机单栏 + 返回;`ConversationPanel key={selected.id}` 防跨会话流泄漏;`ContextBar` 收起补 `[hidden]{display:none}` 兜底;输入区粘底 + safe-area;流式/失败五态徽标;`useChatStream.abort` 本地置 interrupted 防幽灵;`sessionNotFound` → `replace('/chat')` 解手机死胡同;长消息 CJK 断行;魔法数换 token。
- [x] **T5 后端 CRITICAL 读路径**:`_message_attachments` JOIN `attachment_blobs` 取 `mime_type/scan_status`、`byte_size←file_size`;`distill_preview` 裸 SQL 换 ORM + 可见性门。红:带附件会话 `GET messages` 500(真栈探针复现);绿:往返回归单测 + 真栈 200。
- [x] **T6 安全 HIGH-1 读门**:`_can_read_host` chat_message 分支 JOIN `chat_sessions` 校 `owner_id==viewer`,非属主 404。红:同空间非属主可读他人私聊附件;绿:非属主读被拒回归单测 + 真栈探针(stranger 404/owner 200)。
- [x] **T7 前端附件上传 400**:`ChatComposer` 经 `useAttachmentUploader({ workspaceId })` 透传归属工作区。红:upload-requests 400;绿:透传回归单测 + 真栈直传成功。
- [x] **T8 CI 配置**:真栈走查 spec 改名 `real-mes111-b3-evidence.spec.ts` 归入 `real-*` 兜底排除,修复 mock 契约套件误跑真栈 spec 致 frontend e2e 红;消重 `phone-home-dark.png` 跨批 md5 重复使 evidence-unique 转绿。红:mock 套件 1 failed(真栈 spec);绿:73 passed + 214 存证全互异。
- [x] **T9 存证/口径**:手机重生成步去静默跳过(`waitFor`+`scrollIntoView` 硬等待),四组合补齐 58 张;README/CHANGELOG 张数与实测对齐。

## Verification(真浏览器 + 真后端)

- 前端:`vitest run --coverage` 308 文件/3262 例全绿 + per-file 门禁过;`tsc -b --noEmit` 0;`npm run lint` 0 error;`check-contrast.mjs` 76 对双主题;`npm run build` 绿;`playwright -c playwright.config.ts`(mock)73 passed;`playwright -c playwright.mes111b3.config.ts`(真栈)四组合 passed;`check-evidence-unique.mjs` 214 全互异。
- 后端:`pytest tests/unit/test_chat_service.py tests/unit/test_attachment_service.py` 全绿(含 T5 往返 + T6 读门回归)。
- 真栈探针:聊天附件 `GET messages` owner 200 / 含附件渲染;读门 stranger 404 / owner 200。

## 验收差距闭合说明(对应复验清单)

- 清单项 1/2(相对导入/断言):安全修复提交已改对(绝对导入 + `link.linked_id==user_msg_id`)。
- CRITICAL #3/#4、HIGH #3–#8:T5/T6/T2/T4/T3 逐条实现并以真栈/真浏览器取证(非 jsdom 假象:`[hidden]` 级联、跨会话流、手机返回均真浏览器复验)。
- 证据 #9、CI #10:T8/T9 修复(真栈 spec 归 `real-*`、消重、手机重生成补齐、口径对齐)。
- 流程 #11:本计划文档 + 全程先红后绿 + 真浏览器/真后端自验。
