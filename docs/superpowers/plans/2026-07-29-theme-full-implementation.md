# MES-81 theme 全功能实现 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/specs/features/theme.md` 五章闭合主题模块缺口:偏好协商链(T4)、首帧防闪烁三级链路(T7/H2)、错误码升格、token 生成管线与 CI 门禁(对比度/AST 硬编码扫描/视觉回归/forced-colors)、存量 CSS 债务收口。

**Architecture:** 后端 FastAPI 新增只读 HTML 入口中间件(读 auth.md 会话模型的 `mesh_session` HttpOnly cookie,SHA-256 定位 sessions 行,解析协商链,注入二值 `__MESH_APPEARANCE__`,`Cache-Control: private, no-store` + per-request nonce CSP;无会话时返回字节不变的静态 shell)。前端以 `tokenValues.ts` 为唯一事实源构建期生成 CSS;协商链 `user(absent/null 跳过;system 本级终止)→ workspace default → system` 在 ThemeProvider 落地;首帧三级链路 `注入 → mesh.theme.active 分区 locator(路由身份校验)→ 中性 skeleton`。CI 增对比度独立关卡、gen:tokens 幂等断言、AST 级硬编码扫描(Stylelint + ESLint 自定义规则)、Playwright 视觉回归门禁。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 async / pytest(asyncio) · React 19 / TypeScript / Vite / zustand 5 / react-intl / Vitest / Playwright / ESLint 9 flat / Stylelint 16 · nginx / docker compose · GitHub Actions

## Global Constraints

- Spec 唯一权威:`docs/specs/features/theme.md`;README §6.12/§6.14/§6.18 为全局契约锚点。
- `data-theme` 与 `__MESH_APPEARANCE__.mode` 二值收敛 `light|dark`;一切来自存储/注入的值显式白名单,非法即丢弃进 skeleton/system 解析。
- 个性化入口 HTML 一律 `Cache-Control: private, no-store`;CSP `script-src` 经 per-request nonce(入口)或 sha256 哈希(静态 shell),绝不 `unsafe-inline`。
- 注入值仅含二值主题模式,不含工作区标识/名称等可枚举信息;工作区身份取自路由路径段,不经 query/header。
- UT 覆盖率 ≥90%(整体 + 新增,`backend-ci.yml --cov-fail-under=90` 与 `scripts/verify-perfile-coverage.mjs` 双达标)。
- token 命名 `--color-<语义>[-<状态>]` 表意不表值;数据色例外(标签色板/头像底色)逐文件 + 行级注释登记,禁整文件白名单。
- Git 提交 author/committer 恒 `cnwenf <cnwenf@outlook.com>`;提交信息无 Co-Authored-By;代码/注释/文档/分支不得暴露参考来源。
- 中间件凭据:测试容器 loopback 绑定 + 强随机口令(`mesh81-test.env`,不入库)。

## File Structure

### 后端(backend/)

| 文件 | 责任 |
|---|---|
| `src/mesh/validation.py` | `validate_theme` 错误码升格 `invalid_theme_mode`(修改) |
| `src/mesh/workspace/invitations.py` | `preview_invitation` 返回 `appearance.default_theme`(修改) |
| `src/mesh/web/__init__.py` · `src/mesh/web/appearance.py` | **新建**:协商链服务端解析(会话定位 → 用户偏好 → 路由 slug/invite 工作区默认 → 二值收敛) |
| `src/mesh/web/entry.py` | **新建**:HTML 入口路由——读 dist/index.html 模板,nonce CSP,注入 `__MESH_APPEARANCE__`,缓存头分流 |
| `src/mesh/api/app.py` | 注册入口路由(最后挂载,`settings.frontend_dist_dir` 缺失则不挂载)(修改) |
| `src/mesh/config.py` | `frontend_dist_dir` 设置项(env `MESH_FRONTEND_DIST_DIR`,默认 `/srv/mesh/frontend`)(修改) |
| `tests/unit/web/test_appearance.py` · `test_entry.py` | 新建:协商真源表 + 入口集成测试(httpx ASGI) |
| `tests/unit/test_validation.py` · `tests/unit/workspace/...` · `tests/unit/auth/...` | 错误码/预览断言更新(修改) |

### 部署(deploy)

| 文件 | 责任 |
|---|---|
| `frontend/nginx.conf` | SPA fallback 改 `try_files $uri $uri/ @app;` + `@app` 反代 api;静态 shell CSP `add_header`(sha256 占位) |
| `frontend/Dockerfile` | 构建期从 dist/index.html 计算 FOUC 脚本 sha256,替换 nginx.conf 占位;entrypoint 将 dist 拷入共享卷 |
| `docker-compose.yml` | `frontend_dist` 命名卷:frontend 写入(api 只读挂载 `/srv/mesh/frontend`);api 增 `MESH_FRONTEND_DIST_DIR` |

### 前端(frontend/)

| 文件 | 责任 |
|---|---|
| `src/design/tokenValues.ts` | 唯一事实源:补 selection/mark/skeleton/code 高亮 token;`AA_CONTRAST_PAIRS` 升级为 `{fg,bg,kind}` 含大文本/图形阈值(修改) |
| `scripts/gen-tokens.mjs` | **新建**:从 tokenValues.ts 生成 `tokens.css` / `tokens-dark.css` / `tokens-print.css`(生成头标记) |
| `scripts/check-contrast.mjs` | **新建**:独立对比度关卡脚本(大文本组单列),非零退出 |
| `src/design/tokens.css` · `tokens-dark.css` · `tokens-print.css` | 生成产物(禁止手改) |
| `src/design/themeNegotiation.ts` | **新建**:`resolveThemeChain({userTheme,workspaceDefault,systemPrefersDark})` 真源表;`expectedRouteId(href)` 路由身份分区表;`parseThemeLocator(raw)` 白名单解析 |
| `src/design/themeLocator.ts` | **新建**:`writeThemeLocator(mode)` / `clearThemeLocators()`(登出清理含旧 `mesh.theme` 与残留分区键) |
| `src/design/ThemeProvider.tsx` | 链式解析 + skeleton pending 信号 + matchMedia 跟随/卸载注销 + workspace.updated 重解析(重写) |
| `src/design/skeleton.css` · `ThemeSkeleton.tsx` | **新建**:中性 skeleton(骨架 token 取色,pending 期渲染) |
| `src/design/base.css` | selection/mark/autofill/print/reduced-transparency/prefers-contrast/forced-colors(修改) |
| `src/state/settingsStore.ts` | `theme: ThemeMode | null`(默认 null);persist v2 迁移;locator 回写;storage 跨 tab 同步;`setTheme(null)` 清除;`hydrateFromServer`(服务端覆盖 + 匿名合并裁决)(重写核心段) |
| `src/state/pendingSettingsQueue.ts` | **新建**:分区键 `mesh.settings.pending:{host}:{user_id}:{workspace_id}`,三元组校验重放,服务端优先冲突策略,online/visibility/写入触发 |
| `src/state/preferencesSync.ts` | 增 `invalid_theme_mode` 码归一;payload 支持 `theme: null`(修改) |
| `src/hooks/useWorkspaceLocale.ts` → 模板 | 新建 `useWorkspaceDefaultTheme`(同模板,detail `settings.default_theme`) |
| `src/workspace/pages/InviteAcceptPage.tsx` | preview `appearance.default_theme` 驱动第 2 级(修改) |
| `src/api/invitations.ts` | 类型补 `appearance`(修改) |
| `src/shell/pages/SettingsPage.tsx` | 四态选择(跟随工作区默认/light/dark/跟随系统)+ 解析值标注 + 恢复跟随默认写 null(修改) |
| `src/workspace/pages/WorkspaceSettingsPage.tsx` | admin 默认主题三态入口(修改) |
| `src/i18n/messages/zh-CN.ts` · `en.ts` | 新文案键(修改) |
| `src/shell/AppShell.tsx` | workspace.updated 订阅 → 无显式偏好成员重解析(修改) |
| `index.html` | 内联防闪烁脚本重写为三级链路(注入 → locator 校验 → pending 标记);meta theme-color 双声明 |
| `stylelint-plugins/no-hardcoded-colors.mjs` · `eslint-rules/no-hardcoded-colors.js` | **新建**:AST 级硬编码色值规则 |
| `theme-lint-exemptions.json` | **新建**:数据色例外逐文件登记(文件 + 行 + 原因) |
| `playwright.visual.config.ts` · `e2e/visual/*.spec.ts` · `e2e/fixtures/seed-visual.mjs` · `e2e/fixtures/fonts/` | **新建**:确定性视觉回归矩阵 |
| `playwright.theme-real.config.ts` · `e2e/real-theme-*.spec.ts` | **新建**:无闪错三场景 + locator 分区真实 e2e |

