# 主题与暗色模式(theme)功能 Spec

> **所属层**:平台能力层(设计系统级;README §6.12 设计系统与体验基线「主题与暗色模式」段的详 Spec)。
> **依赖的其他 Spec**:
> - `auth.md`(§2.2 `users`):`users.settings.theme`(账号级主题偏好,`∈ {light,dark,system}`,默认 `system`)经 `PATCH /api/v1/users/me`(auth.md §3.1)写入——端点为 auth.md owns,本 Spec 声明其承载的键与校验。
> - `workspace.md`(§2.2 `workspaces.settings`):`workspaces.settings.default_theme`(工作区默认主题,默认 `system`)经 `PATCH /api/v1/workspaces/{id}` 写入——**已在 workspace.md §2.2 `settings` 已知键表登记**,本 Spec 为该键语义的 owner。
> - `i18n.md`(§2 偏好键约定 / §3.4 错误码本地化):主题偏好与 locale 同属「展示层偏好」,协商链同构(§2.2);设置入口文案经消息目录。
> - `analytics.md`(§4.5 图表配色约定):数据可视化配色经语义 token、双主题校准、颜色不作唯一信号——本 Spec 提供 token 面,图表组件实现属 analytics 模块。
> **被依赖方**:一切前端组件以本 Spec 的语义 token 为**唯一取色路径**(§6.12 禁止组件硬编码色值);命令面板/帮助层/各模块 UI 的颜色均经语义 token。
>
> **全局一致性锚点(canonical anchor)**:本 Spec 是 [README.md](../README.md) §6.12「主题与暗色模式」段的**详 Spec**。§6.12 已就**主题模式 `light`/`dark`/`system`**、**偏好真源(`users.settings.theme` 账号级 + `workspaces.settings.default_theme` 工作区默认,未登录/未设置时生效)**、**一切颜色经语义 token 禁止硬编码**、**暗色 token 集整体替换**、**两套主题同满足 WCAG 2.1 AA(4.5:1)**、**图表/状态色双主题校准**、**切换即时无刷新**、**尊重 `prefers-reduced-motion`/`prefers-contrast`** 作出唯一权威契约;本 Spec 仅**展开其实现细节**(token 清单与命名、协商链、防闪烁、验收门禁、存量债务收口),**不复述、不改写契约原文**——凡与 §6.12 冲突,一律以 README 为准。相关契约锚点:展示偏好协商范式(§6.18)、API 包络/错误(§6.14)、用户可控 URL(§6.16)。

---

## 1. 功能描述

### 1.1 模块定位

本模块为 Mesh 提供**设计系统级主题能力**,与 i18n(§6.18)共同构成前端呈现契约:

- **三态主题**:`light` / `dark` / `system`(跟随系统 `prefers-color-scheme`),`system` 缺省;
- **语义 token 单一取色路径**:一切颜色经语义 token 引用,暗色模式以**暗色 token 集整体替换**语义 token 取值实现,不逐组件改写(§6.12);
- **偏好协商链**:用户偏好 → 工作区默认 → 系统(镜像 §6.18 locale 协商链);
- **对比度自证 + 门禁**:两套主题各满足 WCAG 2.1 AA(4.5:1),设计期自证升级为 CI 门禁(§6.12)。

存储层语义不变:主题为展示偏好,**不落业务字段**(§6.18 同域原则)。

### 1.2 功能点 + 用户场景表

