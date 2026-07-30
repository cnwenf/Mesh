# MES-111 Phase 0 前端手机可达性走查存证

## 审查基线

- 日期:2026-07-30
- 分支:`agent/mesh/9a9753a4`(MES-111 Phase 0,基于 `main@6971f547` / v0.23.1)
- 浏览器:Chromium headless(Playwright)
- 主题:light / dark 各走查
- 视口:320×640、390×844、1280×720
- 数据:mock 契约服务端(`e2e/mock-server.mjs`,README §6.14 包络 / §6.7 实时契约忠实镜像),真实邮箱密码登录 `jane@corp.com`

## 本轮实际走查

走查入口:`npx playwright test e2e/mes111-reachability.spec.ts`(12 用例)。

| 走查 | 结果 | 覆盖 |
| --- | --- | --- |
| 底部主导航四项可达 + aria-current | 通过 | 390×844,工作台/工作项/看板/聊天 |
| 「更多」抽屉承载全部次级导航,点选关闭跳转 | 通过 | 390×844,11 个次级入口逐项 |
| `/skills/marketplace` 刷新直达 + `/marketplace` 兼容重定向 | 通过 | 死链修复(A-01) |
| 顶栏搜索键入/回车展开统一命令面板并携带查询 | 通过 | A-02 假搜索修复 |
| skip link 键盘首焦直达主内容 | 通过 | §10.2 |
| 中文「自动值守/运行环境」两个不同条目 | 通过 | zh-CN,§4.1 去重名 |
| 首页/看板/成员无页面级横向溢出 | 通过 | 390×844 三页 `scrollWidth<=clientWidth` |
| 看板手机形态整体不超视口 | 通过 | 390×844(A-04) |
| 暗色主题底部导航与抽屉完整可用 | 通过 | 390×844 dark |
| 320px 极窄视口五入口可达、无横向溢出 | 通过 | 320×640 |
| 桌面侧栏导航完整 + 亮暗存证 | 通过 | 1280×720 light/dark |

退出条件核对(design-quality §12 Phase 0):**所有主导航在 320px 可达 ✅;无死链 ✅、无假搜索 ✅、无页面级横向溢出 ✅。**

## 走查截图

| 页面 | 截图 | 结论 |
| --- | --- | --- |
| 桌面首页 · 亮 | [desktop-home-light.png](desktop-home-light.png) | 侧栏/顶栏搜索/skip link 在场 |
| 桌面首页 · 暗 | [desktop-home-dark.png](desktop-home-dark.png) | 暗色整组 token 替换正常 |
| 手机看板 · 亮 | [phone-board-light.png](phone-board-light.png) | 导轨堆叠、列容器内横滚、无页面级溢出 |
| 手机成员抽屉流程 · 亮 | [phone-members-drawer-flow-light.png](phone-members-drawer-flow-light.png) | 抽屉点选跳转成员页 |
| 手机抽屉中文导航 · 亮 | [phone-drawer-zh-nav-light.png](phone-drawer-zh-nav-light.png) | 「自动值守/运行环境」不再重名 |
| 手机抽屉 · 暗 | [phone-drawer-dark.png](phone-drawer-dark.png) | 暗色底栏/抽屉层级(暗色首页态存证由 mes111-foundation/phone-home-dark.png 承载,同态不重复存证) |
| 手机首页 · 320px | [phone-home-320-light.png](phone-home-320-light.png) | 极窄视口五入口可达 |

截图由 `mes111-reachability.spec.ts` 在真实走查中生成,经 `scripts/check-evidence-unique.mjs` md5 唯一性校验。

## 复查边界

- 本存证覆盖 Phase 0 退出条件(可达性/死链/搜索入口/溢出);视觉精修(令牌/排版/组件矩阵)随 Phase 1 底座另行存证。
- 看板手机完整单泳道模式、成员主次行卡片化属逐页批次(design-quality §8.3),本阶段仅消除阻断级溢出。
- 320/390/768/1024/1440 全宽度矩阵与 112 格四组合存证由阶段三最终验收完成。
