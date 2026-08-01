# MES-116 验收整改实施计划

日期：2026-08-02 · 负责人：Mesh 程序员 · 基线：PR #117 (`940f8092`)

## 目标

关闭验收反馈的五类缺口：Agent 工具真实读写契约、变更文件逐文件覆盖率门禁、命令面板 E2E 竞态、25 页真实交互与 150 张视觉证据，以及 PR 的正式 issue 关联和评审状态。后端以 `skill_installations.granted_capabilities` 为唯一真源，不新增工具主键或重复授权表。

## TDD 与调试顺序

1. **Agent Tools 契约**
   - 先写 service/route 失败测试，覆盖 GET、POST、PATCH、DELETE、权限枚举、高风险默认、鉴权、审计与持久化结果。
   - 实现最小契约层，对 workspace-scope 安装做 agent-scope 写时复制，避免一个 Agent 的授权变更污染其他 Agent。
   - 先写前端失败测试，断言加载、单项启停、权限修改、新增/删除与错误恢复；再用真实 API 替换只读推导。
   - 启动真实服务，通过 HTTP 验证响应包络和数据库落库。
2. **覆盖率门禁**
   - 将 data-jobs、skills、squads、projects、labels 纳入逐文件检查。
   - 对七个拒收文件逐项补失败测试，每个文件的 statements/branches/functions/lines 均达 90%。
3. **E2E 系统调试**
   - 复现 `Ctrl+K → Settings → Enter` 竞态，以 ARIA active option 稳定为等待条件，连续多次运行原失败用例。
   - 25 个路由每页至少执行一个具有业务语义的真实交互，不以仅到达页面代替功能验收。
   - 固定处理 onboarding，在 desktop/tablet/mobile × light/dark 下产出 150 张可区分证据，执行唯一性与溢出检查。
4. **三趟一致性审核**
   - 模块趟：页面族和设计模式。
   - 旅程趟：创建、管理、错误恢复等跨页链路。
   - 横切趟：响应式、深浅色、可访问性、i18n、敏感数据和可观测性。

## 完成门禁

- 后端、前端、CLI、daemon 全量 UT 通过；整体与所有变更文件四项覆盖率均不低于 90%。
- lint、typecheck、build、contrast、设计系统审计全绿。
- 真实 API E2E、页面交互 E2E、视觉矩阵和证据唯一性门禁全绿。
- 运行 completion verification 后复核 git diff；提交前使用 requesting-code-review 清单做独立变更审查。
- PR 标题/正文正式关联 MES-116，转为 ready for review，远端 CI 全绿后交付。
