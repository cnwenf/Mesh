# MES-96 第 1 轮验收整改 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 闭合 MES-96 第 1 轮验收打回的 P1×2 + P2×4 缺陷（daemon 沙箱并发互杀、server 终态事件漏发、双流水位重叠、result 契约兜底、文档漂移、task token RLS），每项以回归测试钉死，合入 main 且 CI 全绿。

**Architecture:** 三个互不耦合的子系统并行整改：(D) daemon `mesh_runtime`（sandbox.py per-attempt proc 槽位 + logs.py 跨流水位序列化）；(B) backend `mesh.runtime`/`mesh.squad`（`emit_execution_finished()` 终态单一扇出收口 + result schema v1 服务端严格校验 + attempt_task_tokens RLS 迁移）；(W) 文档对齐（runtime-executor/README/CHANGELOG/daemon README/cleanup docstring）。所有修复 TDD：先写失败回归测试，再最小实现。

**Tech Stack:** Python 3.12 / asyncio（daemon）、FastAPI + SQLAlchemy async + Alembic + PostgreSQL 16（backend）、pytest + pytest-cov（≥90% 门禁）。

## Global Constraints

- runtime.md §3.6 写死：`execution.finished`（outbox 内部事件）是**终态单一扇出真源**——执行进入任一终态时状态机在**同一事务**写 outbox，payload `{execution_id, workspace_id, status, failure_reason, finished_at}`，幂等键按 execution_id 守卫。
- runtime-executor.md §2.2 写死：`attempt_task_tokens` 带同租户复合 FK 和 **fail-closed RLS**。
- runtime-executor.md §3.9 写死：终态 result 为 schema v1（decimal-string 金额、非负整数 token/turn、固定 termination 词表），「The server 422s anything else」。
- runtime.md §2.3 / schemas.py L3：daemon 上报 JSONB 一律 `_bounded_json` 限 64 KiB，超限 422。
- 服务端日志 offset 协议：单水位跨 stdout/stderr，wire 字节 = `len(utf8) + 1`（含行尾 `\n`，backend `logs.py::_line_bytes` 为权威）；`start_offset != expected` → 409 `offset_mismatch`。
- runtime-executor.md §5.2 红线：沙箱 fail-closed 永不降级裸跑；rollback 只能杀失败 attempt 自身的进程。
- 单测覆盖率 ≥90%（整体 + 新增代码）；提交身份 cnwenf <cnwenf@outlook.com>；提交无 co-author 署名；不得暴露任何参考来源字样。

---

## Task D1: daemon sandbox — per-attempt proc 槽位，消灭 `_pending_proc` 共享态（P1-1）

**Files:**
- Modify: `daemon/src/mesh_runtime/sandbox.py`（`provision` :142-179、`_reserve_link_addresses` :215-233、`_spawn_and_verify` :235-297、`_rollback` :446-470）
- Test: `daemon/tests/unit/test_sandbox.py`（新增 `TestConcurrentRollback`）

**Interfaces:**
- Consumes: 既有 `SandboxSpec` / `SandboxHandle` / `SandboxUnavailableError`
- Produces: `_spawn_and_verify(..., created: dict)` 在 spawn 成功后写 `created["proc"] = proc`；`_rollback(spec, created)` 只杀 `created.get("proc")`；`_reserve_link_addresses` 的 `ip` 探测在 worker 线程执行

**根因**：`RuntimeApp` 全局共享一个 `SandboxManager`，`_spawn_and_verify` 写实例级 `self._pending_proc = proc`（:288），任一 attempt 握手失败 `_rollback`（:447）不校验 attempt 身份直接 kill 当前指向进程 → A 失败杀死 B 的沙箱 → 连环双失败。`created["proc"]` 槽位（:151）声明但从未赋值。连带：`_reserve_link_addresses`（:225-231）与 `_rollback`（:456）同步 `subprocess.run(["ip", …])` 阻塞事件循环，拉宽握手超时窗口。