---

## Task 1: token 源升级与对比度配对表

**Files:**
- Modify: `frontend/src/design/tokenValues.ts`
- Modify: `frontend/src/design/contrast.ts`(支持 rgba/alpha 解析——scrim 与 shadow 含 rgba)
- Test: `frontend/src/design/__tests__/contrast.test.ts`

**Interfaces:**
- Produces: `LIGHT_TOKENS`/`DARK_TOKENS` 新增键 `--color-selection-bg/-text`、`--color-mark-bg/-text`、`--color-skeleton-base/-highlight`、`--color-code-bg/-text/-keyword/-string/-comment`;`ContrastPair = { fg: string; bg: string; kind?: 'text' | 'large-text' | 'graphic' }`;`AA_CONTRAST_PAIRS: ReadonlyArray<ContrastPair>`;阈值 text=4.5、large-text/graphic=3.0(`WCAG_AA_RATIO`、`WCAG_AA_LARGE_RATIO`)。

- [ ] **Step 1: 写失败测试** — contrast.test.ts 增 `parseColor` 对 `rgba(15,23,42,0.45)` 的 alpha 混合断言(对指定底色合成后计算亮度)、`meetsAA(ratio, kind)` 大文本阈值断言。
- [ ] **Step 2: 运行确认失败** — `cd frontend && npx vitest run src/design/__tests__/contrast.test.ts` → FAIL。
- [ ] **Step 3: 实现** — `contrast.ts` 增 `parseColor(value): {r,g,b,a}`,`compositeOver(fg, bg)` alpha 合成,`meetsAA(fg,bg,kind='text')`;tokenValues.ts 补新 token(两套一一对应,暗色 selection 底取品牌暗变体 `#1e3a8a` 级 + 高对比文本;mark 同族配对;skeleton 亮/暗中性灰;code 高亮亮/暗各一套,与所在表面 ≥4.5:1)并登记新配对(text/marked/selection/code 文本对底为 text 组,`--color-focus-ring` 对 bg 为 graphic 组)。
- [ ] **Step 4: 运行确认通过** — vitest 该文件 PASS。
- [ ] **Step 5: 提交** — `git commit -m "feat(theme): 扩充语义 token(selection/mark/skeleton/code 高亮)与对比度配对表分级"`

## Task 2: token 生成管线(gen:tokens + 幂等防线)

**Files:**
- Create: `frontend/scripts/gen-tokens.mjs`
- Modify: `frontend/package.json`(`"gen:tokens": "node scripts/gen-tokens.mjs"`,`prebuild` 钩子)
- Regenerate: `frontend/src/design/tokens.css` · `tokens-dark.css` · `tokens-print.css`
- Modify: `frontend/src/design/base.css`(`@import './tokens-print.css';`)
- Test: `frontend/src/design/__tests__/tokens.test.ts`(保留 CSS↔TS 逐项一致为第二道防线,增生成头标记断言与 print 文件断言)

**Interfaces:**
- Produces: 生成文件首行注释 `/* 本文件由 scripts/gen-tokens.mjs 从 src/design/tokenValues.ts 生成 —— 禁止手改。 */`;`tokens-print.css` 为 `@media print { :root, :root[data-theme='dark'] { <颜色 token 亮色值> } }`(打印强制亮色,§4.3)。

- [ ] **Step 1: 写失败测试** — tokens.test.ts 断言两 CSS 首行含生成标记;断言 `tokens-print.css` 存在且颜色 token 等于 LIGHT_TOKENS 颜色子集。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 gen-tokens.mjs** — 动态 `import('../src/design/tokenValues.ts')` 不可直接 node 执行 → 脚本以正则/TS-strip 方式解析:用 `node --experimental-strip-types`?统一方案:脚本 `import { spawnSync } from 'child_process'` 调 `npx tsx`?仓库无 tsx。最简稳方案:gen 脚本 `import` 一个 **`.mjs` 镜像**——不行(双真源)。**采用**:tokenValues.ts 保持 TS,gen 脚本经 `node:module` + esbuild(Vite 依赖已含 esbuild)`buildSync(tokenValues.ts, {format:'esm'})` 转临时 mjs 再 import;输出 CSS 字符串拼接(按分组注释);写三文件。esbuild 为 vite 传递依赖,显式加入 devDependencies。
- [ ] **Step 4: 运行 `npm run gen:tokens` + vitest PASS;`git diff --exit-code -- src/design/tokens.css` 验证与既有内容一致(生成器输出须与现状逐项相同,差异仅头标记与新增 token)**。
- [ ] **Step 5: 提交** — `git commit -m "feat(theme): token CSS 改为构建期生成(gen:tokens + 幂等防线)"`

## Task 3: 对比度独立 CI 关卡

**Files:**
- Create: `frontend/scripts/check-contrast.mjs`
- Modify: `frontend/package.json`(`"check:contrast": "node scripts/check-contrast.mjs"`)
- Modify: `.github/workflows/frontend.yml`(独立 job `contrast`)

