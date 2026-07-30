# 前端安全基线

> **权威**：MES-135 交付物。Mesh 前端实现必须遵循的安全基线，阶段二实现与阶段三 S3-C 安全审查均以此为依据。
>
> 基线代码：`main`（2026-07-30）。

---

## 1. XSS 防护

### 1.1 总原则

- **绝不**将用户输入直接插入 DOM（禁止未净化的 `dangerouslySetInnerHTML`、`innerHTML`、`document.write`）。
- 所有用户生成内容（UGC）在渲染前必须经过消毒（sanitize）。
- React 默认的 JSX 转义是第一道防线；需要渲染 HTML 的场景必须走 DOMPurify 白名单净化。

### 1.2 Markdown / 富文本渲染消毒策略

项目已使用 `marked` + `DOMPurify`（`frontend/src/features/comments/markdown.ts`），基线要求：

**允许标签集（ALLOWED_TAGS）**：

```
p, br, hr, h1-h6, strong, em, del, s, blockquote, code, pre,
ul, ol, li, a, img, table, thead, tbody, tr, th, td, span, div, input
```

**允许属性（ALLOWED_ATTR）**：

```
href, title, src, alt, class, target, rel, checked, disabled, type
```

**强制规则**：
- `ALLOW_DATA_ATTR: false`（禁止 `data-*` 属性，防利用自定义属性绕过）；
- `<a>` 标签必须强制 `rel="noopener noreferrer"`（DOMPurify hook 或后处理）；
- `<img src>` 仅允许 `https:` 和 `data:image/`（base64 内联小图），禁止 `javascript:` 协议；
- `<input>` 仅允许 `type="checkbox"`（GFM 任务列表），`checked`/`disabled` 只读渲染；
- 禁止 `<script>`、`<iframe>`、`<object>`、`<embed>`、`<form>`、`<style>`、`<link>`、`<meta>`；
- 禁止所有事件属性（`on*`）——DOMPurify 默认剥离，确认配置未覆盖此行为。

### 1.3 服务端净化为权威源

- 已落库评论的展示使用服务端净化的 `body_html`（comment-inbox.md §5.1），前端客户端净化仅用于编辑预览；
- 服务端净化规则须与前端白名单一致或更严格；
- 前端不得信任 `body_html` 以外的服务端 HTML 字段——若新增富文本字段，须同步更新白名单。

### 1.4 其他 XSS 向量防护

| 向量 | 防护措施 |
|------|----------|
| URL 注入（`javascript:` 伪协议） | 所有 `href`/`src` 渲染前校验协议白名单（`https:`/`http:`/`mailto:`/`data:image/`） |
| CSS 注入（`expression()`/`url(javascript:)`） | 不使用 CSS-in-JS 动态拼接用户输入；设计令牌为静态值 |
| SVG 内嵌脚本 | 上传的 SVG 须经 DOMPurify 净化或转为 PNG；禁止直接内联原始 SVG |
| `dangerouslySetInnerHTML` 滥用 | ESLint 规则 `react/no-danger` 设为 warn + 代码审查逐处确认 |

---

## 2. CSP（Content Security Policy）

### 2.1 建议策略

CSP 由 API 入口中间件按请求下发（per-request nonce），nginx 层补充加固头。建议生产 CSP：

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self' 'nonce-{RANDOM}';
  style-src 'self' 'unsafe-inline';
  img-src 'self' https: data:;
  font-src 'self';
  connect-src 'self' wss: ws:;
  frame-ancestors 'none';
  base-uri 'self';
  form-action 'self';
  object-src 'none';
  upgrade-insecure-requests;