- [ ] **Step 1: 写失败回归测试**（`daemon/tests/unit/test_sandbox.py` 追加；真实沙箱，沿用既有 `manager` fixture 与 `spec` 构造方式）

```python
class TestConcurrentRollback:
    async def test_failed_attempt_rollback_kills_only_its_own_sandbox(
        self, manager, tmp_path
    ):
        """MES-96 P1-1：并发两 attempt，向 A 注入握手失败，
        B 必须正常 SANDBOX_READY 并完成——rollback 不得触碰 B 的进程。"""
        import asyncio
        from mesh_runtime.sandbox import SandboxUnavailableError

        spec_a = _sleep_spec(tmp_path / "a", "attempt-a-concurrent")
        spec_b = _sleep_spec(tmp_path / "b", "attempt-b-concurrent")
        real_handshake = manager._handshake

        async def flaky_handshake(spec, **kw):
            if spec.attempt_id == "attempt-a-concurrent":
                await asyncio.sleep(0.05)  # 让 B 先完成 spawn 写入自己的槽位
                raise SandboxUnavailableError("injected handshake failure")
            return await real_handshake(spec, **kw)

        manager._handshake = flaky_handshake
        results = await asyncio.gather(
            manager.provision(spec_a), manager.provision(spec_b),
            return_exceptions=True,
        )
        assert isinstance(results[0], SandboxUnavailableError)
        handle_b = results[1]
        assert not isinstance(handle_b, BaseException)
        # B 的沙箱进程未被 A 的 rollback 误杀
        assert handle_b.proc.returncode is None
        # A 自己的进程已被回收（不留孤儿）
        procs_after = [p for p in manager._handles.values()]
        assert all(p.attempt_id != "attempt-a-concurrent" for p in procs_after)
        await manager.destroy(handle_b)
```

（`_sleep_spec(root, attempt_id)` 按测试文件内既有 spec 工厂构造：uid=65534、argv=`("sleep","2")`、合理限额。若文件已有等价 helper 直接复用。）

- [ ] **Step 2: 运行测试确认失败**
Run: `cd daemon && python -m pytest tests/unit/test_sandbox.py::TestConcurrentRollback -v`
Expected: FAIL（B 的进程被 A 的 rollback 杀死 → `proc.returncode is not None`，或 B 握手超时抛 `SandboxUnavailableError`）

- [ ] **Step 3: 最小实现**（sandbox.py）

```python
# provision(): 把 created 传给 _spawn_and_verify
            handle = await self._spawn_and_verify(
                spec, created,
                cgroup_path=cgroup_path,
                host_ip=host_ip, sandbox_ip=sandbox_ip,
                veth_host=veth_host, veth_peer=veth_peer,
            )

# _spawn_and_verify(): 签名加 created: dict；spawn 成功后写槽位，删除 self._pending_proc
        created["proc"] = proc  # per-attempt slot — rollback kills ONLY this proc

# _reserve_link_addresses(): 探测循环整体入线程
    async def _reserve_link_addresses(self, attempt_id: str) -> tuple[str, str, str, str]:
        return await asyncio.to_thread(self._probe_free_link, attempt_id)

    def _probe_free_link(self, attempt_id: str) -> tuple[str, str, str, str]:
        # 原 for salt in range(16) 循环原样搬入（subprocess.run 探测现在在 worker 线程）
        ...

# _rollback(): 只杀本 attempt 的进程；ip 调用入线程
    async def _rollback(self, spec: SandboxSpec, created: dict) -> None:
        proc = created.get("proc")
        if proc is not None and proc.returncode is None:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                pass
        created["proc"] = None
        if created.get("veth"):
            veth = created["veth"]
            await asyncio.to_thread(
                subprocess.run, ["ip", "link", "del", veth], capture_output=True
            )
        ...  # cgroup/root 清理保持原样
```

删除 `self._pending_proc = proc`（:288）及 `_rollback` 中对 `getattr(self, "_pending_proc", None)` 的读取；`__init__` 不再需要该属性。

