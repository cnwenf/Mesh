# MES-115 设计系统与全局壳层收敛(MES-111 Stage 1)—— 实施计划

日期:2026-07-30 · 负责人:Mesh 程序员 · 依据:docs/specs/features/design-quality.md(MES-109 Spec)§4.1/§5/§7/§11/§12 Phase 1

## 目标

按 MES-109 设计 Spec 完成系统层收敛,为 MES-111 后续逐页优化(Stage 2/3/4)提供唯一组件与布局基础:

1. 扩充颜色、排版、间距、控件、圆角、阴影、动效与 z-index token(承接 Phase 1 底座)。
2. 建立统一图标入口,替换产品 UI 中 emoji/字符图标(§7.1)。
3. 补齐 Field、Textarea、Checkbox、Switch、Tabs、Tooltip、Popover、Menu、Drawer、PageHeader、Toolbar、DataTable 等基础组件(§7.3–§7.7 全状态矩阵)。
4. 重构全局壳层:分组/可折叠侧栏、明确的 Autopilot/运行时/技能入口、移动端底部导航与「更多」抽屉(§4.1/§4.3)。
5. 修复可见导航与路由不一致、重复中文入口、原始 i18n key 泄露。
6. 建立组件状态 fixture 与 1440/1024/768/390、亮/暗视觉回归基础(§13.5)。
7. 静态门禁杜绝业务层回归:裸交互控件、emoji 图标、硬编码颜色、任意 z-index。

## 底座策略(验收 R1-H1 后的关键裁决)

Phase 1 底座存在两个并行实现:本分支早期携带的 6 个底座提交,与经 PR #94(f3938e5a)合入 main 的正式底座(MES-111 Phase 1)。**裁决:以 main(PR #94)底座为唯一底座**——

- rebase 到最新 main,删除 6 个重复底座提交(`git rebase --onto origin/main <底座末提交>`);
- token 值一律采用 main 现行值(如 `--color-text-muted: #5f6980`,不再并行校准);
- 组件 API 对齐主仓:`Icon/ICON_PATHS`(Icon.tsx,非 icons.tsx)、`Tooltip.content`、`Menu.entries`(key 化 + 分隔线)、`Tabs.items`(items 式非复合组件)、`Badge` children + tone `warning`、`ErrorState` 内置诊断 ID;
- MES-115 增量(新组件/壳层/门禁/图标清偿/fixture)全部移植到 PR #94 底座之上;图标注册表增量并入主仓 Icon.tsx(24 枚新增 + filled 实心变体 + upload),重名图标归一(`x→close`、`alert-triangle→warning`、`sparkles→sparkle`、`users→user`)。

## 组件补齐(design/components/,逐件 TDD)

| 组件 | 契约要点 |
| --- | --- |
| Field | label/control/hint/error 一体外壳;render-prop 下发 controlProps(id/aria-invalid/aria-describedby/aria-required)杜绝漏关联 |
| Textarea | 与 Input 同族字段契约;自适应高度钳制 maxHeight 后内部滚动 |
| Checkbox | 原生 input + 自绘盒体;indeterminate 半选经原生属性同步;焦点环经 :focus-visible + 盒体 |
| Switch | role=switch + aria-checked;拇指位置即状态(颜色非唯一信号);44px 命中区 |
| Popover | 非模态次级上下文浮层:portal + 焦点进入(首可聚焦元素)/归还、Esc/点外关闭、下方不足翻转向上、水平钳制 |
| PageHeader | 页面唯一 h1(title-1)+ eyebrow/描述/动作槽(§1.2 一页一主标题) |
| Toolbar | role=toolbar + aria-label,视图/筛选/批量操作容器 |
| DataTable | 语义 table + caption(可 sr-only)+ scope=col + aria-sort 排序上抛 + default/comfortable 密度 + 空态槽;行点击 closest 守卫(验收 R1-M4) |

Tabs 采用主仓 items 式实现,补焦点兜底(验收 R1-M3):受控 value 未命中/命中禁用项时回退首个可用项可聚焦,杜绝整组 tabIndex=-1。

