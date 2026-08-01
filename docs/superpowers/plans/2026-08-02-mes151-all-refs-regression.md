# MES-151 全 refs 提交历史回归修复计划

> 基线：远端 `main` 的 `94ff8a38`；分支：`agent/mesh/mes-151-all-refs`。
> 方法：writing-plans、test-driven-development、systematic-debugging、
> verification-before-completion、requesting-code-review。

## 问题与调试假设

扫描器声称覆盖完整提交历史，但实际执行的 `git log` 没有选择所有 refs，只能看到当前
`HEAD` 可达提交。`git for-each-ref` 仅提供 ref 名称，不能补齐其他 ref 独有提交的
message/author。因此，安全主分支之外的独有违规提交会产生确定性 false negative。

## 实施顺序

1. **TDD RED**：在临时仓库创建安全 `main`，从它创建另一分支并提交仅该分支可达的
   synthetic marker，随后切回 `main`。新增回归断言：扫描结果包含 `<git-log>` 且 CLI
   返回非零；实现不改，先运行定向测试证明当前行为失败。
2. **systematic-debugging**：用 `git rev-list HEAD` 与 `git rev-list --all` 对比拓扑，确认
   漏检来自 revision selection，而不是规则加载、文本解码或 ref 名称扫描。
3. **TDD GREEN**：最小修改为 `git log --all --format=...`，不改变脱敏格式、受管文件扫描
   或 fail-closed 语义；同一回归测试转绿。
4. **verification-before-completion**：运行扫描模块定向测试与 branch coverage、backend
   ruff/format、仓库外规则来源审计、后端完整 CI；核对 diff、提交身份、无 co-author。
5. **requesting-code-review**：复核 alternate ref 确实不在 `HEAD` 祖先链中，测试同时覆盖
   Python API 与 CLI 退出码，且 `--all` 与 workflow 的完整 fetch 配置一致。PR checks 全绿
   后合并到 `main`，再核对主干检查。

## 过程证据

- **writing-plans**：本文件先于回归测试和生产实现创建，固定了 RED、命令语义定位、最小
  GREEN、完整验证和 review/发布顺序。
- **TDD RED**：实现不变时运行 alternate-ref 定向测试，得到 1 failed；观测值为
  `sources=set()`、CLI `rc=0`、`status=passed`，与验收复现一致。
- **systematic-debugging**：同一临时仓库中，`git rev-list HEAD` 只有安全主分支提交，
  `git rev-list --all` 多出 alternate ref 独有提交；`git for-each-ref` 仅返回 ref 名称和对象
  ID。由此排除规则加载与解码问题，把根因收敛到 `git log` 的 revision selection。
- **TDD GREEN**：只给 `git log` 增加 `--all` 后，同一定向测试 1/1 通过；扫描模块完整
  13/13 通过，97 statements / 28 branches 均 100%，高于 90% 门禁。
- **verification-before-completion（阶段结果）**：backend 全量 ruff 通过，两个改动 Python
  文件的 ruff format check 通过，`git diff --check` 通过。以仓库外 8 条受控规则扫描 1,788
  个受管文本、全部 refs 的 commit metadata 与 ref 名称，结果 `passed`、零 violations。
  完整 CI、提交身份与主干结果待提交前后用新鲜输出追加。
- **requesting-code-review（代码级结论）**：最终 staged diff 已逐项复核，无未解决 finding。
  `--all` 明确把所有本地 refs 纳入 revision set，workflow 仍以 `fetch-depth: 0` 提供完整历史；
  回归测试证明 marker 不在 `HEAD` 日志、只在 `--all` 日志，并同时断言 Python API 来源、
  CLI 非零退出和输出脱敏。CI 通过仍是合并条件，不沿用上轮已撤回的通过结论。