- [ ] **Step 4: 全量沙箱回归**
Run: `cd daemon && python -m pytest tests/unit/test_sandbox.py -v`
Expected: PASS（含既有 `TestProvision` / `TestFailClosed` / `two_concurrent_sandboxes_isolated` 与新增 `TestConcurrentRollback`）

- [ ] **Step 5: 真实 churn 压测取证**（root + 真实沙箱；issue 附件复现脚本）
Run: `python sandbox-churn-repro.py 24 0.08`（脚本在 workdir 根；先于修复前基线复现、修复后必须 `SUMMARY: failures=0`，连跑 3 轮）
Expected: 3/3 轮 `failures=0`

- [ ] **Step 6: Commit**
`git commit -m "fix(daemon): MES-96 P1-1——sandbox rollback 收敛到失败 attempt 自身进程（created[\"proc\"] 槽位 + ip 调用异步入线程）"`

---

## Task D2: daemon logs — 跨流单水位序列化，消灭 offset 重叠静默丢行（P2-1）

**Files:**
- Modify: `daemon/src/mesh_runtime/logs.py`（`_collect_batches` :210-246、`_flush_stream` :172-208、`_lines_after_bytes` :289-303）
- Test: `daemon/tests/unit/test_logs.py`（fake ack 对齐服务端 wire 格式 + 新增跨流回归）

**Interfaces:**
- Consumes: `LogSpool.pending(attempt_id, stream)` / `has_pending` / `ack` / `write`
- Produces: `_collect_batches` 返回 `list[tuple[str, int, list[str]]]`（**(stream, start_offset, lines)**——先按 offset 升序回放**双流**未 ack spool 批次，再追加本流缓冲批次）；模块级 `_line_bytes(line) = len(utf8) + 1`（与服务端 `backend/src/mesh/runtime/logs.py::_line_bytes` 逐字一致）

**根因**：`_collect_batches` 计算 `next_offset` 只看**本流** pending（:225-228）。一流瞬态失败后其 spool 批次占据 `[100,150)`，另一流新批次从同一 offset 起 → 上传覆盖 → 失败流重放时 409 `offset_mismatch`，对账 `_lines_after_bytes` 把「别的流的内容」当已确认前缀丢弃 → 静默丢行。连带：daemon 自算批次尾偏移不含行尾 `\n`，而服务端 wire 字节含 `+1/行`——重试路径上 daemon 自算值比服务端 ack 短 N 字节，同样触发对账丢行。

- [ ] **Step 1: 写失败回归测试**（`test_logs.py`；先更新 `FakeApi.append_logs` 的 ack 计算为服务端契约：`end = start_offset + sum(len(l.encode()) + 1 for l in lines)`，并同步修正文件内因此变化的既有 offset 断言——这是把 fake 对齐真实服务端，不是迁就实现）

```python
@pytest.mark.asyncio
async def test_other_stream_pending_blocks_overlapping_offsets(journal, ctx, tmp_path):
    """MES-96 P2-1：stdout 批次瞬态失败进 spool 后，stderr 新批次不得从被占
    offset 起；重放必须按 offset 序跨流串行化，全程零丢行、零 offset_mismatch。"""
    spool = LogSpool(tmp_path / "spool", max_bytes=4096)
    api = FailOnceApi(fail_stream="stdout")     # 首个 stdout 批次抛 DaemonError
    up = uploader(api, journal, clock=FakeClock(), spool=spool,
                  batch_lines=100, batch_bytes=10_000, batch_interval=10)
    await up.submit(ctx, "stdout", "out-1")      # flush 失败 → spooled [0, 6)
    api.fail_stream = None                       # 之后恢复正常
    await up.submit(ctx, "stderr", "err-1")      # 触发 stderr flush
    # 断言：无任何一次上传的 [start, end) 与已接受区间重叠；两流行全部落库
    assert api.received_lines() == {"stdout": ["out-1"], "stderr": ["err-1"]}
    assert api.no_overlap()
    entry = await journal.get(ctx.attempt_id)
    assert entry.log_offset_stdout == entry.log_offset_stderr == api.watermark
```

