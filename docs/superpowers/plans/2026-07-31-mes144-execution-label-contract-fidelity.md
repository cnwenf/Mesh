# MES-144 执行行标签契约保真 —— 规划与排障记录

> 关联 Issue：MES-144（父 MES-124 复验裁定的遗留应改）。修复 PR 叠在最新 `main` 之上，单一语义提交。
> 过程方法：writing-plans（规划）+ systematic-debugging（根因排障）+ test-driven-development + verification-before-completion。

## 1. 症状与影响

- 首页工作台「AI 运行」行标签在真实环境恒退化为无信息兜底文案（`home.aiRunFallback`）；
  运行 / 执行详情页的 agent / issue 行同理恒为 `—` 或整段 id。
- 既有 e2e 看不出该缺陷（mock 契约栈伪造了后端不返回的字段，掩盖了漂移）。

## 2. 根因（systematic-debugging）

1. 读后端真源 `backend/src/mesh/runtime/service.py::_render_execution`：返回字段集为
   `id / workspace_id / agent_id / issue_id / trigger / status / priority / task_spec /
   label_requirements / required_capabilities / config_snapshot / max_attempts / queued_at /
   finished_at / timeout_seconds / failure_reason / result / cancel_requested_at`（详情再含
   `attempts / retry_count / credentials`）。**不含** `agent_name` 与 `issue_identifier`。
2. 读前端消费点：`HomePage` 的 `AiRunsSection` 用 `execution.agent_name ?? execution.issue_identifier ?? …`
   拼标签；`ExecutionDetailPage` / `RuntimeDetailPage` 同样依赖这两字段。类型 `ExecutionSummary`
   还把这两字段声明为可选 —— 类型与后端契约不一致，使漂移在编译期也不报错。
3. 读 mock：`e2e/mock-server.mjs` 的 `GET …/executions` 与 `e2e/fixtures/page-routes.mjs` 的详情夹具
   均填了 `agent_name` / `issue_identifier` —— 与真实响应形状不符，导致 e2e 假绿。
4. 结论：**展示层依赖幽灵字段 + 契约镜像伪造字段**双重作用；修复应只动展示层与镜像，后端不动。

## 3. 计划（writing-plans）

1. 新增纯工具 `frontend/src/features/runtimes/executionLabel.ts`：`executionShortId`（UUID 首段 8 字符）、
   `executionTriggerLabelKey`（已知 trigger → `runtimes.execution.triggerKind.*`，未知落 `.unknown`）、
   `executionDisplayLabel`（`trigger 文案 · 短 ID` 规范形）。先写单测（RED→GREEN）。
2. `HomePage`「AI 运行」行标题改用 `executionDisplayLabel`；元信息补「状态文案 · 相对时间」
   （复用 `i18n` 的 `formatRelativeTime` + `useIntl().locale`）。
3. `ExecutionDetailPage` / `RuntimeDetailPage`：标题 / 行改 trigger+短 ID；详情 agent / issue 行改呈
   契约实际返回的 `agent_id` / `issue_id`。
4. 类型 `ExecutionSummary` 移除 `agent_name?` / `issue_identifier?`、补回 `issue_id`，与后端逐字段对齐。
5. mock 列表与详情夹具删幽灵字段，补齐 `_render_execution` / `_render_attempt` 全字段
   （含凭证 `value:'***'`），杜绝漂移再被掩盖。
6. i18n 增 `runtimes.execution.triggerKind.unknown` 兜底文案，并按各目录最终 messages 重算 `version`。
7. 更新三页单测 + `api.test.ts` 的 typed 夹具；e2e 工作台用例扩标签兜底断言。

## 4. TDD 与验证证据

- 新增 `executionLabel.test.ts`（短 ID / trigger 映射 / 标签组合 / 未知兜底）；先红后绿。
- 三页单测与 `api.test.ts` 夹具同步真实形状，断言改为 trigger+短 ID 与 `agent_id`/`issue_id`。
- 门禁（rebase 后在最新 main 上复跑）：`test:coverage` 全过且 `verify-perfile-coverage` 逐文件 ≥90%；
  `typecheck` / `lint` 0 错；`vite build` 成功；mock 契约 e2e 工作台 24/24（含新增标签断言）；
  真实后端 `test:e2e:runtimes` 全过；真实栈 + 真实浏览器实测首页「AI 运行」行
  `Assign · <短ID> / Running · <相对时间>` 与 `Mention · <短ID> / Awaiting approval · <相对时间>`。

## 5. 合入基线整改（复验打回后）

- 首版 PR 误叠在未合入的批次分支上，致与 `main` 8 文件冲突。整改：以当前 `origin/main` 为唯一基，
  将已安全审核放行的展示层改动 cherry-pick 重落到 main 当前版本（仅 i18n `version` 行需手工对齐并重算），
  收敛为**单一语义提交**，`git diff origin/main` 仅含本 Issue 文件，对 main 为快进合并、零冲突。

## 6. 合规

- 提交 author/committer 均 `cnwenf <cnwenf@outlook.com>`，无 co-author；分支名 / 代码 / 注释 / 文档无参考来源暴露字样。
