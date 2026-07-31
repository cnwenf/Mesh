# MES-111 批次③ 设计对齐走查存证 — 成员/Agent 名册 + 收件箱 + 聊天

真实浏览器(Chromium)走查截图,覆盖 **桌面 1440×900 / 手机 390×844 × 亮 / 暗**
四组合,逐页逐按钮真实操作(真实后端栈 `docker compose -p mes126b3`:
postgres + redis + minio + api + worker + gateway + frontend;走查经 compose 前端
`:18130` 同源反代,生产形态)。验证 design-quality.md §3.2/§4.4 成员行/收件箱行/
聊天行 + §7.2 头像徽标 + §7.6 行规 + §8.2 触控/悬浮 + §9.8 运行反馈五态统一语言
+ competitor-parity-checklist 成员/通知/聊天条目落地后的真实渲染与交互。

每组合 14–15 步,文件名 `{组合}-{NN}-{步骤}.png`,共 58 张,
经 `scripts/check-evidence-unique.mjs` 校验 **md5 全部互异**(每步存证唯一 #1)。

## 走查内容(每组合)

| 步 | 文件后缀 | 验证点 |
|----|----------|--------|
| 01 | members-roster | 同一名册:底座 Avatar(人类缩写稳定 hash / agent 统一轮廓)+ 名称/类型/角色/状态主次分行 + AI 徽标;手机=主次行卡片、表格隐藏、无横向溢出(A-05 收尾) |
| 02 | members-role-changed | 角色改动(真实 PATCH + 落库校验):人类成员行下拉即时生效;手机经卡片内下拉(触控可达) |
| 03 | agent-detail | agent 详情深链(名册唯一入口):头部底座 Avatar + accent 徽标 + §9.8 运行态五态徽标(data-state)+ 容量三元组说明 |
| 04 | inbox-list | 收件箱双栏(分组列表 + 预览):未读点 / 优先级 Badge / 来源头像一致排列;手机单栏 |
| 05 | inbox-preview | 选中通知预览窗:标题 + 优先级 + 来源(agent/human)+ 正文 + 操作 |
| 06 | inbox-read | 标已读(真实 POST + read_at 落库);手机返回列表呈已读态 |
| 07 | inbox-deeplink | 深链:查看来源 → `/issues/{id}#comment-{anchor}` |
| 08 | inbox-archived | 归档(真实 POST + 行移除) |
| 09 | chat-new-session | 新建会话(真实操作:选 agent → 创建) |
| 10 | chat-streamed | 流式发送(真实 SSE,内建上游逐块回复)+ agent 运行态徽标 queued→running |
| 11 | chat-stopped | 停止生成(真实 stop + 终态) |
| 12 | chat-regenerated | 重生成(新候选流式) |
| 13 | chat-attachment | 会话内附件(真实 MinIO 三段直传 + 扫描门 + 气泡附件卡;经 dev server 联调口径,同 MES-59) |
| 14 | chat-context-collapsed | 上下文条收起/展开(§3.2 可收起条) |
| 15 | phone-chat-list | (仅手机)会话 → 返回列表(单栏路由化)+ 输入区粘底 |

## 运行方式

```bash
# 1) 真实后端栈(独立 project,自定义端口避免与他分支互踩)
docker compose -p mes126b3 up -d --build postgres redis minio api worker gateway frontend \
  MESH_API_PORT=18126 MESH_WS_PORT=18127 MESH_STORAGE_PORT=18128 \
  MESH_STORAGE_CONSOLE_PORT=18129 MESH_FRONTEND_PORT=18130 \
  MESH_STORAGE_PUBLIC_ENDPOINT=http://127.0.0.1:18128

# 2) dev server(附件直传步联调口径;后端未开 CORS)
VITE_MESH_API_BASE_URL=http://127.0.0.1:18126 \
VITE_MESH_WS_BASE_URL=ws://127.0.0.1:18127 \
  npx vite --port 5326 --host 127.0.0.1

# 3) 四组合走查 + 存证
npx playwright test -c playwright.mes111b3.config.ts
node scripts/check-evidence-unique.mjs e2e/evidence/mes111-b3
```

## 已知说明

- 附件直传步另开 dev server 页面(`:5326`,同 MES-59 `real-attachment` 口径):compose 形态
  的 HTML 入口 CSP 为 `connect-src 'self'`(`backend/web/entry.py`,生产同源反代),与
  「浏览器直连对象存储」的联调口径不同,故该步沿用 dev server + `--disable-web-security`。
- 走查前经 API 服务端 dismiss 引导清单 + 写入服务端主题偏好,避免清单接管层遮挡路由页、
  避免 preferencesSync 覆盖预置主题,保证亮/暗存证撞色与 md5 唯一性。