| # | 功能点 | 说明 | 典型用户场景 |
|---|--------|------|--------------|
| T1 | 主题模式三态 | `light`/`dark`/`system`;`system` 跟随 `prefers-color-scheme` | 夜间自动暗色 |
| T2 | 账号级偏好 | `users.settings.theme`(默认 `system`,显式 `null` = 清除落协商链下一级);经 `PATCH /users/me` 键级浅合并 | 跨设备一致 |
| T3 | 工作区默认 | `workspaces.settings.default_theme`(默认 `system`,admin 写);未登录/账号未设时生效 | 团队统一暗色 |
| T4 | 偏好协商链 | 用户偏好(为 `null` 跳过)→ 工作区默认 → 系统(→`prefers-color-scheme`);未登录场景从第 2 级起 | 邀请接受页随工作区 |
| T5 | 暗色整组替换 | `:root[data-theme='dark']` 属性选择器整组覆盖语义 token;暗色颜色 token 与亮色一一对应(测试断言无遗漏/多余) | 一处切换全局生效 |
| T6 | 切换即时无刷新 | 仅改 `<html data-theme>`,CSS 变量级联即时生效;不重载不重建路由 | 设置页即时预览 |
| T7 | 防闪烁(FOUC) | `<head>` 同步内联脚本首帧前读镜像键 `mesh.theme`(**键值显式白名单:非 `light|dark` 一律丢弃,回落 system 解析**)→ 解析 system → 设 `data-theme`;存储访问 try/catch | 刷新无白闪 |
| T8 | 系统偏好实时跟随 | `system` 模式监听 `prefers-color-scheme` `change` 实时切换;显式 light/dark 忽略;卸载注销 | 操作系统切换即跟随 |
| T9 | `color-scheme` 联动 | `:root` 声明 `color-scheme: light`,暗色声明 `dark`——原生滚动条/下拉/自动填充随主题 | 原生控件不刺眼 |
| T10 | `prefers-reduced-motion` | 减少动效偏好下关过渡/动画;主题切换不做首帧渐变 | 无障碍 |
| T11 | `prefers-contrast: more` | 高对比偏好下边界/文本增强(媒体查询增强,非独立第三套主题) | 无障碍 |
| T12 | 对比度 AA 自证 | 文本/底色配对在亮/暗两套各 ≥4.5:1(正文),图形元件 ≥3:1;单一事实源 token 值代入公式自证 | 设计期保障 |
| T13 | CI 门禁(防回归) | 对比度校验独立 CI 关卡 + 硬编码色值扫描(白名单仅 token 源 + 显式登记的数据色例外) | 阻止回归 |
| T14 | 组件硬编码禁令 | 组件层一律 `var(--token)`;覆盖 `color/background-color/border-color/outline/fill/stroke/box-shadow` 颜色位 | 暗色无死角 |
| T15 | 数据色例外立约 | 标签色/头像底色属**数据**非主题,为合法例外:预设色板双主题对表面色满足对比,例外在 CI 白名单**逐文件登记并注释原因** | 标签彩色可用 |
| T16 | 图表双色板 | 数据可视化经语义 token(status/danger/warn/success/info + 中性),亮/暗各校准;颜色不作唯一信号(线型/图标/文字叠加,analytics.md §4.5) | 暗色图表可读 |

### 1.3 边界与非目标(明确不做什么)

- **不做用户自定义品牌色 / 主题编辑器**:本期仅 `light/dark/system` 三态,token 是设计契约,品牌一致性优先于个性化。
- **不做主题市场 / 分享**:无主题打包/导入/社区分享。
- **不单列独立 `high-contrast` 主题集**:以 `@media (prefers-contrast: more)` 在亮/暗各自增强(避免「模式 × 对比」矩阵翻倍);待真实无障碍审计压力到位再升级。
- **不做服务端 token 下发端点**(`GET /api/v1/theme/tokens`):token 是随构建分发的设计资产,纯前端静态;服务端化引入双真源漂移(正是 i18n `workspaces.default_language` 旧列被废的同类教训)、首帧阻塞与无谓攻击面,YAGNI。
- **不**新增业务表、**不**改存储层时间语义(UTC 不变)、**不**自定义角色/权限模型(沿用 auth.md RBAC)、**不**约束前端框架(README §3.2)。

---

## 2. 数据模型与配置