- [ ] **Step 1: 实现脚本** — 复用 esbuild 转译导入 tokenValues;对每个配对按 kind 取阈值;输出逐对报告(含大文本组单列);任一不达标 `process.exit(1)`;stdout 摘要 `PASS 32 pairs (text 27 / large-text 3 / graphic 2)`。
- [ ] **Step 2: 本地运行 PASS**;构造临时不达标 token 验证 exit 1 后还原。
- [ ] **Step 3: frontend.yml 增 job** — `contrast: runs-on ubuntu-latest, steps: checkout → setup-node → npm ci → npm run check:contrast`(独立于 quality,先于 e2e 快速失败)。
- [ ] **Step 4: 提交** — `git commit -m "ci(theme): 对比度校验独立关卡(亮/暗逐对 + 大文本/图形阈值)"`

## Task 4: AST 级硬编码色值门禁(Stylelint + ESLint 自定义规则)

**Files:**
- Create: `frontend/stylelint-plugins/no-hardcoded-colors.mjs`
- Create: `frontend/eslint-rules/no-hardcoded-colors.js`(+ `eslint-rules/index.js`)
- Create: `frontend/.stylelintrc.json`(extends stylelint-config-standard + 本地 plugin;`frontend/src/**/*.css`)
- Create: `frontend/theme-lint-exemptions.json`(`{"files": {"src/features/.../x.css": {"reason": "...", "lines": [..]}}}`)
- Modify: `frontend/eslint.config.js`(注册本地规则 `mesh/no-hardcoded-colors: error`)
- Modify: `frontend/package.json`(`"lint:css": "stylelint 'src/**/*.css'"`,lint 脚本合并)
- Modify: `.github/workflows/frontend.yml`(quality job 增 `npm run lint:css`)
- Test: `frontend/stylelint-plugins/__tests__/no-hardcoded-colors.test.mjs`(stylelint testRule API)·`frontend/eslint-rules/__tests__/no-hardcoded-colors.test.js`(RuleTester)

**规则语义(两规则一致):**
- 命中:`#hex`、`rgb()/rgba()/hsl()/hsla()/oklch()/oklab()`、CSS 命名色(维护静态表)、内联 `style={{ color/background/... }}` 字面量、JSX SVG `fill`/`stroke` 字面量。
- 覆盖属性位:`color`、`background`、`background-color`、`border*`、`outline*`、`fill`、`stroke`、`box-shadow`、`text-shadow`、`caret-color`、`accent-color`、`text-decoration-color`、`column-rule-color`。
- 放行:`var(--*)` 开头的值、关键字 `transparent|currentColor|inherit|initial|revert|revert-layer|unset|none|Canvas|CanvasText|Highlight|GrayText|LinkText|ButtonText|ButtonFace`(forced-colors 系统色仅在 `@media (forced-colors: active)` 块内放行)。
- 例外:节点前一行注释含 `mesh-data-color: <原因>` **且** 文件登记于 `theme-lint-exemptions.json`;否则 error。**无整文件白名单。**

- [ ] **Step 1: 写 Stylelint 规则测试**(stylelint `testRule`):命中 hex/rgb/命名色/box-shadow 颜色位;放行 var()/transparent;@media print 内豁免(打印块引用亮色固定值为生成产物之外的合法面——不,print 由 tokens-print.css 承载,仍走 var;@media (forced-colors: active) 内系统色放行);例外注释 + 登记放行;无登记仅注释 → 仍 error。
- [ ] **Step 2: 实现 Stylelint 规则至测试通过。**
- [ ] **Step 3: 写 ESLint RuleTester 测试**:JSX `style={{ color: '#fff' }}` 命中;`style={{ color: 'var(--color-text)' }}` 放行;`<svg fill="#000">` 命中;`fill="currentColor"` 放行;模板字符串颜色命中。
- [ ] **Step 4: 实现 ESLint 规则至测试通过。**
- [ ] **Step 5: 接入 lint 与 CI,提交** — `git commit -m "feat(theme): AST 级硬编码色值门禁(Stylelint + ESLint 自定义规则,逐文件例外登记)"`

## Task 5: 存量 CSS 债务迁移至语义 token

**Files:**
- Modify: `frontend/src/features/skills/skills.css`(≈52 处)·`autopilots/autopilots.css`(≈18)·`data-jobs/dataJobs.css`(≈7)·`projects/projects.css`(≈1)
- Modify: `frontend/src/design/tokenValues.ts`(缺失语义先在源补,随后 `npm run gen:tokens`)
- Modify: `frontend/theme-lint-exemptions.json`(仅数据色例外,如 skill 图标色板,行级登记 + 原因)

- [ ] **Step 1: 跑 `npm run lint:css` 取实际命中清单**(以扫描为准,不以估算)。
- [ ] **Step 2: 分类** — 表面/文本/边框/悬停/状态 → 映射既有或新增语义 token(如 `--color-surface-hover`、`--color-surface-sunken`、状态底浅变体 `--color-<tone>-bg`);数据色(图标色板)→ 例外登记。
- [ ] **Step 3: 逐文件替换 + `npm run gen:tokens` 同步生成物。**
- [ ] **Step 4: `npm run lint:css` 零命中(登记例外除外);vitest 全绿(tokens 镜像断言含新 token)。**
- [ ] **Step 5: 提交** — `git commit -m "refactor(theme): skills/autopilots/dataJobs/projects 硬编码色值迁移语义 token"`

## Task 6: 后端错误码升格 `invalid_theme_mode`

**Files:**
- Modify: `backend/src/mesh/validation.py`(`validate_theme` code → `invalid_theme_mode`,docstring 引 theme.md §3.3)
- Modify: `backend/tests/unit/test_validation.py` + auth `update_me` 测试 + workspace `update_workspace` 测试中的 code 断言

- [ ] **Step 1: 改断言**(三处测试先红):非法 theme → `code == "invalid_theme_mode"`、`details == {"theme": <v>, "supported": ["light","dark","system"]}`;显式 `null` 于 `PATCH /users/me` 合法(清除);`default_theme` 不接受 null。
- [ ] **Step 2: 运行确认失败** — `pytest tests/unit/test_validation.py tests/unit/auth tests/unit/workspace -q`。
- [ ] **Step 3: 改 `validate_theme` 一行 code。**
- [ ] **Step 4: 全量相关测试绿。**
- [ ] **Step 5: 提交** — `git commit -m "fix(theme): 非法主题值错误码升格 422 invalid_theme_mode(两端点统一)"`

## Task 7: 邀请预览返回 `appearance.default_theme`

**Files:**
- Modify: `backend/src/mesh/workspace/invitations.py`(`preview_invitation` 成功分支)
- Modify: `backend/tests/unit/workspace/test_invitations.py`(或对应预览测试文件)

**Interfaces:**
- Produces: 预览成功响应增 `"appearance": {"default_theme": <"light"|"dark"|"system">}`(工作区 `settings.default_theme`,缺失默认 `"system"`);失败分支(`valid: false`)**不含** appearance。