```

### 2.2 说明

| 指令 | 理由 |
|------|------|
| `script-src 'nonce-{RANDOM}'` | 每次请求生成随机 nonce，仅带 nonce 的内联脚本执行；禁止 `unsafe-eval` |
| `style-src 'unsafe-inline'` | CSS Modules 编译为内联 style 标签；`unsafe-inline` 对 style 的 XSS 利用面极小 |
| `img-src 'self' https: data:` | 允许 HTTPS 外链图片（评论/头像）+ base64 内联小图 |
| `font-src 'self'` | 字体打包分发，不从第三方 CDN 加载（减少供应链风险） |
| `connect-src 'self' wss: ws:` | 允许同源 API + WebSocket 实时通道 |
| `frame-ancestors 'none'` | 防点击劫持（配合 `X-Frame-Options: DENY`） |
| `object-src 'none'` | 禁止 Flash/Java 插件嵌入 |
| `upgrade-insecure-requests` | HTTP 资源自动升级为 HTTPS |

### 2.3 现有加固头（nginx.conf 已实现）

```
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
X-Frame-Options: DENY
Permissions-Policy: camera=(), microphone=(), geolocation=()
```

---

## 3. Token 存储与鉴权请求模式

### 3.1 会话模型（auth.md §4.5）

| 组件 | 存储位置 | 说明 |
|------|----------|------|
| Access Token（短期 JWT） | `localStorage`（zustand persist → `mesh.auth.v1`） | 用于 `Authorization: Bearer` 请求头；JS 可读 |
| Refresh Token | **HttpOnly Cookie**（`mesh_session`） | 浏览器自动携带续期；**JS 永不持有 refresh 明文** |

### 3.2 安全要求

- Access Token 有效期短（≤15 分钟），降低 XSS 窃取窗口；
- Refresh 续期走 HttpOnly Cookie + SameSite=Strict，JS 无法读取；
- 登出时前端清除 localStorage 中的 access token + 主题/偏好缓存（`onLogoutCleanup`）；
- 401 响应触发全局兜底：清 token + 跳登录页（`unauthorized.ts`）。

### 3.3 HTTP 非安全上下文兼容（MES-129 教训）

> MES-129 暴露的问题：部分部署环境为 HTTP（非 HTTPS），此时 `Secure` Cookie 不可用。

**基线要求**：
- **生产环境必须 HTTPS**——这是不可妥协的安全底线；
- Cookie 属性：`Secure; HttpOnly; SameSite=Strict`（生产）；
- **开发/内网 HTTP 环境**：Cookie 降级为 `HttpOnly; SameSite=Lax`（去掉 `Secure`），由后端根据请求 `X-Forwarded-Proto` 动态决定；
- 前端代码不得因 HTTP 环境而跳过鉴权——token 存取逻辑不因协议变化；
- 登录页在非 HTTPS 环境下显示安全提示（"当前连接未加密"），但不阻断功能。

### 3.4 请求鉴权模式

```typescript
// 所有 API 请求统一携带（client.ts 已实现）
headers: {
  'Authorization': `Bearer ${getToken()}`,
  'Idempotency-Key': crypto.randomUUID(),  // POST/PUT/PATCH/DELETE
}
```

- GET 请求不携带 Idempotency-Key；
- Token 为空时不带 Authorization 头（登录前接口）；
- 鉴权豁免端点（登录/注册/MFA）的 401 不触发全局跳转。

---

## 4. 第三方资源引入边界

### 4.1 原则

- **零外部 CDN 依赖**：所有 JS/CSS/字体/图标打包到构建产物，不从第三方 CDN 运行时加载；
- 运行时仅与**同源后端**通信（nginx 反向代理，无 CORS）；
- WebSocket 连接仅连同源 gateway。

### 4.2 禁止引入

| 类型 | 示例 | 理由 |
|------|------|------|
| 运行时 CDN 脚本 | `<script src="https://cdn.xxx.com/lib.js">` | 供应链攻击面、SRI 难维护 |
| 第三方分析/追踪脚本 | Google Analytics、Mixpanel 等 | 隐私合规风险、CSP 复杂度 |
| 第三方字体 CDN | Google Fonts `<link>` | 隐私（IP 泄露给 Google）；打包分发替代 |
| 第三方 iframe 嵌入 | 第三方 widget | 点击劫持/数据泄露风险 |

### 4.3 例外（须审批）

若未来确需引入第三方运行时资源，须：
1. 安全审核员审批；
2. 配置 SRI（Subresource Integrity）`integrity` + `crossorigin="anonymous"`；
3. CSP 白名单精确到域名（不用 `*`）；
4. 定期审查第三方资源可用性与安全性。

---

## 5. 错误信息不泄露内部细节

### 5.1 前端错误展示规则

| 场景 | 展示内容 | 禁止展示 |
|------|----------|----------|
| API 4xx/5xx | 后端返回的 `message` 字段（面向用户的文案） | 堆栈、SQL、内部路径、服务名 |
| 网络错误 | "网络连接失败，请重试" | 内部 URL、IP、端口 |
| JS 运行时异常 | "页面遇到问题，请刷新"（+ 错误 ID 供排查） | 完整 Error.stack、变量值 |
| 鉴权失败 | "登录已过期，请重新登录" | Token 内容、Cookie 名 |

### 5.2 实现要求

- 全局 ErrorBoundary 捕获渲染异常，展示友好 UI + 上报错误 ID；
- `MeshApiError` 归一化（client.ts 已实现）：仅暴露 `message`/`code`/`status`；
- 生产构建禁用 React DevTools 提示、`console.log` 敏感输出；
- Vite 生产构建 `drop_console`（或仅保留 `console.error`）。

---

## 6. 依赖供应链基线

### 6.1 Lockfile 锁定

- `package-lock.json` **必须**提交到仓库，且与 `package.json` 同步；
- CI 使用 `npm ci`（非 `npm install`）确保确定性构建；
- 禁止在代码中引用 unpinned 版本（`latest`、`*`、`>=`）；
- Dependabot / Renovate 自动升级 PR 须经 `npm audit` + 许可扫描通过后方可合入。

### 6.2 已知漏洞检查命令

```bash
# 日常开发
npm audit

# CI 门禁（高危及以上阻断）
npm audit --audit-level=high

