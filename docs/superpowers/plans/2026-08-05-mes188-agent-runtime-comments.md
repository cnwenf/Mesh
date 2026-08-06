# MES-188 Agent、Runtime 与评论闭环实施计划

> 日期：2026-08-05
> 依据：`comment-inbox.md` §4.1/§4.3、`agent.md` §4.7–§4.10、
> `runtime.md` §4.5、`runtime-executor.md` §4.1/§4.2，以及前端 parity 清单。

## 目标与边界

本批只修改评论区、Agent 运行可观测、Runtime/Execution 审计和 issue 详情的执行区。
不修改 issue 属性区、看板属性编辑器或标签/成员管理区；与 MES-187 的交叉点仅通过现有
`IssueDetailPage` 主内容插槽组合。实现完成后必须等待 MES-187 合入，再 rebase 最新 `main`
并重跑全部门禁。

## 验收矩阵

| 条目 | 先写的失败断言 | 最小实现 | 真实验收 |
| --- | --- | --- | --- |
| L396 | resolve/reopen 保留回复聚合；system activity 不可解决；已解决区默认折叠 | 服务端返回完整 resolver 快照与聚合；评论区分组、留痕、重新打开 | 两个浏览器互相解决/重新打开，刷新后状态一致 |
| L401 | 乐观卡片有 sending/failed；同幂等键重试；HTTP/WS 去重；删除 tombstone | 本地投递状态机、稳定幂等键、按 id 合并、删除占位 | 断网失败后重试；另一浏览器收到最终态；删除仍可见占位 |
| L434 | enqueue/claim/审批/终态后容量三元组正确且事件可消费 | 服务端统一 presence 快照和事件；列表/详情共享映射 | 真实分派运行时观察 running/queued/approval 连续变化 |
| L435 | 分派生成 execution；claim 显示 runtime；start/terminal/log 可深链 | 补齐可观测投影与 issue 执行列表 | 真 PostgreSQL + 真服务 + daemon/provider 完成 claim→terminal |
| L436 | pause 两策略、resume/disable/archive/restore/transfer 均有状态与副作用断言 | 复用生命周期服务；pause 调用在途取消器；历史作者返回 inactive 占位 | 浏览器逐个操作并刷新验证数据库状态 |
| L437 | 活跃执行可停止；产物批准/打回；配置 rollback | issue 执行区提供控制入口；复用审批/取消/rollback API | 浏览器执行停止、批准、打回、回滚各一次 |
| L450 | 四种 operational state 与 lifecycle 分离；诊断无泛化错误 | 从心跳安全字段派生 diagnostics，显示能力、任务类型、修复命令 | 构造四态心跳并在桌面/移动、亮/暗验证 |
| L451 | 每个 attempt 均返回 provider/version/model、预算、usage、时间线和审批链 | 扩展安全响应投影和 attempt 审计 UI | 真实 requeue 与审批续跑产生两次 attempt 并逐项核对 |
| L452 | issue_id 过滤不泄露私有项目执行；详情列出全部运行 | 服务端复用 issue 可见性门禁；独立 `IssueExecutionsPanel` | 公共/私有两个项目账号分别请求并浏览 |

## 实施顺序

1. 评论、Runtime、Agent 三个切片各自先补单元/组件失败测试并保存红灯证据；文件边界
   不交叉。主线同时为 issue 执行反查补 API 可见性和组件失败测试。
2. 逐切片实现最小闭环，每次只运行对应测试；通过后补边界、错误态、实时竞态和安全负测。
3. 汇总 i18n（中英键值必须完全同构）、响应 schema、样式与 Spec/README；更新 parity 勾选
   仅限已经有自动断言和真实验收证据的条目。
4. 运行前后端完整质量、类型、lint、单测与逐文件覆盖率，整体和新增代码均不得低于 90%。
5. 使用强随机密码和仅内部网络的 PostgreSQL/Redis 启动真实 API；数据端口不映射宿主。
   运行 REST/WS 双客户端、daemon claim→execution→terminal 和数据库落库断言。
6. 真实浏览器完成桌面/移动 × 亮/暗四组合；保存截图、请求记录和验收矩阵，随后执行
   代码审查与安全审查。
7. MES-187 合入后 rebase 最新 `main`，重跑所有门禁；确认提交身份与无署名行，推送并
   创建 ready PR，不自行合并。

## 关键安全约束

- execution 列表按 issue 过滤前必须验证该 issue 的项目可见性；工作区成员身份不足以读取
  私有项目的执行、结果、日志或审批摘要。
- API/UI 只呈现白名单审计字段；secret、凭证值、宿主路径、原始 prompt、thinking 与未经
  脱敏的 provider 输出均不得返回。
- 高风险动作的 grant 与 result 采用受限结构，不用任意 JSON 展开器。
- 评论失败重试复用首次 idempotency key；HTTP 响应与实时帧按服务端实体 id 收敛。
- 数据服务使用强且唯一密码、保护模式与容器内部网络，不发布 PostgreSQL/Redis 端口。