- [ ] **Step 1: 写失败测试** — 工作区 settings 设 `default_theme: dark` → 预览含 `appearance.default_theme == "dark"`;未设 → `"system"`;expired token → 无 appearance 字段。
- [ ] **Step 2: 运行确认失败 → 3: 实现(workspace.settings JSONB `.get("default_theme") or "system"`,与 workspace service 默认口径一致)→ 4: 绿。**
- [ ] **Step 3: 提交** — `git commit -m "feat(theme): 邀请预览公开 appearance.default_theme(有限公开字段,workspace.md §3.1)"`

## Task 8: 服务端协商链解析模块(web/appearance.py)

**Files:**
- Create: `backend/src/mesh/web/__init__.py` · `backend/src/mesh/web/appearance.py`
- Test: `backend/tests/unit/web/test_appearance.py`

**Interfaces:**
- `async def resolve_entry_appearance(session_factory, *, cookie_value: str | None, path: str, invitation_resolver) -> AppearanceResolution`
- `AppearanceResolution` 数据类:`mode: Literal["light","dark"] | None`(None = 无法精确解析,入口不注入)、`personalized: bool`(cookie 命中会话 → True,响应须 private,no-store)。
- 解析链(镜像 §2.2):cookie 缺失/无效 → `personalized=False`、mode=None(静态 shell);cookie 有效 → 用户 `settings.theme`:显式 light/dark → 该值终止;显式 system → 跟随 **服务端无法知 OS** → 不注入(mode=None,personalized=True——仍 no-store 防共享缓存);absent/null → 工作区默认:路径 `/w/{slug}/…` → 按 slug 查 workspaces.settings.default_theme(缺失 → system → mode=None);`/invite/{token}` 或 `/invite?token=` → 经 invitation_resolver(复用 preview 同源数据)取 default_theme;default_theme light/dark → 该值;system → mode=None。二值收敛:任何非 light|dark 的持久化值视为非法 → mode=None。
- 会话定位:`security.hash_token(cookie)` → `SELECT s.user_id, u.settings FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=$1 AND s.revoked_at IS NULL AND s.expires_at > now()`(只读;复用 auth.security)。

- [ ] **Step 1: 写失败测试(真源表)** — 用 sqlite/pg fixture 建 user+session:① 无 cookie → (None, False);② cookie + user.theme=dark → ("dark", True);③ user.theme=system → (None, True);④ user.theme=null + slug 工作区 default=dark → ("dark", True);⑤ user.theme=null + 无 slug 段 → (None, True);⑥ /invite/{token} + 邀请工作区 default=dark → ("dark", False)(未登录);⑦ 已撤销会话 cookie → (None, False);⑧ 过期会话 → (None, False);⑨ user.settings 损坏非法值 → (None, True) 不崩。
- [ ] **Step 2: 运行确认失败 → 3: 实现(单事务只读,异常吞咽为 mode=None——入口注入永不阻塞 HTML 响应)→ 4: 绿。**
- [ ] **Step 5: 提交** — `git commit -m "feat(theme): 服务端入口协商链解析(web/appearance,只读会话定位)"`

## Task 9: HTML 入口路由(web/entry.py)+ 应用接线

**Files:**
- Create: `backend/src/mesh/web/entry.py`
- Modify: `backend/src/mesh/config.py`(`frontend_dist_dir: str = "/srv/mesh/frontend"`,env `MESH_FRONTEND_DIST_DIR`)
- Modify: `backend/src/mesh/api/app.py`(所有 API 路由注册之后挂载入口;`dist_dir/index.html` 不存在则跳过挂载)
- Test: `backend/tests/unit/web/test_entry.py`(httpx ASGITransport + tmp_path dist)

**行为契约:**
- 路由:`GET /{path:path}`(含 `/`),仅当 `Accept` 含 `text/html` 且路径不以 `/api`、`/ws`、`/assets`、`/uploads`、`/favicon` 开头;否则 404(让 API/静态资源路径不被遮蔽——实际上 API 先注册已优先,此守卫为双保险)。
- 响应:以 dist/index.html 为模板,在 `</head>` 前按解析结果注入 `<script nonce="N">window.__MESH_APPEARANCE__={"mode":"dark"};</script>`(mode 经 `json.dumps` 且仅二值);无注入时模板字节不变。
- CSP:两内联脚本(注入数据脚本 + 既有 FOUC 脚本)统一经 nonce——**入口须同时给 FOUC 脚本补 nonce 属性**(模板替换 `<script>` → `<script nonce="N">`,仅首个无属性 script 标签);头 `Content-Security-Policy: default-src 'self'; script-src 'self' 'nonce-N'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self' ws: wss:; base-uri 'self'; object-src 'none'; frame-ancestors 'none'`(unsafe-inline 仅 style-src,Vite CSS 运行期注入所需;script 绝不 unsafe-inline)。
- 缓存:`personalized=True` → `Cache-Control: private, no-store`;否则 `Cache-Control: public, max-age=300`(静态 shell 分离,字节不变可缓存)。
- `Vary: Accept, Cookie`。
- nonce:`secrets.token_urlsafe(16)` 每请求新生成。

- [ ] **Step 1: 写失败测试** — tmp dist(最简 index.html 含 `<script>/*FOUC*/</script></head>`):① 无 cookie GET / → 200,正文不含 `__MESH_APPEARANCE__`,`Cache-Control: public`,`Content-Security-Policy` 不含 unsafe-inline 于 script-src;② 建会话 + cookie + user.theme=dark → 正文含 `window.__MESH_APPEARANCE__={"mode":"dark"}`,private,no-store,两个 script 标签均带同一 nonce,nonce 与 CSP 头一致;③ 两请求 nonce 不同;④ GET /api/ping 不被入口遮蔽(200 JSON);⑤ `Accept: application/json` → 404;⑥ dist 缺失时应用工厂不挂入口(GET / → 404,应用正常启动);⑦ 注入值二值收敛(构造 settings.theme="evil" → 不注入)。
- [ ] **Step 2: 运行确认失败 → 3: 实现 entry.py + config + app 接线 → 4: 绿。**
- [ ] **Step 5: 提交** — `git commit -m "feat(theme): HTML 入口中间件(__MESH_APPEARANCE__ 注入 + nonce CSP + 缓存分流)"`

## Task 10: 部署接线(nginx 反代 + 共享 dist 卷 + shell CSP)

**Files:**
- Modify: `frontend/nginx.conf`
- Modify: `frontend/Dockerfile`
- Modify: `docker-compose.yml`
- Create: `frontend/scripts/gen-shell-csp.mjs`(构建期从 dist/index.html 提取 FOUC 脚本算 sha256,输出 `dist/sha256.txt`)
- Modify: `frontend/package.json`(`"build"` 末尾串联 gen-shell-csp)

