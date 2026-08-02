# MES-152 验收返修计划

> 基线：PR #111 的 `fc922456`，远端 `main` 为 `6607572b`。
> 方法：writing-plans、test-driven-development、systematic-debugging、
> verification-before-completion、requesting-code-review。

## 1. 验收问题与根因假设

1. `aaef47e5` 在 relay 修复中同时修改了 realtime flood E2E：删除每次发送后的
   `asyncio.sleep(0)`，并把 `25 <= pongs <= 40` 放宽为 `pongs < 200`。该变化超出
   MES-152 的 src 竞态修复范围，也不再证明约 30 帧/窗口的安全契约。
2. `305ca42` 为旧 0036 数据库增加了 `mesh_member_search_name(UUID)` 的 helper
   撤权，但现有真实迁移测试只重建 ledger ACL，没有重建 helper 的 PUBLIC 与
   `mesh_app` EXECUTE 权限，因此旧安装升级路径没有回归覆盖。
3. 上一轮缺少五项方法的仓库内可复核时序。本次计划先于任何返修代码建立，后续只追加
   实际 RED、GREEN、调试、完工验证与 review 结果。

## 2. 实施步骤（writing-plans）

### Task A：系统化定位与最小边界

- 比较 `origin/main...fc922456`、`aaef47e5` 和 `305ca42` 的目标文件 diff。
- 将 realtime E2E 精确恢复为当前 `origin/main` 的安全契约，不改 gateway src，也不把
  MES-149 的时序重构带入本返修。
- 保留 0037 helper 撤权：该修复消除旧安装的 SECURITY DEFINER 直接调用面；以真实迁移
  回归补齐安全证明，而不是删除防护。

### Task B：真实 PostgreSQL TDD RED

- 扩展 `test_0037_backfills_legacy_0036_search_contract`：在升级停留于 0036 时，显式把
  `mesh_member_search_name(UUID)` EXECUTE 重新授予 PUBLIC 与 `mesh_app`。
- 升级 0037 后分别断言 PUBLIC ACL 与 `mesh_app` 的有效 EXECUTE 均已消失，并以
  `mesh_app` 真实写入触发器路径验证 search projection 仍可工作；迁移 owner 的降级路径
  继续验证可用。
- 在隔离 worktree 中把“仅测试补丁”应用到 `305ca42^`，对独立临时 PostgreSQL 数据库运行
  该用例。预期 RED：旧实现不会撤销 helper EXECUTE，权限断言失败。

### Task C：最小 GREEN 与安全 E2E 恢复

- 在当前分支运行同一个真实迁移用例，确认 `305ca42` 的撤权实现使其 GREEN。
- 恢复 flood 发送循环的调度点、原注释和 `25 <= pongs <= 40` 断言；运行真实 socket E2E，
  重复执行以排除一次性侥幸。
- 对目标 Python 文件运行仓库 CI 使用的 Ruff lint，执行 `git diff --check`。

### Task D：verification-before-completion

- 运行 relay/outbound/config/database-cleanup、search migration/projection 和 realtime gateway
  定向测试；新增迁移路径必须使用真实 PostgreSQL。
- 按 Issue 门禁在最终 head 连续三轮运行 backend 全量单测与真实 E2E，并实测总覆盖率
  ≥90%；任何一轮失败都先定位并修复，再重新从第一轮计数。
- 核对 PR diff 只包含本次必要返修、提交身份为 `cnwenf <cnwenf@outlook.com>`、无任何
  co-author 行，且受管文本/提交信息/refs 来源门禁通过。

### Task E：requesting-code-review 与交付

- 由独立 reviewer 对 `fc922456..最终 head` 做代码审查，重点检查安全断言是否完整恢复、
  ACL 测试是否真实复现 0036 旧态、PUBLIC 与 app role 是否分别断言、触发器/owner 路径是否
  仍可用，以及返修是否引入范围外变化。
- 处理全部 finding 后提交并推送 `agent/mesh/d609122f`，等待 PR #111 可见 checks 全绿。
- 在 MES-152 的验收线程回复最终 head、RED→GREEN、连续三轮、覆盖率、review 与 CI 证据；
  默认不使用 agent mention，避免回复链重复触发。

## 3. 过程证据

