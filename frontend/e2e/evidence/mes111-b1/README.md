# MES-111 批次① 走查存证 —— 登录/注册全流程 + 首页工作台(含整改补全)

真实浏览器(Chromium + mock 契约栈)逐页截图 + 全链断言,覆盖 **桌面 1440×900 / 手机
390×844 / 320×568 × 亮/暗** 四组合,验证 `docs/specs/features/design-quality.md` 在
登录/注册/MFA/找回/重置、`/device`、首页工作台的落地。

走查命令:`npx playwright test -c playwright.mes111-b1.config.ts`(**24 用例全绿**)。
截图 md5 两两唯一(20/20)。

**截图稳定化(应改 #4)**:config 设 `reducedMotion: 'reduce'` 禁用动画/过渡;每次截图前
`settle()` = `waitForLoadState('networkidle')` + `document.fonts.ready`,消除加载时机/字体
渲染不确定,作为回归基线前提。(像素级完全一致仍受字体 hinting 限制,故基线以结构/主题/
溢出判定为主,md5 作辅助。)

## 登录页 PublicFlowShell 四组合 + 错误态(§4.4 / §9.2)

| 截图                                                           | 视口/主题 | 验证点                                                          |
| -------------------------------------------------------------- | --------- | --------------------------------------------------------------- |
| [desktop-login-light.png](desktop-login-light.png)             | 1440 亮   | 品牌区 + 单任务卡 + 安全/帮助三段式;分段控件;44px 字段;首屏聚焦 |
| [desktop-login-dark.png](desktop-login-dark.png)               | 1440 暗   | 暗色独立校准                                                    |
| [phone-login-light.png](phone-login-light.png)                 | 390 亮    | 手机形态完整                                                    |
| [phone-login-dark.png](phone-login-dark.png)                   | 390 暗    | 手机暗色                                                        |
| [phone-login-320-light.png](phone-login-320-light.png)         | 320 亮    | 320px 无横向溢出                                                |
| [desktop-login-error-light.png](desktop-login-error-light.png) | 1440 亮   | 账号锁定**分开**提示 + 恢复建议 + 密码不清空                    |

## 注册页 PublicFlowShell 四组合(整改补全)

| 截图                                                     | 视口/主题 | 截图                                                   | 视口/主题 |
| -------------------------------------------------------- | --------- | ------------------------------------------------------ | --------- |
| [desktop-register-light.png](desktop-register-light.png) | 1440 亮   | [desktop-register-dark.png](desktop-register-dark.png) | 1440 暗   |
| [phone-register-light.png](phone-register-light.png)     | 390 亮    | [phone-register-dark.png](phone-register-dark.png)     | 390 暗    |

## 找回密码页 PublicFlowShell 四组合(整改补全)

| 截图                                                 | 视口/主题 | 截图                                               | 视口/主题 |
| ---------------------------------------------------- | --------- | -------------------------------------------------- | --------- |
| [desktop-forgot-light.png](desktop-forgot-light.png) | 1440 亮   | [desktop-forgot-dark.png](desktop-forgot-dark.png) | 1440 暗   |
| [phone-forgot-light.png](phone-forgot-light.png)     | 390 亮    | [phone-forgot-dark.png](phone-forgot-dark.png)     | 390 暗    |

## 设备授权

| 截图                                                 | 验证点                                                               |
| ---------------------------------------------------- | -------------------------------------------------------------------- |
| [desktop-device-light.png](desktop-device-light.png) | 统一外壳;手工录入码可见;scope/工作区/安全提示(单测覆盖 0/1/多工作区) |

## 认证全链路断言(Issue 自验收第 2 条 + 应改 #3,仓库内 e2e 存证)

- **MFA 全链路**:登录 `mfa@corp.com` → 二步界面 → 错码原位报错 → 正码 `123456` → 进首页。
- **注册全链路**:强度条 → 注册+登录 → 「已发验证邮件」结果页(含邮箱)→ 继续 → 进首页。
- **找回 → 重置全链路**(mock 新增 `POST /auth/forgot-password`、`/auth/reset-password`):
  发起重置 → 已发送;`BAD-TOKEN` → 报错 + 恢复出口(重新发起);`GOOD-TOKEN` → 重置成功。

## 首页工作台 四组合 + 五块断言(§3.2 首页行)

| 截图                                                                                              | 视口/主题      |
| ------------------------------------------------------------------------------------------------- | -------------- |
| [desktop-home-light.png](desktop-home-light.png) / [desktop-home-dark.png](desktop-home-dark.png) | 1440 亮 / 暗   |
| [phone-home-light.png](phone-home-light.png) / [phone-home-dark.png](phone-home-dark.png)         | 390 亮 / 暗    |
| [phone-home-320-light.png](phone-home-320-light.png)                                              | 320 亮(无溢出) |

五块断言(mock 返回真实数据):**我的工作 + 快速创建**(`home-dashboard`)、**等待确认**
(`home-waiting`,待我审批)、**AI 运行**(`home-ai-runs`,过滤终态成功执行)、**最近项目**
(`home-projects`)均渲染且深链正确;空/失败时安静隐藏(单测覆盖)。有数据不展示 onboarding/
演示内容;空工作区进 OnboardingChecklist。

## 对照 checklist 自查(认证 / 外壳 / 首页节)

- ✅ 登录/注册/MFA/找回/重置/设备码 共用 PublicFlowShell;错误可操作且分开、密码不清空。
- ✅ 登录/注册/找回 **四组合存证齐备**(整改补全 register/forgot 手机+暗色)。
- ✅ 注册→MFA→登录→回跳 与 找回→重置 **全链 e2e 存证**(整改补全 mock 端点 + 用例)。
- ✅ 工作台五块(我的工作/等待确认/AI 运行/最近项目/快速创建)全部接通真实端点(整改补全
  `GET /workspaces/{ws}/executions` 与 `/approvals?role=mine` 契约层 + 组件)。
- ✅ ForgotPassword 失败不再静默:catch 后通用可操作错误 + 重提交恢复,不泄露账号存在性。
- ✅ 排版经 typography 工具类、视觉全经语义 token;stylelint 无新增硬编码色值。
- 侧栏「自动化」双入口/中文命名属外壳/导航批次(非本批目录),合入时由对应批次解决。

## 建议项(不阻塞,自行决定随本批与否)

- MFA 验证码视觉「自动分组」(§9.2.4)未做,可粘贴单字段已满足 → 留后续。
- `index.html lang` 静态不随 locale → 留 i18n/外壳批次。
- `clean-room-rules.md` 词表与自检脚本落库 → 项目级,留后续。