> **全局契约引用**:API 包络/错误以 [README.md](../README.md) §6.14 为权威;主题契约以 §6.12 为权威;展示偏好协商范式以 §6.18 为权威。
>
> **不新增表**:本模块**无新表**,仅约定 `users.settings` / `workspaces.settings` 两处既有 JSONB 键(PostgreSQL 16),与 i18n.md §2 完全同构。

### 2.1 偏好键约定

| 键 | 载体 | 类型 | 默认 | owns / 写端点 | 校验 |
|----|------|------|------|---------------|------|
| 账号主题 | `users.settings.theme` | string \| null | `"system"`(`null` = 清除,落协商链下一级) | auth.md §2.2 / `PATCH /api/v1/users/me`(本人) | `∈ {light,dark,system}`,否则 422(§3.3) |
| 工作区默认主题 | `workspaces.settings.default_theme` | string | `"system"` | workspace.md §2.2 / `PATCH /api/v1/workspaces/{id}`(admin) | `∈ {light,dark,system}`(经 `validate_theme`,**已登记**) |

> 两处写入均为**按键浅合并**(PATCH 语义,与 i18n.md §2.1 / workspace.md §2.2 一致):仅覆盖请求中出现的键,未出现的键保持原值。

### 2.2 偏好协商链(镜像 §6.18 locale 链)

```
解析实际应用主题(从高到低):
  1. 用户偏好    users.settings.theme           (为 null → 跳过本级)
  2. 工作区默认  workspaces.settings.default_theme
  3. 系统回退    system → prefers-color-scheme   (dark ? dark : light)
最终落到 <html data-theme="light|dark">;system 态持续跟随系统变化(T8)
```

- 与 locale 链的差异:locale 链尾回退固定 `en`;theme 链尾 `system` 本身即「跟随系统」,是**动态媒体查询结果**而非常量;
- **未登录 / 邀请接受页等无 `users.settings` 场景,直接从第 2 级(工作区默认)起解析**——进入工作区上下文时读取 detail(`GET /api/v1/workspaces/{id}` 返回 `settings.default_theme`,同 `fetchWorkspaceDefaultLocale` 模板);无工作区上下文时落 `system`。

### 2.3 单一事实源(前端)

- `tokenValues.ts` 的 `LIGHT_TOKENS`/`DARK_TOKENS` 为 token 唯一事实源,`tokens.css`(`:root`)/`tokens-dark.css`(`:root[data-theme='dark']`)须与其**逐项一致**(测试解析 CSS 断言镜像);
- 新增/修改 token 须同时改三处(TS + 两份 CSS)并由测试兜底防漂移;
- 防闪烁镜像键 `mesh.theme`(localStorage)由偏好 store 写入,仅供 `index.html` 内联脚本首帧读取,不是偏好真源(真源在服务端 `users.settings.theme`);内联脚本对该键值**显式白名单**——**非 `light|dark` 一律丢弃并回落 system 解析**(localStorage 可被同源脚本写入,该脚本不得成为攻击者可控的属性落点)。

### 2.4 语义 token 清单(亮/暗各一份,一一对应)

| 分组 | token | 用途 | 暗色策略 |
|------|-------|------|----------|
| 表面/文本 | `--color-bg` · `--color-surface` · `--color-surface-raised` · `--color-text` · `--color-text-muted` · `--color-border` | 页面底色/常规表面/浮起表面/正文/弱化文本/边框 | 整组替换;raised 按层级提亮表达层级 |
| 品牌 | `--color-primary` · `--color-primary-contrast` | 主色(文本/底两用)+ 配对文本 | 暗色向更亮偏移保对比 |
| 状态 | `--color-danger` · `--color-warn` · `--color-success` · `--color-info` 各 + `-contrast` | 四态语义色 + 配对文本 | 降饱和/更亮变体;配对 ≥4.5:1 |
| 交互 | `--color-focus-ring` · `--color-scrim` · `--shadow-raised` | 焦点环/弹层遮罩/浮起阴影 | 焦点色更亮;遮罩/阴影加深 |
| 尺度(两主题共用) | `--space-1…6`(4/8/12/16/24/32)· `--radius-sm/md/lg` · `--font-size-sm/md/lg` · `--font-family` · `--duration-fast/slow` | 间距/圆角/字号/字体/动效时长 | 非颜色,不替换 |