- **writing-plans**：本文件在返修代码和测试改动前建立；后续各项只记录实际执行结果。
- **systematic-debugging（初始定位）**：`git diff origin/main --
  backend/tests/e2e/test_realtime_gateway_e2e.py` 精确显示 `aaef47e5` 删除调度点并把
  `25 <= pongs <= 40` 改为 `pongs < 200`；`git show 305ca42` 显示该提交只修改 0037
  migration 的 helper 撤权，测试未同步变化。
- **TDD RED**：先完成真实升级测试，再把仅测试补丁复制到 `305ca42^`（`aaef47e5`）的
  隔离 worktree；对独占 PostgreSQL 16 运行
  `pytest -q tests/unit/test_search_migration.py::test_0037_backfills_legacy_0036_search_contract`
  得到 1 failed，精确失败为升级后 `unauthorized_helper_acl_count` 实得 2、期望 0。
  这证明旧实现原样保留了 PUBLIC 与 `mesh_app` 两条 EXECUTE ACL。
- **TDD GREEN**：轮换隔离数据库凭据后，在当前分支以无密码 DSN + 进程环境注入运行同一
  真实 PostgreSQL 用例，1/1 通过。用例同时证明升级前 ACL count 为 2、升级后为 0、
  migration owner 可直接调用 helper、无 tenant GUC 的 `mesh_app` 用户更新能通过触发器更新
  projection，而 `mesh_app` 直接调用 helper 得到精确 SQLSTATE `42501`；0037→0034 owner
  降级路径仍成功。
- **systematic-debugging（验证环境）**：全量套件最初暴露的失败均来自测试容器环境，而非
  产品行为：schema validation 需要可由 `psql` 解析的 PostgreSQL URL，data-job 同时读取
  `MESH_TEST_STORAGE_*` 与运行时 `MESH_STORAGE_*`，source-provenance 用例需要容器内存在
  `git`。逐项复现后，为测试工具容器补齐 PostgreSQL client、Git 与两组 MinIO endpoint；
  保持 PostgreSQL/Redis/MinIO 仅绑定隔离 Docker 内网且无宿主端口，再从零开始计数最终
  三轮。测试日志中一度显示的随机临时数据库凭据已立即通过销毁并重建数据库容器完成轮换，
  未进入仓库、提交或交付内容。
- **verification-before-completion**：
  - 仓库 CI 使用的 `ruff check` 对 migration、migration test 与 realtime E2E 目标文件通过；
    `git diff --check` 通过。临时工具链的额外 `ruff format --check` 会要求重排必须与
    `origin/main` 精确一致的 E2E 和 0037 既有主体，而仓库 workflow 没有该门禁，因此未做
    范围外格式化。
  - 真实 socket flood E2E 在恢复 `await asyncio.sleep(0)` 与 `25 <= pongs <= 40` 后连续
    5/5 通过；该文件与 `origin/main` 精确一致。
  - relay/outbound/config/database-cleanup、search migration/projection、gateway session 与
    realtime gateway 定向集合 125/125 通过（180.33s）。
  - 未改动工作树上，以独立数据库连续三轮运行完整 backend 测试、真实 E2E 与覆盖率门禁：
    第 1 轮 4269 passed，91.64%，1373.93s；第 2 轮 4269 passed，91.62%，1462.62s；
    第 3 轮 4269 passed，91.64%，1457.57s。三轮退出码均为 0，均超过 90% 门槛。
- **requesting-code-review**：独立只读 reviewer 对本次三文件返修做了专项审查，结论为
  code-level PASS、无 blocking finding。其复核了 realtime E2E 与 `origin/main` 精确一致；
  0036 旧态确实同时重建 PUBLIC/`mesh_app` ACL；升级后 ACL count 为 0；owner helper、
  `mesh_app` 触发器路径仍可用；`mesh_app` 直接 helper 调用独立以 SQLSTATE `42501` 失败；
  未发现假阳性或范围外产品改动。
- **技能加载说明**：返修开始时已请求并获确认安装 Superpowers 插件，但当前活动运行没有
  热加载出其 `SKILL.md` 资源。因此本次按 writing-plans、test-driven-development、
  systematic-debugging、verification-before-completion、requesting-code-review 五类合同逐项
  实际执行并在本文保留真实时序，且使用独立 reviewer 完成审查；不把未发生的 SKILL.md
  调用记为已发生。