## 壳层重构(shell/)

- `navigation.ts`:导航唯一事实源——四分组(工作/团队/运行/管理)× 图标 × 路由;桌面侧栏、手机底栏、「更多」抽屉共用,杜绝导航与路由不一致。
- Sidebar:240px 展开(组标题 + 图标 + 文字)↔ 64px 折叠 rail(Tooltip 补可读名,偏好 localStorage 持久化);当前项浅强调背景 + 3px 边缘指示(弃整块高饱和色,§4.1)。
- 入口清偿:`nav.automation` 含糊旧键 → `nav.runtimes`;命令面板补 skills/autopilots/runtimes 三入口;`g a` 跳 /autopilots。
- TopBar(§4.2):品牌为返回首页 NavLink;连接状态稳定态(connected/idle)仅状态点 + tooltip,进行/异常四态才显文本(验收 R1-H2)。
- 手机底栏/抽屉:图标 + 文字双通道,抽屉按四分组呈现。

## 图标清偿(§7.1)

注册表并入主仓 Icon.tsx 后 56 枚;产品 UI emoji/字符图标全站替换(skills 信任徽标 🛡👤🏪⚠、autopilots 触发器 🔀➕📝💬📣🔗⏰⚙️⏸、board ☰★✕、chat ✓★☆、inbox 🔔、onboarding ✓、comments ⏳、integrations 连接器 🐦💬🐙🦊📤)。回应 emoji 为用户内容,例外。

## 静态门禁

- eslint `mesh/no-emoji-icons`:JSX 文本/字符串/模板串字面片段(含带插值模板,验收 R1-M5 收紧)禁 emoji;区间覆盖 U+1F000–1FAFF(含国旗 U+1F1E6–1F1FF)、U+2600–27BF、U+231A–23FF、U+2B00–2BFF、U+FE0F;UGC 回应经 ignores 排除,键帽/开发诊断标记经行级 `mesh-emoji-ok` 注释放行。
- stylelint `mesh/zindex-token-only`:z-index 一律 `var(--z-*)` 层级令牌或局部 -1/0/1(存量 7 处散落值清偿)。
- Menu 补视口夹持/上翻(验收 R1-M6,与 Popover 定位同构)。
- 叠加既有 AST 级硬编码色值门禁(eslint + stylelint)。

## 视觉回归基础(§13.5)

- `/styleguide` 组件状态 fixture 页:全组件 × 全状态平铺,静态确定性(无时间戳/随机量),全页唯一 h1。
- playwright 视觉矩阵扩充 wide 1440×900 / phone 390×844 项目(仅拍 fixture),与既有 desktop/tablet 合成四视口 × 亮暗。
- 壳层有意改版,核心页双主题基线随 PR 重生成(Phase 0 先例);走查存证目录统一 `e2e/evidence/mes111-shell/`(验收 R1 项 10)。

## 验证门禁

- UT:全量 + per-file ≥90 门禁(本 PR 将 `src/shell/` 纳入目录门禁并补测,验收 R1 项 7)。
- lint(eslint + stylelint 含两条新门禁)/ typecheck / build / 对比度 / token 幂等。
- 真实浏览器 e2e:mes111-foundation(排版/令牌/状态矩阵/触控目标/reduced-motion/forced-colors)、mes111-reachability(桌面/390/320 导航可达 + 无横向溢出 + 双主题)、ui-baseline(壳层冒烟)、视觉回归四视口 × 亮暗。
- 提交身份 cnwenf <cnwenf@outlook.com>,无 co-author;不暴露参考来源。

## 阶段与并行

1. 底座移植:rebase 删 6 提交 + API 归一(串行,关键路径)。
2. 图标清偿:4 路 subagent 并行(skills / autopilots / board / 混合区)。
3. 壳层 + 门禁 + fixture:主线串行。
4. 验收 R1 整改:TopBar §4.2、Medium 四项、shell 覆盖门禁补测(2 路 subagent 并行:壳层文件 / 页面文件)。