- 命名规范:`--color-<语义>[-<状态>]`(kebab-case),**表意不表值**(禁 `--color-red` 式);状态色成对(`--color-<tone>` + `--color-<tone>-contrast`);
- 演进建议(YAGNI 渐进):出现真实分化需求再抽「基础色板层」与「组件层」,当前「语义层 + 内联基础值」已满足契约。

### 2.5 数据色例外契约(T15)

标签预设色板(`ColorPicker`)与确定性头像底色是「禁硬编码」规则的**合法例外**,立约如下:

- 预设色板在亮/暗两套主题下对各自表面色满足对比(文本/前景叠加对比由组件保证);自定义 hex `^#[0-9a-fA-F]{6}$` 在**服务端写入边界**(标签持久化,label-property.md 落地)与**渲染时同校验**——仅客户端校验无效(写入路径可绕过前端直达 API);
- 数据色**不进入全局 token**;例外文件须在 §5.4 CI 扫描白名单**逐文件登记并注释原因**,新增例外需评审,不默认豁免。

---

## 3. 接口设计

> REST 基础路径 `/api/v1`;鉴权 `Authorization: Bearer <token>`(auth.md)。成功包络、错误信封以 README §6.14 为权威。**本模块不新增业务端点**,仅复用既有偏好写端点。

### 3.1 端点清单(复用为主)

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| PATCH | `/api/v1/users/me` | 写 `settings.theme`(键级浅合并;显式 `null` 清除)。请求体 `{ "settings": { "theme": "dark" } }` | 本人 |
| PATCH | `/api/v1/workspaces/{id}` | 写 `settings.default_theme`(admin;workspace.md 既有端点,键已登记) | admin |
| GET | `/api/v1/me` | 返回合并后 `settings`(含 theme),登录后回填偏好 | 本人 |
| GET | `/api/v1/workspaces/{id}` | detail 返回 `settings.default_theme`,供协商链第 2 级读取(列表短响应不含 settings,须读 detail) | 成员 |

> 变更经 auth.md 既有审计(`audit_logs`);工作区默认主题变更触发 `workspace.updated` 实时事件(workspace.md §3.5,已登记 §6.7)。**不新增实时事件**。

### 3.2 请求/响应 JSON 示例

**写账号主题** `PATCH /api/v1/users/me`(auth.md 既有端点)
```json
// Request(按键浅合并)
{ "settings": { "theme": "dark" } }
// 200 Response(README §6.14 单对象包络)
{ "data": { "id": "u-1", "settings": { "locale": "zh-CN", "theme": "dark" },
            "timezone": "Asia/Shanghai", "updated_at": "2026-07-28T08:00:00Z" } }
```

**写工作区默认主题** `PATCH /api/v1/workspaces/{id}`(workspace.md 既有端点)
```json
// Request
{ "settings": { "default_theme": "dark" } }
// 200 Response:返回工作区对象;触发 workspace.updated(workspace.md §3.5)
```

### 3.3 错误码表(模块专属)

| HTTP | code | 场景 |
|------|------|------|
| 422 | `invalid_theme_mode` | `settings.theme` / `settings.default_theme` 不在 `{light,dark,system}`(**具名升格**:与 i18n 的 `unsupported_locale` / `invalid_timezone` 对齐;`details: {theme, supported}` 供前端按 §6.18 消息目录渲染本地文案。升格需 auth.md §3.5 / workspace.md 错误码表同步登记) |
| 401 | `unauthorized` | 凭证缺失/无效(§6.14 canonical) |
| 403 | `forbidden` | 非 admin 写工作区 `default_theme`(§6.14 canonical) |
| 429 | `rate_limited` | 偏好写触发限流(auth.md) |