（`FailOnceApi` / `received_lines` / `no_overlap` 为测试内工具：记录每次成功上传的 `(stream, start, end, lines)`，校验区间不重叠且行内容完整。按测试文件既有 fake 风格实现。）

- [ ] **Step 2: 运行确认失败**
Run: `cd daemon && python -m pytest tests/unit/test_logs.py -k "other_stream_pending or offset" -v`
Expected: FAIL（重叠上传 / 丢行 / offset_mismatch 计数 > 0）

- [ ] **Step 3: 最小实现**（logs.py）

```python
def _line_bytes(line: str) -> int:
    """Wire bytes per line — MUST mirror server logs.py::_line_bytes
    (UTF-8 bytes + trailing newline), or retry-path self-computed offsets
    drift behind the server watermark and reconciliation drops lines."""
    return len(line.encode("utf-8")) + 1


def _batch_wire_end(batch: SpooledBatch) -> int:
    return batch.start_offset + sum(_line_bytes(l) for l in batch.lines)
```

`_collect_batches` 重写为（签名返回三元组；回放**双流** pending 按 offset 升序，`next_offset` 取双流 pending 尾偏移最大值与 journal start 的最大值）：

```python
    def _collect_batches(self, ctx, stream, key, start):
        batches: list[tuple[str, int, list[str]]] = []
        next_offset = start
        if self._spool is not None:
            pending: list[SpooledBatch] = []
            for s in ("stdout", "stderr"):
                if self._spool.has_pending(ctx.attempt_id, s):
                    pending.extend(self._spool.pending(ctx.attempt_id, s))
            pending.sort(key=lambda b: b.start_offset)
            for batch in pending:
                batches.append((batch.stream, batch.start_offset, list(batch.lines)))
                next_offset = max(next_offset, _batch_wire_end(batch))
        lines = self._buffers.pop(key, [])
        self._buffer_bytes.pop(key, None)
        self._first_at.pop(key, None)
        if lines:
            if self._spool is not None:
                try:
                    self._spool.write(SpooledBatch(ctx.attempt_id, stream, next_offset, tuple(lines)))
                except DaemonError:
                    self._rebuffer_sync(key, lines)
                    raise
            batches.append((stream, next_offset, lines))
        return batches
```

`_flush_stream` 相应改为解包三元组、按各批次自身 stream 上传 / ack / rebuffer；`sealed` 只落在**本流**最后一个批次（batches 中最后一个属于 `stream` 的项，且其后无本流批次）。`_lines_after_bytes` 的 `end = running + _line_bytes(line)`。

- [ ] **Step 4: 全量日志回归**
Run: `cd daemon && python -m pytest tests/unit/test_logs.py -v`
Expected: PASS（含对账重放、spool 持久化、sealed 封口既有测试；fake 对齐后断言同步更新）

- [ ] **Step 5: Commit**
`git commit -m "fix(daemon): MES-96 P2-1——日志单水位跨流串行化（next_offset 取双流 pending 最大值 + wire 字节对齐服务端 +1/行）"`

---

## Task B1: server — `emit_execution_finished()` 终态单一扇出收口（P1-2）

**Files:**
- Modify: `backend/src/mesh/runtime/attempts.py`（抽 helper + `_sync_execution_status` :199-221 改用 + `cancel_execution` :510-538、`cancel_in_flight_for_agent` :637-650）
- Modify: `backend/src/mesh/runtime/reaper.py`（`_reclaim_one` :202-215 与 :253-266、`_expire_approvals` :379-394；import 补 helper）
- Modify: `backend/src/mesh/runtime/approvals.py`（`decide_approval` reject :271-286）
- Modify: `backend/src/mesh/squad/tasks.py`（`_cancel_execution` queued→cancelled :398-409）
- Test: `backend/tests/unit/` 对应 runtime/reaper/approvals/squad 测试文件（按既有文件归属追加）