**契约:**
- nginx:`location / { try_files $uri $uri/ @app; }`;`location @app { proxy_pass http://api:8000; proxy_set_header Host/X-Real-IP/X-Forwarded-*; }`(proxy_pass 无 URI 部分 → 原路径透传);静态资源 `/assets/` 命中 try_files 不到达 api。
- 静态 shell CSP(直接命中 index.html 的路径——try_files 中 `$uri/` → index.html?根路径 `/` 命中 `index.html` 默认文件):`location = /index.html` 与 `location = /` 的 `add_header Content-Security-Policy "...script-src 'self' 'sha256-<FOUC_HASH>'..."` 由 Dockerfile 构建期 sed 替换占位 `__FOUC_SHA256__`。**注意**:经 `@app` 的响应头由 api 控制(已含 nonce CSP),nginx 不叠加。
- compose:卷 `frontend_dist`;frontend 服务 entrypoint:`cp -rn /usr/share/nginx/html/. /dist/ && exec nginx -g 'daemon off;'`(先拷后服务);api 服务挂载 `frontend_dist:/srv/mesh/frontend:ro` + env `MESH_FRONTEND_DIST_DIR=/srv/mesh/frontend`;api depends_on frontend service_healthy 不强制(入口对 dist 缺失容错)。

- [ ] **Step 1: gen-shell-csp.mjs + build 串联;本地 `npm run build` 产出 dist/sha256.txt。**
- [ ] **Step 2: nginx.conf 改造 + Dockerfile sed 替换 + entrypoint 拷卷。**
- [ ] **Step 3: compose 卷与 env 接线。**
- [ ] **Step 4: 本地冒烟** — `docker compose up -d --build frontend api` → `curl -s http://127.0.0.1:<frontend_port>/` 命中静态 shell(有 CSP sha256 头);登录取 refresh 后 `curl -H "Cookie: mesh_session=<refresh>" http://127.0.0.1:<port>/w/<slug>/board` → 正文含注入(需 api 已挂入口且 dist 卷就绪)。
- [ ] **Step 5: 提交** — `git commit -m "feat(theme): 生产入口链路接线(nginx @app 反代 + dist 共享卷 + shell CSP sha256)"`

## Task 11: 前端协商真源模块(themeNegotiation + themeLocator)

**Files:**
- Create: `frontend/src/design/themeNegotiation.ts`
- Create: `frontend/src/design/themeLocator.ts`
- Test: `frontend/src/design/__tests__/themeNegotiation.test.ts` · `themeLocator.test.ts`

**Interfaces:**
- `export type ResolvedTheme = 'light' | 'dark';`
- `export type ThemeSource = 'user' | 'workspace' | 'system';`
- `export function resolveThemeChain(input: { userTheme: ThemeMode | null; workspaceDefault: ThemeMode | null | undefined; systemPrefersDark: boolean }): { mode: ResolvedTheme; source: ThemeSource }` — 真源表:user light/dark → {mode, 'user'};user 'system' → {OS 值, 'user'}(本级终止,不看 workspace);user null/undefined → workspace light/dark → {mode,'workspace'};workspace 'system'|null|undefined → {OS 值,'system'}。
- `export function expectedRouteId(href: string, apiOrigin: string): string` — `/w/{slug}/…` → `${host}:w:${slug}`;`/invite` 前缀 → `${host}:invite`;其余 → `${host}:app`(host 取 `new URL(href).host`;apiOrigin 参数预留,当前以页面 host 为准)。
- `export function parseThemeLocator(raw: string | null, expectedId: string): ResolvedTheme | null` — JSON 解析失败/null/`id !== expectedId`/`mode ∉ {light,dark}` → null(先校验 id 再读 mode)。
- `export const THEME_LOCATOR_KEY = 'mesh.theme.active';`
- `writeThemeLocator(mode: ResolvedTheme, href = location.href): void`(try/catch;单键覆盖 `{id: expectedRouteId(href), mode}`);`clearThemeLocators(): void`(删 `mesh.theme.active` + 遗留 `mesh.theme` + 枚举 `mesh.settings.pending:` 前缀键删除——登出清理)。

- [ ] **Step 1: 写失败测试(真源表 8 行 + route id 表 4 行 + locator 白名单/非法 JSON/id 不符/mode 非法/清理枚举)。**
- [ ] **Step 2: 运行确认失败 → 3: 实现 → 4: 绿。**
- [ ] **Step 5: 提交** — `git commit -m "feat(theme): 协商链与分区 locator 真源模块(白名单收敛 + 登出清理)"`

## Task 12: settingsStore v2(null 语义 + 迁移 + 跨 tab + locator 回写)

**Files:**
- Modify: `frontend/src/state/settingsStore.ts`
- Modify: `frontend/src/state/preferencesSync.ts`(`invalid_theme_mode` 归一;theme:null payload)
- Modify: `frontend/src/api/userPreferences.ts`(类型 `theme?: ThemeMode | null`)
- Test: `frontend/src/state/__tests__/settingsStore.test.ts`(新文件段)·`settingsStoreSync.test.ts`(更新)

**契约:**
- `UserPreferences.theme: ThemeMode | null`,默认 `null`(absent = 继承工作区默认)。
- persist `version: 2`,`migrate(persisted, version)`:v1 → `theme: 'system'` 映射为 `null`(语义对齐:旧默认 system ≈ 未表达偏好,协商链自工作区默认起;旧显式 light/dark 保留),`mesh.theme` 旧镜像键删除。
- `setTheme(theme: ThemeMode | null)`:本地立即生效(乐观)+ locator 回写(解析后二值,由 ThemeProvider 触发——store 写 mode 偏好,locator 写解析结果)+ 同步 `PATCH {settings:{theme}}`(null 合法)。
- `hydrateFromServer(remote: { theme: ThemeMode | null; locale; timezone; updatedAt })`:服务端有值 → 覆盖本地;absent/null → 本地镜像保留但 preferences.theme 置 null(匿名本地值不充当账号偏好,§4.5 裁决)。
- 跨 tab:`window.addEventListener('storage')`,`mesh.settings.v1` 变更 → `set` 当前 preferences(不再二次同步服务端);`mesh.theme.active` 变更 → 仅触发 locator 重解析事件(自定义 `CustomEvent('mesh-theme-locator')`,ThemeProvider 监听)。模块级 `initCrossTabSync()` 由 main.tsx 调一次。
- 登出钩子:导出 `onLogoutCleanup()`(clearThemeLocators + pending 队列非当前主体清理),由 authStore 登出路径调用。

- [ ] **Step 1: 写失败测试** — 默认 null;v1→v2 迁移(system→null、dark→dark);setTheme(null) 发 `{settings:{theme:null}}`;hydrateFromServer 两分支;storage 事件同步(invoke listener);invalid_theme_mode 错误归一码。
- [ ] **Step 2: 运行确认失败 → 3: 实现 → 4: 绿(含既有 settingsStore 测试适配)。**
- [ ] **Step 5: 提交** — `git commit -m "feat(theme): settingsStore v2(theme null 语义 + persist 迁移 + 跨 tab 同步 + 登出清理)"`

## Task 13: ThemeProvider 链式解析 + skeleton + 实时联动

