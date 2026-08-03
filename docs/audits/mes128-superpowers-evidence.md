# MES-128 工程工作流证据

> 日期：2026-08-02
> 范围：PR #118 验收整改
> 结论规则：只有代码、测试输出、浏览器证据与外部人工验收同时完成，才可转为 Ready。

本文记录本轮实际执行的 planning、TDD、debugging、verification 与 review 工作流。它不是
事后补写的完成声明；仍未满足的人工门禁会保持为显式阻断。

## 1. Writing plans

实施计划位于
`docs/superpowers/plans/2026-08-02-mes128-responsive-a11y-closeout.md`。计划先按六个验收阻断簇
定义完成条件，再列每簇的 RED、最小 GREEN、回归命令与证据目录。计划明确禁止用聚合覆盖率、
mock 正常态截图或“后续处理”替代硬条件。

## 2. Test-driven development

下表保留本轮实际观察到的 RED 和同一行为的 GREEN；“失败数”只表示目标用例，不把 unrelated
suite 的结果拼成证据。

| 行为                    | RED                                                                                          | 最小修复                                                                     | GREEN                                                                 |
| ----------------------- | -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| 页面快捷键优先级        | 页面上下文与全局快捷键同时处理，2 个断言失败                                                 | provider 按最近上下文仲裁，并尊重 `defaultPrevented`、输入框和 IME           | ShortcutProvider 目标套件 34/34                                       |
| 原生控件 Enter/Space    | window 级 spy 观察到快捷键错误接管，1 个断言失败                                             | 原生 activation target 直接交还浏览器                                        | 同一目标用例通过                                                      |
| 切工作区后搜索 scope    | 搜索仍请求第一工作区，真实流程在结果等待处失败                                               | palette 从规范路由 slug 解析当前工作区                                       | palette/shell 目标套件 95/95；真栈两视口通过                          |
| 变更文件发现器          | 新增 fixture 暴露目录白名单遗漏，2 个测试失败                                                | 从 base diff、index、worktree 与 untracked 动态发现源码                      | discovery 单测 15/15                                                  |
| 逐文件四指标            | 原实现漏扫；首次完整扫描有 15 个文件低于 90%                                                 | 补用户可见分支测试，未使用 coverage ignore                                   | 66/66 变更源码四指标均 ≥90%                                           |
| 巨型路由文件覆盖        | 把条件门控塞进 `App.tsx`/`BoardPage.tsx` 后动态门禁失败                                      | 门控下沉到 AppShell、BoardColumns；两巨型文件恢复为 base 内容                | 目标组件测试 89/89；动态门禁通过                                      |
| 44px 目标发现           | 链接、ARIA button、summary、checkbox/radio/custom fixture 未被旧选择器发现                   | 集中候选发现；严格校验实际命中框，checkbox/radio 可合并关联 label 的实际矩形 | 孤立 20×20 链接稳定失败；完整 a11y 128 passed/10 project skips        |
| 虚拟看板读屏            | 250 张卡片只能读到当前窗口                                                                   | 增加显式完整列表模式，关闭该模式下的窗口化                                   | 第 1 与第 250 张卡均在 DOM，移动操作保留                              |
| 工作区 feature flag     | `false` 时入口与内容仍可达                                                                   | API/后端校验、管理 UI、导航/命令/路由统一门控                                | workspace/feature gate 前后端目标测试通过                             |
| `agent.trigger_skipped` | 实时帧没有用户可见反馈                                                                       | 严格解析六类原因并显示可关闭 toast/issue 出口                                | parser 与 AppShell 帧形测试通过                                       |
| 非核心路由正常态        | 首次 fail-closed crawl 暴露 resolver VCS API 404 与可滚动 JSON 区域不可聚焦，共 6 个目标失败 | 补真实 fixture 响应，并让所有 JSON/代码滚动区可键盘聚焦                      | 63 路由访问/权限/重定向、46 条 extended 正常态 axe 与六档 reflow 全绿 |
| offline 视觉字体        | exact-head CI 有 4 张 offline 快照因冷 context 字体回退失败；修复后旧回退基线稳定 RED       | 断网前加载并验证 400/500/700 三档内置字体，重建全部 26 张 offline 基线       | 无更新模式重跑 26/26 通过                                             |

## 3. Systematic debugging

真栈流程每次失败都同时保留当前 URL/焦点、浏览器请求状态、服务日志和 PostgreSQL 断言，未用
route interception、mock 或重试放宽绕过：

1. 首次键盘移动失败定位为 Enter 事件继续冒泡到全局快捷键；修复 provider 仲裁后重新从登录
   开始运行，而不是跳过看板步骤。
2. 切换工作区后的搜索超时，通过请求路径确认仍使用旧 workspace id；修复 scope 来源后，断言
   请求命中第二工作区且 Enter 打开该工作区的 issue。