**Interfaces:**
- Produces: `attempts.emit_execution_finished(session, *, execution: TaskExecution) -> None`——同事务写 outbox `execution.finished`，payload `{execution_id, workspace_id, status, failure_reason, finished_at}`（runtime.md §3.6 写死的五字段），幂等键 `execution:{id}:finished`（与既有键一致 → outbox 天然去重）

**根因**：唯一 emit 点 `attempts.py:213` 仅经 daemon PATCH（`_sync_execution_status`）可达；reaper 三处、console cancel 两处、supersede queued、approval 拒绝、squad 级联取消共八处终态只发 realtime/notification。squad relay / result_sink 只订阅 `execution.finished` 且无补偿 sweep → 子任务永挂 in_progress、root 永不聚合。

- [ ] **Step 1: 写失败回归测试**（每条漏发路径一条断言：事务提交后 outbox 存在 `event_type='execution.finished'` 行；至少覆盖：① console cancel queued 执行；② supersede `cancel_in_flight_for_agent` queued；③ reaper max_retries 失败；④ reaper cancelling→cancelled（daemon 死于 cancel 中途）；⑤ approval 拒绝 → cancelled(approval_rejected)；⑥ reaper approval 过期 → cancelled(approval_expired)；⑦ squad `cascade_cancel_task` 取消 queued 子执行；另加一条「挂死场景」：queued 子执行被 console cancel 后经 outbox relay 处理器 `squad_execution_finished_handler` 驱动子任务 failed、root 得以聚合）
Run: `pytest backend/tests/unit/<对应文件> -k finished_fanout -v`
Expected: FAIL（outbox 无该行 / 子任务永挂 in_progress）

- [ ] **Step 2: 实现 helper 并收口八处**

```python
# attempts.py
async def emit_execution_finished(
    session: AsyncSession, *, execution: TaskExecution
) -> None:
    """runtime.md §3.6：终态单一扇出真源。执行进入任一终态的事务内必写本事件；
    幂等键按 execution 守卫，多路径重复写由 outbox 去重。"""
    if execution.status not in EXECUTION_TERMINAL_STATUSES:
        return
    await emit_event(
        session,
        workspace_id=execution.workspace_id,
        event_type="execution.finished",
        payload={
            "execution_id": str(execution.id),
            "workspace_id": str(execution.workspace_id),
            "status": execution.status,
            "failure_reason": execution.failure_reason,
            "finished_at": (
                execution.finished_at.isoformat() if execution.finished_at else None
            ),
        },
        idempotency_key=f"execution:{execution.id}:finished",
    )
```

`_sync_execution_status` 内原 inline `emit_event(..., event_type="execution.finished", ...)` 替换为 `await emit_execution_finished(session, execution=execution)`（payload 因此补齐 `workspace_id`/`finished_at` 两字段——消费方 relay/result_sink 按 execution_id 回查行，纯增量安全）。其余八处在各自 `emit_realtime(...)` 之后、同事务内追加 `await emit_execution_finished(session, execution=execution)`。reaper 顶部 import：`from mesh.runtime.attempts import _emit_terminal_notification, _release_capacity, emit_execution_finished`。

- [ ] **Step 3: 全量回归**
Run: `pytest backend/tests/unit -k "runtime or reaper or approval or squad" `
Expected: PASS（含新增 8+1 条）

- [ ] **Step 4: Commit**
`git commit -m "fix(server): MES-96 P1-2——execution.finished 终态单一扇出收口（reaper/console cancel/supersede/approval/squad 级联八处同事务补发 + payload 补齐五字段）"`

---

## Task B2: server — result schema v1 严格校验 + 尺寸门禁（P2-2）