**Files:**
- Modify: `frontend/src/design/ThemeProvider.tsx`
- Create: `frontend/src/design/ThemeSkeleton.tsx` · `frontend/src/design/skeleton.css`
- Create: `frontend/src/hooks/useWorkspaceDefaultTheme.ts`(镜像 useWorkspaceLocale:workspace detail `settings.default_theme`)
- Modify: `frontend/src/shell/AppShell.tsx`(workspace.updated 订阅:无显式偏好 → 重取工作区默认并重解析)
- Modify: `frontend/src/design/index.ts`(导出)
- Test: `frontend/src/design/__tests__/ThemeProvider.test.tsx`(重写扩充)

**契约:**
- 解析输入:store.preferences.theme + useWorkspaceDefaultTheme()(工作区上下文外返回 null)+ matchMedia;输出经 resolveThemeChain;应用 `document.documentElement.dataset.theme = mode`;每次解析完成 `writeThemeLocator(mode)`。
- pending/skeleton:挂载即同步解析(链中工作区默认为异步 fetch:未到位且 user theme 为 null 且无注入/locator 首帧值时 `resolved=false`);`resolved=false` 期间 `props.children` 不渲染,渲染 `<ThemeSkeleton />`(中性灰阶,token 取色);`<html data-theme-pending>` 属性联动 CSS 隐藏闪烁面。**首帧已由 index.html 脚本设置 data-theme 时(注入/locator 命中),provider 首渲染即 resolved,不闪 skeleton。**
- system 跟随:仅当链尾或 user=system 时注册 matchMedia change;显式 light/dark 忽略系统变化;卸载注销。
- workspace.updated:AppShell 订阅(经 realtime useRealtime 既有通道),事件 settings 含 default_theme 变更且 `preferences.theme == null` → 刷新 useWorkspaceDefaultTheme 缓存并重解析(经 store bump 或 query 失效)。

- [ ] **Step 1: 写失败测试** — user null + workspace dark → data-theme=dark 且 locator 写入 {id: 当前路由, mode:dark};user system + workspace dark + OS light → data-theme=light(忽略工作区);pending → skeleton 渲染、children 缺席;解析完成 → children 出现、pending 属性移除;matchMedia 触发 system 态切换、显式态忽略;卸载注销(change 后无副作用)。
- [ ] **Step 2: 运行确认失败 → 3: 实现 → 4: 绿。**
- [ ] **Step 5: 提交** — `git commit -m "feat(theme): ThemeProvider 链式解析(协商链 + skeleton 兜底 + workspace.updated 联动)"`

## Task 14: index.html 三级链路与 meta theme-color

**Files:**
- Modify: `frontend/index.html`

**契约(内联防闪烁脚本重写):**
```
1) 注入优先:window.__MESH_APPEARANCE__?.mode ∈ {light,dark} → 应用,return;
2) locator:localStorage['mesh.theme.active'] JSON → parseThemeLocator(raw, expectedRouteId(location.href)) → 命中应用,return;(id 校验先于 mode;非法/不符丢弃)
3) 均无:document.documentElement.setAttribute('data-theme-pending',''),不设 data-theme(交 skeleton;CSS :root 默认亮 token 不为错主题——pending 属性使骨架覆盖视口)。
存储访问全程 try/catch;脚本不含任何用户输入拼接。
```
- `<head>` 增 `<meta name="theme-color" media="(prefers-color-scheme: light)" content="#f9fafb">` 与 dark 变体 `#1e293b`(值 = 表面 token;构建期静态,运行期由 JS 于显式切换时改写单条 `media=""` 值)。

- [ ] **Step 1: 单测先行** — 将脚本主体抽为 `frontend/src/design/__tests__/inline-bootstrap.test.ts` 可测的纯函数 `bootstrapFirstFrame(win)`(index.html 内联其为压缩镜像;tokens.test 式断言 index.html 内联脚本与源函数行为一致——或接受双份:源函数单测 + e2e 断言真实 index.html 行为;此处取后者减双真源:index.html 脚本由 vitest `fs.readFileSync` 读出 + jsdom `eval` 执行,四场景断言)。
- [ ] **Step 2: 改 index.html,运行断言通过。**
- [ ] **Step 3: 提交** — `git commit -m "feat(theme): 首帧三级链路(注入 → 分区 locator → skeleton)+ meta theme-color 双声明"`

## Task 15: 邀请接受页协商链接通

**Files:**
- Modify: `frontend/src/api/invitations.ts`(`InvitationPreview.appearance?: { default_theme: ThemeMode }`)
- Modify: `frontend/src/workspace/pages/InviteAcceptPage.tsx`(preview 到位且账号偏好 null → 以 appearance.default_theme 作第 2 级,经 resolveThemeChain 即时应用 + locator 写 `{host}:invite` 分区)
- Test: `frontend/src/workspace/__tests__/InviteAcceptPage.test.tsx`(更新)

- [ ] **Step 1: 写失败测试** — preview 返回 appearance.default_theme=dark、store.theme=null → data-theme=dark;账号显式 light → 忽略 preview;preview 无 appearance → system 解析。
- [ ] **Step 2: 红 → 3: 实现 → 4: 绿。**
- [ ] **Step 5: 提交** — `git commit -m "feat(theme): 邀请接受页经预览 appearance.default_theme 解析(未登录第 2 级)"`

## Task 16: 登录回填与 pending 分区队列

**Files:**
- Create: `frontend/src/state/pendingSettingsQueue.ts`
- Modify: `frontend/src/state/preferencesSync.ts`(失败写入进队列)
- Modify: `frontend/src/shell/bootstrap.ts` 或 main.tsx 登录回填路径(`fetchCurrentUserPreferences` → `hydrateFromServer`)
- Test: `frontend/src/state/__tests__/pendingSettingsQueue.test.ts`

**契约:**
- 键 `mesh.settings.pending:{host}:{user_id}:{workspace_id}`;条目 `{payload, baselineUpdatedAt, retryCount, subject:[host,user,ws]}`。
- 重放触发:`online` 事件、`visibilitychange` visible、下次偏好写入;重放前校验当前活跃主体三元组与条目一致,不一致 → **不重放**(保留?规范:不重放,可丢弃该条——采丢弃并记录)。
- 冲突:重放前 `GET /me` 取 `updated_at`;服务端 > 条目 baseline → 丢弃条目、采用服务端值(hydrate);否则重放 PATCH。
- pending 清空后本地与服务端一致(lastSyncError 清除)。

- [ ] **Step 1: 写失败测试(fake timers + mock client)** — 分区键格式;主体不符不重放;服务端较新丢弃并 hydrate;较旧重放成功清键;online/visibility 触发;retryCount 上限(3 次后丢弃,防坏条目无限循环)。
- [ ] **Step 2: 红 → 3: 实现 → 4: 绿。**
- [ ] **Step 5: 提交** — `git commit -m "feat(theme): 偏好写失败 pending 分区队列(三元组校验 + 服务端优先冲突策略)"`