> 现状非法 theme 值为通用 `422 validation_error`;**本 Spec 决定升格具名码**以与同域偏好键一致,auth.md/workspace.md 错误码表在开发 Issue 中同步(评审如有异议以评审批注修订本节)。公共 HTTP 语义不重复定义(§6.14)。

---

## 4. UI/UX 设计

### 4.1 切换入口(两层)

- **个人偏好**:设置 → 外观 → 主题下拉(light/dark/system),即时生效(既有实现延续);命令面板提供快捷命令(`theme.light`/`theme.dark`/`theme.system`/`theme.toggle`,既有注册延续);`system` 态标注当前系统解析值(如「跟随系统(暗)」)让用户预知结果;
- **工作区默认**:工作区设置 → 默认主题(admin 可见),写入 `settings.default_theme`,文案说明「成员未单独设置时生效」(**当前无此入口,属 T4 的 UI 面,随协商链一并落地**);
- 两级关系可视化:用户级未设置时显示「跟随工作区默认(dark)」占位,显式选择后提供「恢复跟随默认」(与 i18n.md §4.1 同款交互)。

### 4.2 切换即时生效(无刷新、不重放动画)

- 选项变更即落 `data-theme`,所见即所得,无「保存」按钮;
- 主题过渡若启用须 gate 在首帧后(避免「白→暗慢 fade」替代闪烁的同源问题),且受 `prefers-reduced-motion` 约束(减少动效则无过渡)。

### 4.3 暗色模式细部

- **阴影**:暗色加深;raised 表面按层级提亮表达层级,而非仅靠阴影;
- **焦点环**:暗色用更亮焦点色,`:focus-visible` 2px + offset;
- **遮罩**:`scrim` 暗色加深;
- **原生控件**:经 `color-scheme` 随主题;
- **图片/头像**:用户头像为数据不主题化,暗色下加 1px 语义 border 或轻微降亮度;装饰 SVG 用 `currentColor`/`<picture media>`(可选增强)。

### 4.4 数据可视化双色板

- 图表色一律引用语义 token(status/danger/warn/success/info + 中性),亮/暗各校准(analytics.md §4.5);
- 多系列类别色板若需扩展,在语义层增设 `--color-chart-N` 并**先登记进对比度配对表再合入**;
- 颜色不作唯一信号:线型虚实 + 图标 + 文字叠加(analytics.md §4.5 / §6.12)。

### 4.5 首次访问 / 跨设备 / 降级

- **未登录**:无账号偏好 → 工作区默认(邀请接受页/公开页);无工作区上下文 → `system`;防闪烁脚本仅读本地镜像键,无镜像时落 `system`,进入工作区上下文后补协商;
- **跨设备一致性**:账号偏好经 `PATCH /users/me` 持久化服务端,登录即回填(`GET /me`);本地 persist 仅作降级镜像与防闪烁首帧用,**服务端为跨设备真源**;
- **降级**:服务端同步失败不回滚本地(乐观 + 降级镜像),错误经 `lastSyncError` 按 code 渲染本地化提示(i18n.md §3.4);离线本地偏好仍可用。

---

## 5. 验收标准

### 5.1 功能性

