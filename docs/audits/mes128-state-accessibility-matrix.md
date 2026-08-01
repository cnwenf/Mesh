# MES-128 响应式、页面状态与无障碍收口记录

> 核对日期：2026-08-02
> 规范：`docs/specs/features/design-quality.md` §8、§10、§13.5
> 自动证据：`frontend/e2e/evidence/mes111-b5/manifest.json`

## 1. 本轮可复现结论

MES-128 将 13 个核心页面登记到同一个 `PAGES` 注册表。视觉、axe、媒体偏好、
reflow、触控目标和证据生成均读取这份注册表，新增页面不再需要在多套门禁中重复登记。

| 门禁     | 覆盖                                                               | 结果                                                      |
| -------- | ------------------------------------------------------------------ | --------------------------------------------------------- |
| 视觉基线 | 13 页 × light/dark × 390×844、768×1024、1024×768、1440×900         | 104 个基线                                                |
| 走查证据 | 13 页 × light/dark × 手机/桌面                                     | 52 张 PNG，manifest 完整性、宽度与 md5 唯一性 fail-closed |
| axe      | 13 页 × 1024 桌面/390 触控手机，WCAG 2 A/AA、2.1 A/AA、2.2 AA tags | 26 个页面扫描通过                                         |
| Reflow   | 13 页 × 320、390、640 CSS px（640 为 1280px 的 200% 等效宽度）     | `scrollWidth <= clientWidth`                              |
| 触控     | 13 页、真实 coarse pointer，按钮/输入/选择/菜单/Tab                | 可见目标均不小于 44×44px                                  |
| 媒体偏好 | 13 页 × forced-colors、prefers-contrast、reduced-motion            | 自动回归纳入视觉 job                                      |
| 结构契约 | 页面主地标、共享浮层、表格 caption/scope                           | 静态门禁通过                                              |

核心页为：登录、工作台、issue 列表、看板、issue 详情、成员、聊天、运行详情、
收件箱、自动值守、集成、洞察和设置。

## 2. 页面状态普查

标记：✅ = 已有实现且有单测/E2E；🟡 = 已有呈现或组件覆盖，但没有该页面的独立视觉
状态基线；❌ = 未形成可验收路径；— = 页面语义不适用。`正常`列由本轮 104 个视觉基线、
52 张存证及双视口 axe 共同验证；其余列是逐页实现与测试盘点，不能被解释为已完成全状态截图。

| 页面       | 正常 | loading / refreshing          | empty 有权 / 无权              | error 可重试 / 终态 | permission             | offline / stale     | 长内容/大数量/缺省字段          | 结论                   |
| ---------- | ---- | ----------------------------- | ------------------------------ | ------------------- | ---------------------- | ------------------- | ------------------------------- | ---------------------- |
| 登录       | ✅   | ✅ 提交 loading               | —                              | ✅ 具名错误与恢复   | —                      | 🟡 网络错误         | 🟡 中英目录，无长文视觉态       | 可用；长文状态待扩     |
| 工作台     | ✅   | ✅ 首载 skeleton / 分区刷新   | ✅ 无工作区、无 issue          | ✅ 分区重试         | 🟡 由成员身份裁剪      | 🟡 全局横幅         | 🟡 长名称 CSS 具备，无状态图    | 可用；状态视觉待扩     |
| issue 列表 | ✅   | ✅ skeleton / 保留数据重取    | ✅ 创建 CTA / 权限由入口裁剪   | ✅ 重试             | 🟡 角色门控            | ✅ 全局连接态       | ✅ card reflow、长标题换行      | 可用                   |
| 看板       | ✅   | ✅ skeleton / resync banner   | ✅ 快速创建 / 只读能力         | ✅ 重试与 WIP 回滚  | 🟡 由 `can_write` 控制 | ✅ reconnect/resync | ✅ 虚拟化大数量、长标题         | 可用；读屏虚拟化见 §5  |
| issue 详情 | ✅   | ✅ 首载/局部保存态            | 🟡 子项/附件/评论空态          | ✅ 保存冲突与重试   | 🟡 只读控制            | ✅ 全局连接态       | ✅ 长标题、缺负责人             | 可用                   |
| 成员       | ✅   | ✅ skeleton / 操作回写        | ✅ 邀请 CTA / 角色裁剪         | ✅ 重试             | ✅ 管理操作角色门控    | 🟡 全局横幅         | ✅ card reflow、缺头像          | 可用                   |
| 聊天       | ✅   | ✅ 会话/消息 loading、流式态  | ✅ 无会话/无消息               | ✅ 发送失败保留输入 | 🟡 可见性由 API 控制   | ✅ reconnect/resync | ✅ 长消息、缺头像               | 可用                   |
| 运行详情   | ✅   | ✅ 首载、日志续传             | 🟡 无日志/无产物               | ✅ 取消失败与终态   | 🟡 API 门控            | ✅ offset 续传      | ✅ 长日志受控滚动               | 可用                   |
| 收件箱     | ✅   | ✅ skeleton / 后台刷新        | ✅ 无通知                      | ✅ 重试             | 🟡 通知可见性后端裁剪  | ✅ 单栏重连         | ✅ 聚合数量、长预览             | 可用                   |
| 自动值守   | ✅   | ✅ skeleton / 列表重取        | ✅ 创建 CTA / 无工作区         | ✅ 重试             | ✅ admin 操作门控      | 🟡 全局横幅         | 🟡 长名称有换行，无大数量状态图 | 可用；状态视觉待扩     |
| 集成       | ✅   | ✅ skeleton / 健康检查        | ✅ 目录 + 未连接 / 只读 banner | ✅ 重试             | ✅ admin 与只读分支    | 🟡 全局横幅         | ✅ 手机表格卡片化、字段标签     | 本轮修复暗色与手机布局 |
| 洞察       | ✅   | ✅ 同形 skeleton / 时间窗刷新 | ✅ 空窗口/数据不足             | ✅ 可重试/成本超限  | ✅ 可见性过滤说明      | 🟡 全局横幅         | ✅ 图表重排、tabular numbers    | 可用                   |
| 设置       | ✅   | ✅ 分区 pending               | —                              | ✅ 同步错误 banner  | ✅ 分区路由与角色门控  | 🟡 同步网络错误     | 🟡 长文案靠响应式布局           | 可用                   |