## Task 17: 暗色细部 CSS(选区/autofill/print/透明度/高对比/forced-colors)

**Files:**
- Modify: `frontend/src/design/base.css`

**契约(逐条对应 §4.3):**
- `::selection { background: var(--color-selection-bg); color: var(--color-selection-text); }`、`mark { ... mark tokens ... }`;
- `input:-webkit-autofill { -webkit-box-shadow: 0 0 0 1000px var(--color-surface) inset; -webkit-text-fill-color: var(--color-text); }`(亮/暗各自表面色自动取);
- `@media (prefers-reduced-transparency: reduce) { .mesh-scrim, [class*='scrim'] { background: var(--color-surface); } }`(scrim 降级不透明,对比达标);
- `@media (prefers-contrast: more) { :root { --color-border: <增强值 token 化>; --color-text-muted: <加深>; } }`(媒体查询内 token 重赋值,非第三套主题;值登记 token 源?媒体块内直接覆写 token 值为合法——仍经 var 体系,扫描器放行 `@media (prefers-contrast)` 块内对 token 变量的再赋值,不放行裸色值——故增强值亦须在 tokenValues 登记为 `--color-*-high-contrast` 后引用);
- `@media (forced-colors: active) { :root, :root[data-theme='dark'] { --color-bg: Canvas; --color-text: CanvasText; --color-border: CanvasText; --color-focus-ring: Highlight; --color-text-muted: GrayText; --color-primary: LinkText; } .mesh-raised { border: 1px solid ButtonBorder; } .mesh-chart-selfproof { forced-color-adjust: none; } }`(系统色关键字——扫描器在 forced-colors 块内放行,Task 4 已约定);
- print 经 tokens-print.css(Task 2)+ `@media print { :root { color-scheme: light; } }`。

- [ ] **Step 1: base.css 逐条落地 + tokenValues 增高对比增强 token + gen:tokens。**
- [ ] **Step 2: vitest(tokens 镜像)+ lint:css(forced-colors 系统色放行路径验证)绿。**
- [ ] **Step 3: 提交** — `git commit -m "feat(theme): 暗色细部(选区/mark/autofill/print/透明度/高对比/forced-colors)"`

## Task 18: markdown UGC 内联色兜底 + 代码高亮双色板

**Files:**
- 定位 marked 渲染组件(`grep -rn "marked(" frontend/src` → chat/comment 渲染器)
- Modify: 该渲染器组件 + 其 CSS

**契约:**
- 代码块:`<pre><code>` 经 `--color-code-*` token(亮/暗各一套,Task 1 已登记);
- UGC 内联 `style` 颜色:渲染后遍历 `[style*="color"]` 元素,计算其 color 与当前表面色(getComputedStyle `--color-surface` 解析值)对比,<4.5:1 → `el.style.color = 'var(--color-text)'`(与 §6.15 不可信内容同边界;主题切换后重算——经 MutationObserver 或主题 change 事件重扫)。

- [ ] **Step 1: 写组件测试** — 渲染含 `<span style="color:#000">` 的 markdown,暗色表面下断言 color 被覆写为 var(--color-text);亮色下保留;代码块类名带 token 化样式。
- [ ] **Step 2: 红 → 3: 实现 → 4: 绿。**
- [ ] **Step 5: 提交** — `git commit -m "feat(theme): markdown 代码高亮双色板 + UGC 内联色对比兜底"`

## Task 19: 设置 UI(个人外观四态 + 工作区默认 admin 入口)

**Files:**
- Modify: `frontend/src/shell/pages/SettingsPage.tsx`
- Modify: `frontend/src/workspace/pages/WorkspaceSettingsPage.tsx`
- Modify: `frontend/src/i18n/messages/zh-CN.ts` · `en.ts`
- Test: 对应页面测试更新

**契约:**
- 个人外观 Select 四选项:`default`(跟随工作区默认——显示当前解析值占位「跟随工作区默认(暗)」)/ light / dark / system(「跟随系统(暗)」标注当前 OS 解析,与「跟随工作区默认」措辞区分);当前值为 null 时 default 选中;「恢复跟随默认」动作 = setTheme(null);错误横幅支持 `invalid_theme_mode` 码本地化(details.supported 列表)。
- 工作区设置:新增「默认主题」section(admin 可见,非 admin 不渲染),三态 Select(light/dark/system,默认 system),说明文案「成员未单独设置时生效」;变更经 `updateWorkspace({settings:{default_theme}})`,成功即时生效(既有 workspace.updated 链路联动其他成员)。
- 全部文案经 react-intl 键(无硬编码可见文案)。

- [ ] **Step 1: 写页面测试** — 四态渲染与选择(选 default → PATCH theme:null;选 system → 'system');占位含工作区解析值;非 admin 无默认主题 section;admin 改 default_theme → PATCH settings.default_theme;错误码横幅。
- [ ] **Step 2: 红 → 3: 实现(复用 default_locale 既有 UI 模式)→ 4: 绿。**
- [ ] **Step 5: 提交** — `git commit -m "feat(theme): 外观设置四态(恢复跟随默认写 null)+ 工作区默认主题 admin 入口"`

## Task 20: 真实 e2e——无闪错三场景 + locator 分区 + 跨 tab

**Files:**
- Create: `frontend/playwright.theme-real.config.ts` · `frontend/e2e/real-theme-chain.spec.ts` · `frontend/e2e/real-theme-locator.spec.ts`
- Modify: `frontend/package.json`(`test:e2e:theme-real`)
- Modify: `frontend/e2e/helpers.ts`(复用真实后端工具;按需扩展 seed 工作区 settings)

**前置:** 真实后端栈(docker compose up postgres redis api worker gateway,MESH_AUTH_MODE=dev)+ vite dev 指向真实后端(同 playwright.real.config.ts 模式)。注入链路经 Playwright `context.addCookies([{name:'mesh_session', value:<登录响应 refresh>, url: baseURL}])` 建立(HttpOnly 可由 Playwright 设定;sessions 行真实存在)。

**场景清单(对应 §5.1):**
1. A(默认暗)→ B(默认浅)切换:cookie 会话 + 导航 B 路由 → 入口 HTML 含 `__MESH_APPEARANCE__={"mode":"light"}`(page.content 断言,注入链路命中);首帧 data-theme 历史(addInitScript MutationObserver 记录)无 dark 帧。
2. 换账号:暗色偏好账号登出(clearThemeLocators 断言 localStorage 无 `mesh.theme.active` 与遗留键)→ 无偏好账号登入 → 按工作区默认解析,不串用。
3. 邀请接受页(未登录,无 cookie):默认暗工作区邀请链接 → 入口经 preview 同源注入暗色 → 首帧 dark,无白闪。
4. locator 四子场景:A 暗/B 浅双 storage,B 写 locator 后 A 路由刷新 → skeleton 后暗(不读 B 分区);前进/后退 route_id 校验;非法 locator(`javascript:` / 任意串 / id 不符)→ 丢弃进 skeleton;登出清理断言。
5. 跨 tab:两 page 共享 context,A setTheme(dark) → B 经 storage 事件 data-theme=dark(不刷新)。

