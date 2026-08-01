# MES-127 MES-111 批次④ —— 设置 + 搜索/命令面板 + Analytics 实施计划

日期:2026-07-30 · 负责人:Mesh 程序员 · 基线:main @ f3938e5a(PR #94 设计系统底座已合入)

## 目标(严格对齐 issue 范围)

1. **设置**:SettingsLayout 页面模式(沉淀到 `src/design/patterns`,API 稳定);账号 `/settings` 与工作区 `/w/:slug/settings` 二级导航(桌面左侧 / 手机顶部分组列表);内容按 Appearance/Notifications/Security/Tokens/Audit/Data/Danger 分页;dirty state + 保存反馈;权限不可见;危险操作独立分区 + 确认。**G11**:工作区默认主题入口核验与增强(后端已支持 `settings.default_theme`,前端入口在 BasicInfo 内已有雏形 → 提为独立可见、带「成员未单独设置时生效」说明的 admin 入口)。
2. **统一搜索/命令面板**(`src/shortcuts/`,design-quality §9.6 全条):顶栏搜索与 Ctrl/Cmd+K 同一面板同一结果视图;**G3/G4**:六类对象检索(工作项/项目/成员·agent/视图/聊天/命令)+ favorites/recents + identifier 直达(跳过防抖)+ no-results 语法提示与建 issue 入口;120ms 防抖、AbortController 取消、旧响应不覆盖新查询;↑↓/Enter/Esc;aria-live 宣布结果数;`/` 聚焦搜索。需新建后端 `GET /api/v1/workspaces/{ws}/search`(search-command-palette.md §3 契约)。
3. **Analytics**(`/insights` + 内嵌面板):Workbench KPI 条 + 图表网格 + 口径说明;数字 tabular-nums;无数据/数据不足/权限过滤/时区/亮暗/颜色非唯一信号;窄屏 KPI 两列或单列,图表重排不压缩文字。
4. **G10**:`/approvals` + `/w/:slug/approvals` 统一审批页(聚合 tool_call/autopilot_action/squad_plan 三类;后端 `GET /workspaces/{wid}/approvals?role=mine` 已存在)。
5. **G19**:`shell/hooks/useDocumentTitle` 公共 hook(导出),本批页面接入。

## 设计契约

- 颜色一律语义 token(ESLint/Stylelint `mesh/no-hardcoded-colors` 门禁);图表色经 `ChartColorToken`(不复用状态色语义冲突,§5.2 允许经语义 token 登记);排版用 `.mesh-text-*` 工具类;组件从 `src/design` 桶导入。
- 命令面板首开交互 ≤100ms:本地命令同步过滤先渲染,远程 skeleton 不阻塞。
- i18n:新文案 zh-CN + en 两套目录同步(parity 测试门禁),键前缀 `search.*`/`approvals.*`/`settingsLayout.*`/`docTitle.*` 等。

## 后端:search 模块(新建 backend/src/mesh/search/)

- 迁移 `0034_search_indexes.py`:pg_trgm + unaccent 扩展;`public.mesh_search_norm(TEXT)` IMMUTABLE plpgsql(NFKD + unaccent 显式词典 + lower);`members.search_name` 投影列 + 全量回填;spec §2.2 的 11 条索引 + 2 条支撑索引;**搜索名同步以数据库触发器实现**(members/users/agents 写路径同事务重算,等效 spec 的单一函数同步契约,自包含不侵入其他模块服务层;偏差在 MES-111 评论报备)。
- `routes.py`:`GET /api/v1/workspaces/{wid}/search?q=&types=&limit=&cursor=`,经 `require_workspace` + 限流;空 q → `{data:[],next_cursor:null}`;types 白名单 issue/member/agent/project/view/chat_session(非法 400 validation_error);limit 默认 20 上限 50;cursor = base64(json{fp(sha256(q|types|ws)) + keyset 元组(score_bucket,title_len,title_lex,type,id) + HMAC}),签名/绑定不符 400。
- `service.py`:三条路径(1–2 字符前缀 LIKE norm||'%';完整 identifier `upper(trim)` 等值快路径顶置;≥3 字符 trigram `%`);可见性谓词查询内完成(issue 项目可见性、私有 agent 仅 owner/admin、view 私有仅 owner 且项目 AND、chat 仅参与者);Python 侧分层打分 + 全序排序 + keyset 翻页;highlight codepoint 区间在原文计算;badge 用消息目录 label_key + params;url 规范深链(经 workspace slug)。
- 测试:unit(归一/打分/高亮映射/cursor 签名) + e2e(六类命中、私有项目/私有 agent/他人会话负向、identifier 大小写顶置、cursor 跨参复用 400)。

## 前端:命令面板重构(src/shortcuts/)

- `api/search.ts`(新):`searchWorkspace(client, wsId, {q, types, limit, cursor, signal})`。
- `useEntitySearch.ts`(新):120ms 防抖;AbortController 取消过期请求;响应 token 比对防旧覆新;identifier 正则 `^[A-Za-z][A-Za-z0-9]*-\d+$` 命中跳防抖;错误保留 query 可重试。
- `recents.ts`(新):`mesh.recents:{host}:{user}:{workspace}` LRU 20;对象/命令记录;空态 favorites(`GET /api/v1/favorites` 既有)→ recents → 常用命令唯一数据流;同 target 去重。
- `CommandPalette.tsx` 重构:分组组头(i18n)、高亮(字重 + 下划线 mark,非颜色唯一)、badge、底部提示条、aria-live=polite 结果数、`aria-activedescendant` 稳定 id 选中(异步补入不移位)、mod+Enter 新标签、no-results「新建 issue "q"」(有 issue:write 权限才渲染,预填 `/issues?create=1&title=`)、离线仅本地命令。
- 命令全集补齐(S3 九组,角色门控:设置子页/危险操作仅 admin/owner 注册):导航补 squads/skills/runtimes/integrations,设置子页命令,待审批,复制当前深链,收藏/取消收藏,标记全部已读(随当前 filter),帮助层。
- `TopBar.tsx`:搜索框真实化——受控 value/onChange,输入即展开同一结果视图(弹出层复用 palette 结果组件),Enter 打开面板,`/` 聚焦保持。

## 前端:SettingsLayout(design/patterns/ 新层)

- `SettingsLayout.tsx`:props `{ title, description?, groups: SettingsNavGroup[], children }`,`SettingsNavGroup { label?, items: SettingsNavItem[] }`,`SettingsNavItem { key, label, to, icon?, end?, hidden? }`;桌面左侧粘性导航(NavLink,当前项强调背景 + 3px 边缘指示),≤768px 折叠为顶部分组列表;内容列 `--content-form` 宽;导出自 `design/index.ts`。配 `patterns.css` + 单测。
- `SettingsSection`(同层):`{ title, description?, children, footer? }` 卡片分区,统一层级与留白。
- 账号设置 `/settings`:SettingsLayout 三节(appearance/notifications/security),路由 `/settings` → 首节,hash/子路径切换;沿用 useSettingsStore 即时生效 + lastSyncError 反馈。
- 工作区设置:子路由 `/w/:slug/settings`(general:基本信息 + **默认主题 G11 独立字段** + 角色矩阵)/ invitations / labels / custom-fields / data / tokens / audit / danger;labels/custom-fields/data 既有页面包进 SettingsLayout;danger 独立页;非 admin 不见管理项(权限不可见);general 表单 dirty guard(离开确认)+ 保存成功/失败反馈。

## 前端:Analytics 升级(features/analytics/)

- `KpiStrip` + `Kpi` 组件(页内):标签 + 大数字(tabular-nums)+ 单位/口径/时间范围,语义 tone;窄屏两列 → 单列(container query / 断点)。
- InsightsPage:Workbench 头(PageHeader 式)+ 可见性/时区口径说明(meta.display_timezone 回显 + 「按你的项目可见范围统计」)+ 图表网格重排;空数据 EmptyState(调整范围/新建 issue)、insufficient 标注、query_cost_exceeded「收窄后重试」、403 无权限页;burndown 虚实线型 + 图例文字(颜色非唯一信号)。
- 图表组件补 `role="img"` + aria-label;reduced-motion 下无入场动画。

## 前端:/approvals(G10,features/approvals/ 新)

- `api.ts`:`listApprovals(client, wsId, {role, status, limit})` → `GET /workspaces/{wid}/approvals`;`approveApproval`/`rejectApproval`(带 decision_comment)。
- `ApprovalsPage.tsx`:聚合列表(三类 subject_type 分组徽标 + 图标,非颜色唯一);每条显示动作摘要、capability+permission、影响范围、预估成本、过期时间、续跑提示(「将从审批点以新尝试恢复:已完成 N 步」)、主题深链(execution/run/squad task);pending 行 [批准][拒绝](拒绝填理由);expired 显示「已过期」+「重新发起」(深链主题);空态;agent principal 不呈现审批按钮(仅人类)。
- 路由:`/approvals`(经活跃工作区解析)+ `/w/:workspaceSlug/approvals`(规范深链)。

## G19

- `shell/hooks/useDocumentTitle.ts`:`useDocumentTitle(...parts: (string|undefined)[])` → `parts · Mesh`,卸载还原;`shell/hooks/index.ts` 导出。接入:设置各页、洞察、审批、(命令面板不改标题)。

## 验收门禁(硬门槛)

1. `npm run test:coverage` 全局 90% + per-file 门禁绿;新代码覆盖 ≥90%。
2. 真实 e2e:`playwright.mes111-b4.config.ts` + 独立栈(仿 e2e/mes107/):设置修改 + dirty/保存反馈;命令面板六类检索 + 键盘全流程;Analytics 空/有数据/权限过滤;/approvals 深链;桌面 + 手机。
3. 四组合存证 `frontend/e2e/evidence/mes111-b4/`(桌面 1440×900 / 手机 390×844 × 亮暗)+ README,`scripts/check-evidence-unique.mjs` md5 唯一。
4. `npm run lint`/`typecheck`/`build`/`check:contrast` + 视觉回归(新页面基线)全绿。
5. CHANGELOG 追加本批条目;README/Spec 同步;提交人 cnwenf 无 co-author;不暴露参考来源。

## 并行分工(无耦合,同工作区不同目录;git 与共享文件由主程统一收口)

- A:后端 search 模块 + 迁移 + 测试(backend/src/mesh/search/, backend/migrations, backend/tests)。
- B:命令面板前端 + api/search.ts + TopBar(src/shortcuts/, src/shell/TopBar.tsx 由主程收口)。
- C:SettingsLayout + 设置重构(src/design/patterns/, src/shell/pages/SettingsPage.tsx, src/workspace/, features/labels/data-jobs 包裹)。
- D:Analytics 升级 + approvals 页(features/analytics/, features/approvals/)。
- 主程:i18n 目录统一、App.tsx 路由、design 桶导出、TopBar 接线、集成验证、e2e 存证、文档与 PR。
