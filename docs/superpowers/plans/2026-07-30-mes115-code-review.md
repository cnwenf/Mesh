# MES-115 代码评审记录(requesting-code-review)

日期:2026-07-30 · 评审对象:PR #95(agent/mesh/bc65d738)· 评审方式:自审 + 验收员独立验收(MES-115 评论线程)

## 自审清单(提测前)

- [x] 组件 API 与主仓底座(PR #94)一致:Icon/Tooltip/Menu/Tabs/Badge/ErrorState 逐一对齐,无并行 API。
- [x] 图标名全量比对注册表(脚本化 audit:所有 `name="…"`/`icon: '…'` 引用 vs ICON_PATHS 键集),无未注册名(未注册即运行时崩溃)。
- [x] 门禁自指一致:新门禁规则自身带单测;`mesh/no-emoji-icons` 对全站 0 命中(例外经注释放行)。
- [x] 无障碍:role=switch/tablist/menu/dialog 语义、焦点进入归还、aria-sort、颜色非唯一信号(文本/图标/位置并行)。
- [x] 提交身份与无 co-author(`git log` 逐提交核验);全站 grep 无参考来源字样。

## 验收 R1 打回项与整改对照(2026-07-30 验收员)

| # | 级别 | 问题 | 整改 |
| --- | --- | --- | --- |
| H1 | 硬阻塞 | 6 个底座提交与 PR #94 并行冲突(59 处) | rebase 到最新 main 删除 6 提交;MES-115 增量整体移植到 PR #94 底座;token 值采用 main 现行值;基线/存证重生成 |
| H2 | 硬阻塞 | TopBar §4.2 缺失(品牌非链接;连接态文本常驻) | 品牌改 NavLink to=/;稳定态(connected/idle)仅状态点 + tooltip(role=img + aria-label),进行/异常四态显文本;测试同步 |
| M3 | Medium | Tabs roving 全组 tabIndex=-1 风险 | 受控 value 未命中/命中禁用项时回退首个可用项(可聚焦 + 呈现面板);补 2 例兜底测试 |
| M4 | Medium | DataTable 行 onClick 无 target 守卫 | closest(a/button/[role=button]/表单控件)早退,与 onKeyDown 守卫同构;补守卫测试 |
| M5 | Medium | no-emoji-icons 可绕过(带插值模板跳过;国旗区漏检) | 逐字面片段扫描;区间下扩 U+1F000–1F2FF(含 U+1F1E6–1F1FF 国旗);补门禁测试 |
| M6 | Medium | Menu 无视口夹持 | 打开时超右缘左移/超左缘右移(translate),底部溢出上翻(mesh-menu--above),与 Popover 同构;补 2 例 |
| 项7 | 改进 | shell/ 未纳入 per-file 门禁 | verify-perfile-coverage.mjs 增 `src/shell/`;壳层与页面文件补测至逐文件 ≥90 |
| 项8 | 改进 | 缺 superpower 过程痕迹 | 补本目录实施计划与评审记录两份文档 |
| 项9 | 改进 | e2e 冷启动并发超时 | playwright 默认配置限 workers(CI 稳态优先) |
| 项10 | 改进 | 存证目录名 | `e2e/evidence/mes111` → `mes111-shell`(spec 引用同步) |

## 复审请求

整改完成后请验收员复核:H1/H2 以 rebase 后 diff(仅增量、底座零重做)与 TopBar 实测为准;M3–M6 各有定向单测;门禁项以 `npm run lint` 与 per-file 门禁实跑为准。
