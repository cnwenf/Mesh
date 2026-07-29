## 变更说明

<!-- 简述本 PR 的目标与实现要点。 -->

## 测试计划

- [ ] 单元/组件测试通过（覆盖率 ≥ 90%）
- [ ] 本地 `npm run lint` / `npm run typecheck` 通过
- [ ] 相关真实 e2e 走查通过

## 视觉基线变更（theme.md §5.4 暗色视觉回归门禁）

> 仅当本 PR 触及 UI 渲染 / 设计 token / 字体 / 布局时填写。

- [ ] 本 PR **未**改变任何视觉基线（`*.spec.ts-snapshots/*.png` 无变更）
- [ ] 本 PR **包含**视觉基线变更，说明如下：

  变更原因：<!-- 例如 token 调整 / 组件重构 / 新增页面 -->

  受影响页面与主题：<!-- 看板 / issue 详情 / 成员 / 聊天 / 运行详情 / 收件箱 × light / dark × 桌面 / 平板 -->

### 基线更新规则（强制）

- 视觉基线文件（`frontend/e2e/visual/**/*.png`）的更新**只能**经一个**独立 PR**完成：该 PR 仅运行 `npm run test:e2e:visual -- --update-snapshots` 生成基线，不夹带其他逻辑变更，并经**评审批准**后方可合入。
- CI 常规跑**只比对、不更新**基线；任何视觉差异都会让 `visual` job 失败。失败产物（`actual` / `expected` / `diff` 三元组）随 job 上传，供核对。
- 不要通过放宽 `maxDiffPixelRatio`（逐用例只可收紧、不可放宽）或扩大 mask 区域来“消除”差异；差异应先排查确定性漏洞（动画 / 字体回退 / 时间 / 随机色）或确认为预期的视觉变更。

## Windows 高对比 / 对比主题真机核对（theme.md §5.4 forced-colors 验收）

> 仿真（`page.emulateMedia({ forcedColors: 'active' })`）与真实 Windows 强制色存在实现差，**真机为最终依据**。仅当本 PR 触及 forced-colors / 边框 / 焦点环 / 阴影 / 自证对比区时核对。

- [ ] 已在 **Windows 高对比 / 对比主题（Edge）** 真机核对，结果如下（或注明不适用）：
  - [ ] 语义 token 落系统色（Canvas / CanvasText / Highlight / GrayText / LinkText），文本可读
  - [ ] 结构边界可见：raised 表面有显式 border（box-shadow 失效不破坏层级辨识）
  - [ ] 焦点环随系统 Highlight，按钮 / 输入框可辨识
  - [ ] 自证对比区 `forced-color-adjust: none` 元素在系统色板内仍可读

  核对环境：<!-- 例如 Windows 11 / Edge 1xx / “对比主题: 夜空” -->