3. Compose 端口审计发现基础配置会发布中间件端口；override 改为显式 `!reset []`，最终渲染
   配置与运行中容器都只发布 `127.0.0.1:18430` 的前端入口。
4. 最终复验删除容器与卷后完整 build；任何一步失败都会阻止 manifest 写入，避免旧证据冒充
   本次成功。
5. 异常态视觉的单个 dark 用例偶发渲染成 light；网络记录定位到偏好 bootstrap 覆盖测试注入
   状态。测试基建改为显式走已定义的本地镜像 fallback，并让 scenario route 使用
   `fallback()` 而不是吞掉前置 guard；同一用例 light/dark 2/2 复验通过。
6. 扩展路由 crawl 的失败先按 URL、失败 API 与 active element 聚类：resolver 详情缺少 VCS links
   fixture，autopilot detail/run 的 JSON 滚动区没有键盘入口。补齐最小响应和 `tabIndex=0` 后先跑
   目标回归 7 passed/1 project skip，再跑完整 a11y 套件确认没有相邻路由回归。
7. 最终默认 E2E 刷新证据后，唯一性门禁发现成员抽屉流程与首页图片完全相同。视觉检查确认
   用例只等待了 URL，页面数据 404 后仍截到了错误/旧画面。先新增“成员卡可见”断言稳定复现
   RED，再复用 normal-state 名册 fixture；目标用例 GREEN 后重新生成 112-cell manifest，363 张
   截图唯一性和 SHA/尺寸门禁均恢复通过。
8. exact-head visual 失败仅集中在 4 张 offline 快照；下载 actual/expected/diff 后确认冷 CI context
   在断网后才注入字体，导致部分页面使用宿主回退字形，工作台基线还保留了旧项目计数。修复为
   断网前显式加载并验证三档字体；旧基线随即稳定 RED，重建 26 张基线后无更新模式 26/26 GREEN，
   未通过提高像素容差或遮罩正文绕过。

## 4. Verification before completion

### 4.1 已完成

- `npm run test:coverage`：362 个文件、3981 个测试全部通过；聚合
  lines 98.69%、functions 97.13%、branches 93.70%、statements 98.69%。随后动态门禁扫描
  66 个变更源码，四指标逐文件全部通过。
- `npm run test:e2e`：73/73；`npm run test:e2e:a11y`：128 passed、10 个 project 设计性
  skip；`npm run test:e2e:visual`：424 passed、44 个 project 设计性 skip。三套均 0 failed。
- `npm run test:e2e:visual` 同次核对 91 个状态 cell、146 张异常态快照；`npm run
check:evidence` 核对逐页矩阵 112/112、全局 363 张证据均唯一。
- `./frontend/e2e/mes128-real/run-e2e.sh`：从空卷构建 production 栈，phone-320 4.0s、
  phone-390 3.9s，2/2 通过；Playwright 总计 9.1s，runner 正常清理容器与卷。
- 真栈每档 PostgreSQL：1 user、1 active session、2 memberships、移动后的 issue 为
  `in_progress` 且 version=2、1 条精确评论、第二工作区 1 条搜索目标 issue。
- 真栈每档关键响应：register 201、login 200、issue create 201、non-drag move 200、comment
  201、workspace by-slug 200、second-workspace search 200。
- Compose：PostgreSQL/Redis/MinIO/API/gateway 无 host published port；凭据每次随机生成，
  `stack.env` mode 600 且不进入版本库。
- 12 张两视口流程截图、HTTP 序列、DB 值、尺寸与 SHA-256 位于
  `frontend/e2e/evidence/mes111-b5-real/`；敏感模式扫描无命中。
- `npm run build`、lint（0 error）、typecheck、contrast、responsive、a11y contract、legacy-token
  debt 与证据门禁全部通过；63 个叶子路由均进入 fail-closed manifest 与浏览器 crawl。

### 4.2 完成前仍须执行

- 最新 `origin/main` 同步、提交身份/co-author 检查及 PR exact-head CI。
- NVDA 或 VoiceOver 人工读屏。当前 Linux 执行环境不能运行这两种目标 AT，因此在外部对应
  桌面环境留下逐步记录前，PR 必须保持 Draft，不能把 axe/ARIA snapshot 写成替代证据。

## 5. Requesting code review

本轮以原 GitHub review 的六个阻断簇逐项自审；inline 的 touch selector 与审计缺口只有在代码、
测试和文档同时闭环后才可请求解决。独立复审应重点重跑动态逐文件门禁、真栈 runner、状态矩阵，
并核对人工读屏记录。由于 §4.2 的人工门禁尚未完成，当前不会转 Ready 或请求放行。