- [ ] **Step 1-6: 逐场景 TDD(先写断言跑红——部分断言在 Task 10/13 完成后方具备真绿条件)→ 全绿提交** — `git commit -m "test(theme): 无闪错三场景 + locator 分区 + 跨 tab 真实 e2e"`

## Task 21: 视觉回归门禁(toHaveScreenshot)

**Files:**
- Create: `frontend/playwright.visual.config.ts` · `frontend/e2e/visual/theme-visual.spec.ts` · `frontend/e2e/fixtures/seed-visual.mjs`(mock-server 常量数据扩充:看板/issue 详情/成员/聊天/运行详情/收件箱六页恒定 fixture)· `frontend/e2e/fixtures/fonts/`(内置 OFL 字体 woff2,latin 子集;fixture 文案 latin 化)
- Create: `frontend/e2e/visual/theme-visual.spec.ts-snapshots/`(基线 PNG 入库)
- Modify: `.github/workflows/frontend.yml`(独立 job `visual`:playwright install chromium → `npm run test:e2e:visual`;失败上传 actual/expected/diff 三元组产物)
- Modify: `.github/pull_request_template.md`(新建或追加:视觉基线变更说明项 + Windows 高对比真机核对清单)

**确定性环境:** `timezoneId: 'UTC'`、`locale: 'zh-CN'`、`clock` 冻结(playwright `browserContext.clock.install` 固定时间戳)、字体:`@font-face` 注入内置文件 + `document.fonts.ready` 等待 + 测试样式表强制 `* { font-family: 'Mesh Test Font', sans-serif }`;视口 `1024×768` 与 `768×1024`;动态区 mask:时间戳/presence/头像底色元素以 `mask: [locator(...)]`;`toHaveScreenshot({ maxDiffPixelRatio: 0.01 })`(逐用例只可收紧)。CI 常规跑不带 `--update-snapshots`;基线更新经独立 PR。

- [ ] **Step 1: mock-server 六页常量 fixture + 字体落地 + config。**
- [ ] **Step 2: 写 24 用例(6 页 × 2 主题 × 2 视口),本地 `--update-snapshots` 生成基线入库。**
- [ ] **Step 3: 复跑零 diff 验证稳定(连跑 3 次)。**
- [ ] **Step 4: CI visual job + PR 模板。**
- [ ] **Step 5: 提交** — `git commit -m "test(theme): 双主题视觉回归门禁(6 页 × light/dark × 双视口,确定性环境)"`

## Task 22: forced-colors 验收(仿真 + 真机清单)

**Files:**
- Create: `frontend/e2e/visual/forced-colors.spec.ts`(并入 visual config 或 theme-real)
- Modify: `.github/pull_request_template.md`(真机核对清单)

**契约:** `page.emulateMedia({ forcedColors: 'active' })` 覆盖核心页矩阵:断言 `getComputedStyle(document.body).backgroundColor` 解析为系统色语义(Chromium 计算值断言 `Canvas` 等效 rgb)、raised 表面有显式 border(boxShadow 失效下层级可辨)、`forced-color-adjust: none` 元素存在且生效。

- [ ] **Step 1: 写断言 → 2: 跑红/绿迭代 base.css forced-colors 块(Task 17 已落地则多为验证)→ 3: 提交** — `git commit -m "test(theme): forced-colors 仿真验收 + 真机核对清单入 PR 模板"`

## Task 23: CI 总接线 + gen:tokens 幂等断言

**Files:**
- Modify: `.github/workflows/frontend.yml`

- [ ] **Step 1: quality job 增** — `npm run gen:tokens && git diff --exit-code`(幂等断言:手改生成文件即失败);`npm run lint:css`;`npm run check:contrast` 已在独立 job。
- [ ] **Step 2: e2e job 增 theme-real 套件?**(theme-real 需 compose 后端——GitHub Actions service container 起 postgres/redis + 后端进程;评估既有 real-* 套件是否在 CI 跑:frontend.yml 仅默认 mock 套件。**theme-real 纳入 CI 需后端服务——采:CI 以 postgres/redis service + `pip install` 后端 + uvicorn 起服务,或维持 theme-real 为合入门禁的本地必跑项并在 PR 模板登记。采后者(YAGNI:CI 已有后端 pytest 覆盖入口逻辑;浏览器层 mock 套件 + visual 覆盖渲染),PR 模板增「theme-real 本地通过」勾选项。**
- [ ] **Step 3: 提交** — `git commit -m "ci(theme): gen:tokens 幂等断言 + lint:css 入 quality,visual/contrast 独立关卡"`

## Task 24: 文档同步

**Files:**
- Modify: `README.md`(实现状态表 theme 行 → ✅ 全功能;§6.12 实现注记)
- Modify: `frontend/README.md`(主题体系:token 生成管线、三级首帧链路、门禁清单、开发者指引——改 token 只改 tokenValues.ts + npm run gen:tokens)
- Modify: `backend/README.md`(HTML 入口中间件:会话只读定位、注入契约、缓存/CSP 边界)
- Modify: `CHANGELOG.md`(theme v1.0.0 条目)

- [ ] **Step 1: 逐文件更新,不暴露参考来源,与实现逐条核对。**
- [ ] **Step 2: 提交** — `git commit -m "docs(theme): README/前后端 README/CHANGELOG 同步主题全功能"`

## Task 25: 全量自测 + 评审 + PR

- [ ] **Step 1: 后端** — `pytest --cov=mesh --cov-fail-under=90`(全绿,整体 ≥90%)。
- [ ] **Step 2: 前端** — `npm run typecheck && npm run lint && npm run lint:css && npm run test:coverage`(整体与新增 per-file ≥90%,verify-perfile-coverage 绿)。
- [ ] **Step 3: 幂等** — `npm run gen:tokens && git diff --exit-code && npm run check:contrast`。
- [ ] **Step 4: e2e** — 默认 mock 套件 + visual + theme-real(真实栈)全绿。
- [ ] **Step 5: 真实 UI 操作自测(双主题 × 核心页面)** — 起 compose 全栈,浏览器实际操作:登录/外观四态切换/工作区默认/邀请页/看板/issue/成员/聊天/运行/收件箱,亮暗各走一遍(截图证据)。
- [ ] **Step 6: Quick Start** — `docker compose up --build` 从零跑通。
- [ ] **Step 7: code-reviewer + security-reviewer agent 双评审,CRITICAL/HIGH 清零。**
- [ ] **Step 8: push 前自查** — `git log @{u}..HEAD --format=%B | grep -i co-authored-by` 无输出;author/committer 均 cnwenf。
- [ ] **Step 9: gh pr create → main;PR 描述含验收矩阵对照。**
- [ ] **Step 10: issue 结果评论 + 状态 in_review。**