# 详细 JSON 报告（存档用）
npm audit --json > audit-report.json
```

### 6.3 新增依赖审查清单

每个新增依赖的 PR 须回答：

- [ ] 许可类型是否在白名单内？（asset-license-whitelist.md §9）
- [ ] `npm audit` 是否通过？
- [ ] 是否有已知恶意包同名/近似名（typosquatting 检查）？
- [ ] 维护状态：最近 6 个月是否有更新？star/下载量是否合理？
- [ ] 是否真的需要？能否用已有依赖或 10 行内自实现替代？

### 6.4 恶意依赖排查

```bash
# 检查是否存在 typosquatting 可疑包（与流行包名差 1-2 字符）
npm ls --all --json | python3 -c "
import sys, json
d = json.load(sys.stdin)
# 人工审查不常见包名
print('请人工审查以下非常见依赖:')
"

# 安装前检查（对单个包）
npm info <package-name> --json | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"name: {d.get('name')}\")
print(f\"maintainers: {d.get('maintainers')}\")
print(f\"scripts: {d.get('scripts', {}).get('install', 'none')}\")
"
# 关注: install/postinstall 脚本执行任意代码
```

---

## 7. 其他安全要求

### 7.1 CSRF 防护

- 状态变更请求（POST/PUT/PATCH/DELETE）携带 `Idempotency-Key`（已有）；
- 同源部署（nginx 反代）天然防跨站——无 CORS 配置；
- Refresh Cookie `SameSite=Strict` 阻止跨站携带；
- 若未来引入第三方集成回调，须增加 CSRF Token 机制。

### 7.2 点击劫持防护

- `X-Frame-Options: DENY`（nginx 已实现）；
- CSP `frame-ancestors 'none'`；
- 敏感操作（删除工作区、修改权限）增加二次确认。

### 7.3 敏感操作确认

以下操作须二次确认（模态对话框 + 输入确认文字）：
- 删除工作区
- 移除成员
- 修改计费/订阅
- 生成/撤销 API Token

### 7.4 文件上传安全

- 前端校验文件类型（MIME + 扩展名白名单）和大小（≤64MB，与 nginx `client_max_body_size` 一致）；
- SVG 上传后须服务端净化（去除 `<script>`/`on*` 属性）或转为 PNG；
- 上传文件不从原始路径直接 serve——经后端鉴权 + 重命名存储。

### 7.5 WebSocket 安全

- 连接建立后首帧鉴权（README §6.16）；
- Token 过期时服务端断开连接，客户端触发重新鉴权；
- 消息内容按 UGC 处理——渲染前走 §1 消毒流程。

---

## 8. 安全检查命令汇总

```bash
cd frontend

# 1. XSS: 检查未净化的 dangerouslySetInnerHTML 使用
grep -rn "dangerouslySetInnerHTML" src/ | grep -v "__tests__"

# 2. 硬编码 secrets
grep -rniE "(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{8,}" src/ \
  --include="*.ts" --include="*.tsx" | grep -v "__tests__" | grep -v ".test."

# 3. console.log 残留（生产不应有）
grep -rn "console\.log" src/ --include="*.ts" --include="*.tsx" \
  | grep -v "__tests__" | grep -v ".test."

# 4. 外部 URL 引用（不应有运行时 CDN）
grep -rniE "https?://(?!localhost|127\.0\.0\.1)" src/ \
  --include="*.ts" --include="*.tsx" | grep -v "__tests__" | grep -v "comment"

# 5. 依赖许可 + 漏洞
npx license-checker --summary --onlyAllow "MIT;ISC;Apache-2.0;OFL-1.1;BSD-2-Clause;BSD-3-Clause;CC-BY-4.0;CC0-1.0"
npm audit --audit-level=high
```

---

## 9. 合规矩阵（速查）

| 检查项 | 状态 | 证据 |
|--------|------|------|
| XSS: DOMPurify 白名单净化 | ✅ 已实现 | `features/comments/markdown.ts` |
| XSS: 服务端 body_html 权威 | ✅ 已实现 | comment-inbox.md §5.1 |
| CSP: nginx 加固头 | ✅ 已实现 | `nginx.conf` |
| CSP: per-request nonce | 🟡 待实现 | API 入口中间件（阶段二） |
| Token: HttpOnly refresh cookie | ✅ 已实现 | auth.md §4.5 |
| Token: 短期 access JWT | ✅ 已实现 | `state/authStore.ts` |
| 同源部署无 CORS | ✅ 已实现 | `nginx.conf` 反代 |
| 点击劫持: X-Frame-Options DENY | ✅ 已实现 | `nginx.conf` |
| 依赖 lockfile 锁定 | ✅ 已实现 | `package-lock.json` |
| 依赖漏洞检查 | 🟡 CI 待接入 | 本文件 §6.2 |
| 错误信息脱敏 | ✅ 已实现 | `MeshApiError` 归一化 |
| 文件上传校验 | 🟡 待验证 | 阶段二实现时核查 |