**Files:**
- Modify: `backend/src/mesh/runtime/schemas.py`（`AttemptTransitionRequest.result` :110 加 `_bounded_json` 校验）
- Create: `backend/src/mesh/runtime/result_schema.py`（服务端 v1 校验镜：词表/计数/decimal）
- Modify: `backend/src/mesh/runtime/attempts.py`（`transition_attempt` 终态分支先校验；`_extract_structured_result` 合规才盖戳；`cost_usd` 改 `Decimal` 解析）
- Test: backend runtime attempts/schema 测试文件追加

**Interfaces:**
- Produces: `result_schema.validate_result_schema(result: dict) -> None`——违规抛 `BusinessRuleError(code="invalid_result_schema", details={"field": msg})`（→422）；规则与 daemon `result.py::build_result` 逐条镜像：`schema_version==1`（真 int 非 bool）、provider.name/version/model str、session_id str|None、usage 六个计数非负整数（bool 冒充拒绝）、`total_tokens == 四项之和`、`cost_usd` 为可解析有限 decimal string（`"nan"`/`"inf"`/float 一律拒）、outcome.exit_code int、termination ∈ {completed, failed, timeout, cancelled, budget_exceeded, sandbox_violation, lease_lost}、summary str

- [ ] **Step 1: 写失败测试**：① result >64KiB → 422；② `cost_usd:"nan"` → 422（修复前为 500 / reaper lease_expired 误判路径）；③ bool 冒充 token → 422；④ total_tokens 不一致 → 422；⑤ 未知 termination → 422；⑥ 缺 schema_version → 422；⑦ 合法 result → 200 + `result_schema_version=1` 盖戳 + `cost_usd` 以 Decimal 精确入库
Run: `pytest backend/tests/unit -k result_schema -v`
Expected: FAIL

- [ ] **Step 2: 实现**
`schemas.py`：

```python
class AttemptTransitionRequest(BaseModel):
    ...
    @field_validator("result")
    @classmethod
    def _bound_result(cls, v):
        return _bounded_json(v, "result")
```

`result_schema.py` 新建（TERMINATIONS / `_is_count` / `_is_decimal_string` 镜像 daemon `result.py`，`validate_result_schema` 逐字段校验，首个违规即抛 BusinessRuleError）。`attempts.py::transition_attempt` 终态分支：redaction 之后、持久化之前 `if result is not None: validate_result_schema(result)`；`_extract_structured_result` 内 `attempt.cost_usd = Decimal(str(cost))`（校验已保证可解析；import Decimal）；盖戳仅在校验通过后发生（调用顺序保证）。

- [ ] **Step 3: 全量回归 + 覆盖率**
Run: `pytest backend/tests/unit -k "attempt or result" -v`
Expected: PASS

- [ ] **Step 4: Commit**
`git commit -m "fix(server): MES-96 P2-2——result 契约服务端兜底（64KiB 422 + schema v1 严格校验 + cost Decimal 解析 + 合规才盖戳）"`

---

## Task B3: server — attempt_task_tokens fail-closed RLS（P2-4 / 安全 MEDIUM-1）

**Files:**
- Create: `backend/migrations/versions/0034_attempt_task_tokens_rls.py`
- Test: 按既有 RLS 测试模式追加（先查 `backend/tests` 内现存 RLS/跨租户断言的落点与连接角色处理，沿用其模式）

**Interfaces:**
- Produces: 迁移 upgrade：`ALTER TABLE attempt_task_tokens ENABLE ROW LEVEL SECURITY` + `CREATE POLICY mesh_attempt_task_tokens_tenant ON attempt_task_tokens USING (workspace_id = current_setting('mesh.workspace_id')::uuid)`（与 0004/0008/0009/0021 逐字同模式）；downgrade 逆操作

- [ ] **Step 1: 写失败测试**：租户 A 上下文查询看不到租户 B 的 token 行（fail-closed：未 set 上下文 → 0 行 / 报错，依既有 RLS 测试惯例）
Run: `pytest backend/tests -k task_token_rls -v`
Expected: FAIL（迁移前 RLS 未启用，跨租户可见）