- [ ] **协商链优先级正确**:用户偏好(`null` 跳过)→ 工作区默认 → 系统;账号未设 + 工作区默认 `dark` → 应用暗色;未登录进入工作区上下文 → 按工作区默认解析(**不再硬编码回退 `system`**)。
- [ ] **三态切换即时无刷新**:light/dark/system 切换仅改 `<html data-theme>`,无路由重建;`system` 下操作系统切换深浅色**实时跟随**(`matchMedia change`),显式 light/dark 时忽略系统变化。
- [ ] **防闪烁**:冷启动(清本地镜像)刷新无白闪;内联脚本 try/catch 存储访问;静态 HTML 不预置 `data-theme`。
- [ ] **暗色整组替换**:暗色颜色 token 与亮色**一一对应**(测试断言无遗漏/多余);抽查核心页面(看板/issue 详情/成员/聊天/运行详情/收件箱)暗色下无硬编码死角。
- [ ] **`color-scheme` 联动**:暗色下原生滚动条/下拉/自动填充呈暗色。
- [ ] **偏好写入**:用户偏好经 `PATCH /users/me`、工作区默认经 `PATCH /workspaces/{id}`,按键浅合并;非法值 → `422 invalid_theme_mode`(auth.md/workspace.md 错误码表已同步登记)。
- [ ] **工作区默认入口**:工作区设置含默认主题三态选择器(admin 可见),写入生效并触发 `workspace.updated`。
- [ ] **reduced-motion/contrast**:减少动效偏好下无过渡动画;高对比偏好下边界增强。
- [ ] **文案外部化**:切换入口/标注无硬编码可见文案(i18n.md)。

### 5.2 性能

- [ ] 主题切换界面重渲染 < 1s,无整页刷新;
- [ ] 防闪烁脚本执行 < 5ms(同步内联,不阻塞首屏渲染关键路径);
- [ ] token 为构建期静态 CSS,运行时无网络往返、无服务端状态。

### 5.3 安全与一致性

- [ ] **无任意值注入面(二值收敛)**:`data-theme` 仅取 `light|dark`(脚本与解析函数收敛二值);防闪烁脚本对镜像键 `mesh.theme` 显式白名单——构造非法值(如 `javascript:...`/任意字符串)写入 localStorage 后刷新,`data-theme` 回落 system 解析而非原样落地;偏好枚举受控;token 值全部来自构建期静态 CSS,非运行时拼接用户输入。
- [ ] **CSP 协调**:防闪烁内联脚本经**每请求 nonce** 或 `sha256` 哈希白名单放行,**绝不**放开 `unsafe-inline`;脚本不含用户输入。
- [ ] **数据色例外受控**:自定义 hex 经 `^#[0-9a-fA-F]{6}$` 在**服务端写入边界与渲染时双重校验**(构造非法 hex 直写 API 被拒);用户可控 `avatar_url`/`logo_url` 沿用 §6.16 https-only,主题不引入新 URL 面。
- [ ] **偏好写入鉴权**:`PATCH /users/me` 仅本人;`default_theme` 写需 admin;变更写审计。
- [ ] **无暴露外部出处**:token 命名/注释/文案不含任何竞品名称、外部调色板出处信息。

### 5.4 验收门禁(CI,防回归)

- [ ] **对比度 CI 关卡**:独立脚本 import 单一事实源(`LIGHT_TOKENS`/`DARK_TOKENS` + 配对表 + 对比度公式),正文/状态色文本 ≥4.5:1、图形元件 ≥3:1,亮/暗两套逐对校验,任一不达标 PR 失败;新增颜色 token 须先登记配对表。
- [ ] **硬编码色值扫描**:组件/特性目录 `*.css`/`*.tsx` 颜色属性禁 `#hex`/`rgb()/rgba()`/命名色(白名单放行 `transparent/currentColor/inherit/initial/revert/unset`),必须 `var(--*)`;白名单仅 token 源文件 + **显式登记的数据色例外**(逐文件注释原因)。
- [ ] **存量债务收口**:门禁上线同时完成既有组件硬编码色值迁移(`skills.css` ≈52 处、`autopilots.css` ≈18、`dataJobs.css` ≈7、`projects.css` ≈1,以扫描实际命中为准)——缺失语义 token 先在 token 源补,再替换;迁移后扫描零命中(白名单外)。
- [ ] **暗色视觉回归**:核心页面(§6.12 异常态矩阵页面)在 light/dark 两态 × 关键断点(≥1024 桌面 / 768 平板)截屏存证,接入既有视觉回归 CI 范式。
- [ ] **单元回归常绿**:ThemeProvider(解析/实时跟随/即时切换/卸载注销)、tokens 镜像与一一对应、对比度自证三套件随 CI 常跑。