## 3. 响应式与触控实现

- `src/design/tokenValues.ts` 是 compact 0–599、medium 600–1023、wide
  1024–1439、xwide ≥1440 的单一事实源；`src/design/responsive.ts` 提供同源运行时判断。
- `scripts/check-responsive-contract.mjs` 拒绝业务 CSS 新增近似 viewport 断点，并独立允许
  组件内在尺寸的 container query；同时校验 `viewport-fit=cover`。
- medium 外壳自动进入 rail；compact 使用底部主导航与“更多”抽屉。sticky/bottom-fixed
  元素统一使用顶栏、手机导航和 safe-area offset token。
- ConversationLayout 迁为 container query；Analytics、Skills 等旧 359/720/800/899
  viewport 阈值已归一到规范边界。
- coarse pointer 下基础控件与业务原生控件统一扩大命中区，能力不再依赖 hover 才可发现。
- 集成表格在 compact 下保留原生 table 语义，同时视觉重排为带字段名的单列卡片。

## 4. 无障碍实现

- `MAIN_CONTENT_ID` 集中定义；AppShell、PublicFlowShell、404 与全局错误页拥有主地标，
  业务页面不得再嵌套 `<main>`。登录、设备授权、OAuth、邀请等公共流程均有 skip link。
- 核心页面补齐唯一 `h1`；自动值守、Webhook、集成和技能详情的分区标题统一为连续的
  `h1` → `h2` 层级；Dialog/Drawer 的业务自造实现迁回共享浮层，保留焦点圈定、
  Esc 关闭和触发点焦点归还。
- 原生表格全部补 caption，列头补 `scope`；静态门禁阻止回归。
- 未读数和执行状态采用 polite live region；看板保留非拖拽移动入口和 live announcement。
- axe 是自动检测，不等价于屏幕阅读器人工验收。本执行环境没有 NVDA/VoiceOver 与音频
  输出，因而本轮**未声称完成人工读屏**。人工脚本应走“登录 → 键盘创建 issue →
  非拖拽移动看板卡 → 评论 → 收件箱深链”，并记录读序、焦点与播报文本。

## 5. 已登记的大缺口

以下项目超出“小修 + 门禁”边界，保持显式开放，不能用本轮绿色扫描替代：

1. 虚拟看板只挂载窗口内卡片，读屏连续浏览会跳过未挂载项；需要设计“无障碍列表模式”
   或按 AT 状态禁用窗口化，不能用增大 overscan 冒充完整修复。
2. 13 核心页的正常态视觉矩阵已经完整；loading/empty/error/long/offline/permission 的
   **逐页面视觉快照**尚未形成 13×状态全矩阵。组件与页面实现盘点见 §2，后续应以状态
   fixture 扩展，而不是复制正常态图片。
3. NVDA/VoiceOver 人工读屏仍需在具备对应桌面系统的验收环境执行。
4. G9 看板/issue 上下文快捷键组未完整注册；G15 feature flag 在产品 Spec 中仍属未来规划，
   不能仅由前端擅自实现；G17 `agent.trigger_skipped` 的组件级呈现尚无权威选型。
5. 本轮自动 reflow/axe/视觉门禁覆盖 13 个 §13.5 核心页；其余路由仍由路由可达性、
   组件测试和既有 E2E 保护，尚未扩成相同的全状态矩阵。

## 6. 旧 token 别名债务

`scripts/legacy-token-baseline.json` 记录精确的 `{file, token, count}` 基线；门禁禁止新增
文件/token 对或增加任一现有计数，删除始终允许。本轮盘点共 **263 次 / 110 个文件-token
对**，只建账，不在别名兼容周期内批量替换。

| 别名              | 次数 | 别名                                        |  次数 |
| ----------------- | ---: | ------------------------------------------- | ----: |
| `--color-primary` |   47 | `--color-primary-contrast`                  |    10 |
| `--color-danger`  |   43 | `--color-danger-contrast`                   |     8 |
| `--color-warn`    |   34 | `--color-warn-bg` / `--color-warn-contrast` | 1 / 6 |
| `--color-success` |   23 | `--color-success-contrast`                  |     7 |
| `--color-info`    |   32 | `--color-info-contrast`                     |     5 |
| `--space-5`       |   32 | `--space-6`                                 |    15 |

删除计划：兼容一个发布周期后另起变更，按语义逐处迁移；不得直接改旧 spacing 的变量值，
因为旧 `space-5/6` 的实际像素含义与新阶梯并不一一相同。

## 7. 复现命令

```bash
cd frontend
npm run check:responsive
npm run check:a11y-contract
npm run check:legacy-token-debt
npm run test:e2e:a11y
npm run test:e2e:visual
npm run check:evidence
```
