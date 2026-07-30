# MES-111 批次① 走查存证 —— 登录/注册全流程 + 首页工作台

真实浏览器(Chromium + mock 契约栈)逐页截图,覆盖 **桌面 1440×900 / 手机 390×844 /
320×568 × 亮/暗** 四组合,验证 `docs/specs/features/design-quality.md` 在
`src/features/auth`、`src/shell/pages/*` 登录/注册/MFA/找回/重置、`/device`、`/invite`、
OAuth 回调与首页工作台(`HomePage`)的落地。

走查命令:`npx playwright test -c playwright.mes111-b1.config.ts`(14 用例全绿)。
截图 md5 两两唯一(存证唯一性门禁通过)。

## 登录 / 注册全流程(PublicFlowShell,§4.4 / §3.2 认证行 / §9.2)

| 截图                                                           | 视口/主题 | 验证点                                                                                                              |
| -------------------------------------------------------------- | --------- | ------------------------------------------------------------------------------------------------------------------- |
| [desktop-login-light.png](desktop-login-light.png)             | 1440 亮   | 品牌区(mesh mark + 名)+ 单任务卡 + 安全/帮助信息三段式;分段登录/注册控件;44px 触控字段;首屏邮箱自动聚焦(焦点环可见) |
| [desktop-login-dark.png](desktop-login-dark.png)               | 1440 暗   | 暗色独立校准:surface→raised 分层、accent 主按钮、正文/边界对比充足                                                  |
| [phone-login-light.png](phone-login-light.png)                 | 390 亮    | 手机形态卡片完整、控件 44px 命中区                                                                                  |
| [phone-login-dark.png](phone-login-dark.png)                   | 390 暗    | 手机形态暗色:表面/边框/焦点环独立校准                                                                               |
| [phone-login-320-light.png](phone-login-320-light.png)         | 320 亮    | 320px 无页面级横向溢出                                                                                              |
| [desktop-register-light.png](desktop-register-light.png)       | 1440 亮   | 注册模式:展示名 + 密码强度条在场                                                                                    |
| [desktop-login-error-light.png](desktop-login-error-light.png) | 1440 亮   | **可操作错误**:账号锁定单独提示 + 恢复建议(「等几分钟或重置密码」);密码字段失败不清空(§9.2)                         |

- 第三方/账号分隔线(「or」)、vendor 中立的 SSO 按钮组在场;首屏聚焦首个可编辑字段(§9.2.1)。
- MFA 二步显示步骤(2/2)/ 目标 / 恢复路径,验证码 `autocomplete="one-time-code"` + `inputmode="numeric"`(单测覆盖)。
- 标签页标题随 登录/注册/MFA 语义变化(G19)。

## 其他公共流程页面(共用 PublicFlowShell,§3.2 第二行)

| 截图                                                 | 验证点                                                                                            |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| [desktop-forgot-light.png](desktop-forgot-light.png) | 找回密码统一外壳;恒成功防枚举;返回登录入口                                                        |
| [desktop-device-light.png](desktop-device-light.png) | 设备码授权统一外壳;手工录入码控件可见;scope/工作区/安全提示(单测覆盖批准绑定所录码、0/1/多工作区) |

- OAuth 回调页同样经 PublicFlowShell,交换失败具名 + 「返回登录」恢复动作(单测覆盖,回调为瞬时态不存静态截图)。
- 邀请接受页(`/invite/:token`)经 AppShell 内的卡片形态 + 动态标题 + 具名四 reason 恢复(单测覆盖)。

## 首页工作台(§3.2 首页行:无数据进 onboarding,有数据不展示演示内容)

| 截图                                                 | 视口/主题 | 验证点                                                                                                               |
| ---------------------------------------------------- | --------- | -------------------------------------------------------------------------------------------------------------------- |
| [desktop-home-light.png](desktop-home-light.png)     | 1440 亮   | 工作台:欢迎区(display-sm 排版)+ 工作区 + 快捷创建 + 我的工作(真实 issue 流);mock 有数据 → 不呈现 onboarding/演示内容 |
| [desktop-home-dark.png](desktop-home-dark.png)       | 1440 暗   | 暗色工作台分层一致                                                                                                   |
| [phone-home-light.png](phone-home-light.png)         | 390 亮    | 手机工作台无横向溢出                                                                                                 |
| [phone-home-dark.png](phone-home-dark.png)           | 390 暗    | 手机工作台暗色分层一致                                                                                               |
| [phone-home-320-light.png](phone-home-320-light.png) | 320 亮    | 320px 工作台无横向溢出                                                                                               |

- 有数据时不展示演示内容;空工作区(无 issue 且无项目)呈现 `OnboardingChecklist` 上手引导(单测 + 空态分支覆盖)。
- 「最近项目」小组件经真实 `listProjects`,加载/失败/空安静处理(失败不阻断工作台)。
- 错误态升级为四部分(影响说明 + 恢复),`ErrorState` 带 `impact`。
- 首页标签页标题随语义变化(G19)。

## 对照 checklist 自查(认证 / 外壳 / 首页节)

- ✅ 登录/注册/MFA/找回/重置/设备码/OAuth/邀请 共用 PublicFlowShell 外壳。
- ✅ 错误可操作且分开(锁定/凭据/限流/网络),贴近字段,密码失败不清空。
- ✅ 移动端 `inputmode`/`autocomplete`/44px 触控;首屏聚焦;软键盘不裁内容。
- ✅ 排版经 typography 工具类(title-1/3、display-sm、body-sm 等),视觉经语义 token,无新增硬编码色值(stylelint 门禁绿)。
- ✅ G19 标签页标题随页面语义变化;G12 登录页脚手架文案无残留(旧 `mesh-login` 样式已删)。
- 侧栏「自动化」双入口/中文命名属外壳/导航批次(非本批目录),按协同约定不在本批改动,合入时由对应批次解决。
- ⬜ 工作台「等待确认 / AI 运行」两块需 executions/autopilot-runs 聚合接口,本批以「我的工作 / 最近项目 / 快捷创建 / onboarding」落地,上述两块列入后续批次(已在 MES-111 反馈)。