- [ ] **Step 2: 写迁移 0034**（`revision="0034"`，`down_revision="0033"`）
- [ ] **Step 3: 迁移往返 + 全量回归**
Run: `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`，`pytest backend/tests -k "task_token or rls"`
Expected: PASS

- [ ] **Step 4: Commit**
`git commit -m "fix(server): MES-96 P2-4——attempt_task_tokens 启用 fail-closed RLS（迁移 0034，对齐 runtime-executor.md §2.2）"`

---

## Task W: 文档漂移逐项对齐（P2-3，6 小项）

**Files:** `docs/specs/features/runtime-executor.md`、`README.md`、`daemon/README.md`、`CHANGELOG.md`、`daemon/src/mesh_runtime/cleanup.py`

- [ ] **(a)** `runtime-executor.md:3` 「安全复评通过、开发放行」→「评审通过、开发放行」；`README.md:33` 「安全评审已通过、开发放行」→「评审通过、开发放行」——三处措辞统一为「评审通过、开发放行」。
- [ ] **(b)** `runtime-executor.md` §5.5 开发放行条件：7 项全部 `- [x]` 勾选并各注证据（S-01～S-13 验证/门禁、MES-98 server P0 冻结、daemon 合同测试、ISO-01～14 矩阵、MES-95 受保护真实 e2e、MES-97 最终安全复测通过），与文首状态行「已闭环/放行」一致。
- [ ] **(c)** `daemon/README.md:178` 数字（45,408 tokens / 0.188556 USD）对齐 `docs/evidence/mes-95/real-llm-squad-e2e.json` 实际入库证据（读 JSON 取真值替换，含 tokens/USD/执行数/session 数口径一致）。
- [ ] **(d)** `CHANGELOG.md`：重复 `[0.20.0]` 标题（:127 与 :188）清理——核对两块内容归属合并/改名，恢复严格降序；A3 版本映射与 v0.22.0 发行标题矛盾处对齐（A3 落地版本以 v0.22.0 标题为准）。
- [ ] **(e)** `README.md` 模块表补两行：`daemon/mesh-runtime`（A1/A2/A3 状态 + 证据指引）与 **MES-98**（server P0 契约）具名行。
- [ ] **(f)** `cleanup.py:13` docstring「a cleanup failure isolates the runtime」与实现不符——对齐 §4.4.1 台账语义（cleanup 失败记 journal cleanup bit、经对账上报，而非直接隔离 runtime），措辞改为与实现逐条一致。
- [ ] **验收**：`grep -rn "安全复评通过\|安全评审已通过" README.md docs/specs/features/runtime-executor.md` → 0 命中；CHANGELOG `grep -n "^## \[" CHANGELOG.md | sort -t. -k2 -u` 无重复版本号；daemon README 数字与 evidence JSON 逐字一致。
- [ ] **Commit**：`git commit -m "docs: MES-96 P2-3——验收文档漂移六项对齐（措辞统一/§5.5 勾选/evidence 数字/CHANGELOG 去重/模块表补行/cleanup docstring）"`

---

## Task I: 集成、全量门禁与发版前置（整合者执行）

- [ ] 合并三支线至 `agent/mesh/83cdf5b9`，全量 `daemon` pytest（含 isolation 矩阵，root 真实沙箱）+ `backend` pytest（独立 PG16/Redis/MinIO 栈，强随机口令、仅 loopback）双绿。
- [ ] 覆盖率实测：daemon `--cov=mesh_runtime` ≥90%、backend `--cov=mesh --cov-fail-under=90`，新增代码覆盖率单独核算 ≥90%。
- [ ] churn 复现脚本修复后 3 轮 `failures=0`；命脉并发稳定性自测（fake provider e2e 多轮）。
- [ ] code-reviewer + security-reviewer 双镜复核（重点：并发回滚、outbox 同事务性、RLS fail-closed、422 边界）。
- [ ] PR → merge main → `gh run watch` 阻塞至 backend-ci / daemon-ci / spec-checks 全绿 → issue 回复 @Mesh 验收员 复验。
