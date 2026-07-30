# MES-111 批次②走查存证(mes111-b2)

真实浏览器(Chromium)+ 真实后端栈(api/gateway/worker + PostgreSQL + MinIO,
`MESH_AUTH_MODE=dev`)走查截图,覆盖 design-quality.md §3.2/§8.3/§9.4/§9.5 与
competitor-parity-checklist.md §2.7/§2.8/§2.10/§2.22 的批次②范围,四组合
(桌面 1440×900 / 手机 390×844 × 亮/暗)真实操作:

## 桌面(1440×900)

- `desktop-light-01-board-drag.png` — 看板鼠标拖拽落位(拖拽副本浮层 + 目标列高亮
  + 落点指示条 → 乐观落位,live region 播报)。
- `desktop-light-02-board-keyboard-move.png` — 键盘移动模式(聚焦卡片 → 方向键选
  目标列/位置 → Enter 确认,§10.2 非拖拽等价路径)。
- `desktop-light-03-list-layout.png` — List 布局真实表格(G7 必修:占位 → 分组
  表格/列头排序/行内编辑/多选批量)。
- `desktop-light-04-issues-bulkbar.png` — issue 列表 DataView:标题栏 + 过滤
  chips + 表头 + 粘底批量条(全选后出现)。
- `desktop-light-05-detail-tabs.png` — issue 详情 DetailLayout:桌面两栏 + 320px
  属性侧栏 + summary chips + 内联标题 + 活动/评论 Tab。
- `desktop-light-06-comment-retry.png` — 评论提交失败(中断一次)保留正文 + 重试
  成功(§9.5.4);草稿自动保存「已保存」弱提示。
- `desktop-light-07-comment-undo.png` — 评论删除 → toast 短时撤销 → 恢复(§9.5.5)。
- `desktop-light-08-attachment-retry.png` — 附件上传失败(中断 upload-requests
  一次)→ 进度环卡片显示重试 → 重试成功。
- `desktop-dark-01-board.png` / `desktop-dark-02-issues.png` /
  `desktop-dark-03-detail.png` — 暗色主题同页走查(独立校准 surface/边界/状态)。

## 手机(390×844,触控)

- `mobile-light-01-compact-board.png` — 紧凑看板(§8.3 单泳道 + 顶部 chips 切列)。
- `mobile-light-02-touch-move.png` — 长按召唤目标列 sheet(§9.4.6,不依赖精细
  横向拖动;block 满载列禁用带原因;含列内排序动作)。
- `mobile-light-03-properties-drawer.png` — 详情页属性收为「属性」按钮 → 底部
  Drawer sheet(§8.3),summary chips 保留关键状态。
- `mobile-light-04-comment-undo.png` — 手机评论发布 + 删除撤销。
- `mobile-dark-01-compact-board.png` / `mobile-dark-02-issues.png` /
  `mobile-dark-03-detail.png` — 暗色主题手机走查。

走查用例:`e2e/mes111-b2.spec.ts`(配置 `playwright.mes111-b2.config.ts`)。
每张截图均为不同页面/状态/主题组合,md5 唯一性经 `scripts/check-evidence-unique.mjs`。
