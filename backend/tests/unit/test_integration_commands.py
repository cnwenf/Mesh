"""Command plane tests — /stop two-phase cancel, /btw append, authz (§3.7/§5.6).

Covers: multi-person independence, triple-based authorization (bare-key
forbidden), idempotent repeats, passthrough fallthrough, caps, audit.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.config import load_settings
from mesh.db.models.agent import Agent
from mesh.db.models.integration import (
    ExternalIdentity,
    Integration,
    IntegrationBinding,
    IntegrationEvent,
    IntegrationMessageQueue,
)
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.integrations.commands import maybe_handle_command

pytestmark = pytest.mark.unit

CORP = "dingsample"
CONV_REF = "cidCMD=="
CONV_KEY = f"dingtalk:{CORP}:{CONV_REF}"
ALICE_KEY = "staff-alice"
BOB_KEY = "staff-bob"


def _settings(**overrides):
    return load_settings(
        database_url="postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/mesh_test",
        redis_url="redis://127.0.0.1:6390/1",
        **overrides,
    )


async def _seed_world(session_factory, *, role: str = "member"):
    """World with two mapped external users (alice, bob) + integration."""
    ids = {k: uuid.uuid4() for k in ("ws", "user_a", "user_b", "member_a", "member_b", "agent")}
    integration, binding = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Workspace(id=ids["ws"], name="C WS", slug=f"c-{ids['ws'].hex[:10]}"))
        session.add(
            User(id=ids["user_a"], email=f"a-{ids['user_a'].hex[:6]}@mesh.test",
                 display_name="Alice", password_hash="x")
        )
        session.add(
            User(id=ids["user_b"], email=f"b-{ids['user_b'].hex[:6]}@mesh.test",
                 display_name="Bob", password_hash="x")
        )
        await session.flush()
        session.add(
            Member(id=ids["member_a"], workspace_id=ids["ws"], member_type="human",
                   user_id=ids["user_a"], role=role, status="active")
        )
        session.add(
            Member(id=ids["member_b"], workspace_id=ids["ws"], member_type="human",
                   user_id=ids["user_b"], role="member", status="active")
        )
        session.add(
            Agent(id=ids["agent"], workspace_id=ids["ws"], name="C Agent",
                  owner_user_id=ids["user_a"], lifecycle_status="active")
        )
        await session.flush()
        session.add(
            Integration(id=integration, workspace_id=ids["ws"], kind="im_dingtalk",
                        name="dt-c", created_by=ids["member_a"],
                        config={"app_key": "dingxxxx", "corp_id": CORP})
        )
        session.add(
            IntegrationBinding(id=binding, workspace_id=ids["ws"], integration_id=integration,
                               provider="dingtalk", provider_tenant_key=CORP,
                               external_ref=CONV_REF, bound_agent_id=ids["agent"])
        )
        # identity mapping: alice/bob staffIds → global users
        session.add(
            ExternalIdentity(provider="dingtalk", provider_tenant_key=CORP,
                             external_user_key=ALICE_KEY, user_id=ids["user_a"])
        )
        session.add(
            ExternalIdentity(provider="dingtalk", provider_tenant_key=CORP,
                             external_user_key=BOB_KEY, user_id=ids["user_b"])
        )
    return {**ids, "integration": integration, "binding": binding}


async def _event_id(session_factory, world, msg_id: str = "cmd-1", payload=None) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        row = IntegrationEvent(
            workspace_id=world["ws"], integration_id=world["integration"],
            external_event_id=msg_id, event_type="im.message.receive",
            payload=payload if payload is not None else {"text": {"content": "x"}},
            signature_status="valid",
            process_status="received",
        )
        session.add(row)
        await session.flush()
        return row.id


async def _item(
    session_factory, world, *, seq: int, state: str, sender_key: str,
    execution_id: uuid.UUID | None = None, excerpt: str = "task",
) -> uuid.UUID:
    item_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            IntegrationMessageQueue(
                id=item_id, workspace_id=world["ws"],
                integration_id=world["integration"], binding_id=world["binding"],
                conversation_key=CONV_KEY, seq=seq,
                dispatch_mode="serial_conversation", state=state,
                execution_id=execution_id, target_agent_id=world["agent"],
                message_excerpt=excerpt,
                sender_identity_key=f"dingtalk:{CORP}:{sender_key}",
            )
        )
    return item_id


async def _execution(session_factory, world, *, status: str = "running") -> uuid.UUID:
    exec_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            TaskExecution(
                id=exec_id, workspace_id=world["ws"], agent_id=world["agent"],
                trigger="integration", status=status,
                idempotency_key=f"idem-{exec_id.hex[:8]}", task_spec={},
                label_requirements={}, required_capabilities=[], config_snapshot={},
            )
        )
    return exec_id


async def _run(session_factory, world, *, text: str, sender: str, settings=None,
               event_payload=None):
    settings = settings or _settings()
    event_id = await _event_id(session_factory, world, msg_id=f"cmd-{uuid.uuid4().hex[:8]}",
                               payload=event_payload)
    async with session_factory() as session, session.begin():
        integration = await session.get(Integration, world["integration"])
        # event row must be owned by THIS session (production passes the
        # ingest-transaction row; audit mutations attach to it)
        event_row = await session.get(IntegrationEvent, event_id)
        outcome = await maybe_handle_command(
            session,
            settings=settings,
            integration=integration,
            event_row=event_row,
            normalized_text=text,
            provider="dingtalk",
            tenant_key=CORP,
            user_key=sender,
            conversation_key=CONV_KEY,
        )
        return outcome, event_id


async def _feedback_texts(session_factory) -> list[str]:
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "im.send")
                )
            ).scalars().all()
        )
    return [e.payload["text"] for e in events if e.payload.get("kind") == "command_feedback"]


async def _feedback_events(session_factory) -> list[OutboxEvent]:
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "im.send")
                )
            ).scalars().all()
        )
    return [e for e in events if e.payload.get("kind") == "command_feedback"]


async def _load_item(session_factory, item_id):
    async with session_factory() as session:
        return await session.get(IntegrationMessageQueue, item_id)


class TestStop:
    async def test_two_phase_cancel_processing(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world, status="running")
        item_id = await _item(
            session_factory, world, seq=1, state="processing",
            sender_key=ALICE_KEY, execution_id=exec_id, excerpt="deploy prod",
        )
        outcome, _ = await _run(session_factory, world, text="/stop", sender=ALICE_KEY)
        assert outcome.handled is True
        item = await _load_item(session_factory, item_id)
        assert item.state == "cancelling"  # lane still occupied (two-phase)
        async with session_factory() as session:
            execution = await session.get(TaskExecution, exec_id)
        assert execution.status == "cancelling"
        assert execution.cancel_requested_at is not None
        # failure_reason='cancelled_by_command' is set by the daemon's
        # terminal PATCH report (two-phase); at request time the intent is
        # persisted via cancel_requested_at — no premature failure_reason.
        texts = await _feedback_texts(session_factory)
        assert any("正在停止任务" in t and "deploy prod" in t for t in texts)

    async def test_pending_cancelled_immediately(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world)
        await _item(session_factory, world, seq=1, state="processing",
                    sender_key=ALICE_KEY, execution_id=exec_id)
        p1 = await _item(session_factory, world, seq=2, state="pending", sender_key=ALICE_KEY)
        p2 = await _item(session_factory, world, seq=3, state="pending", sender_key=ALICE_KEY)
        await _run(session_factory, world, text="/stop", sender=ALICE_KEY)
        assert (await _load_item(session_factory, p1)).state == "cancelled"
        assert (await _load_item(session_factory, p2)).state == "cancelled"
        texts = await _feedback_texts(session_factory)
        assert any("2" in t and "排队消息" in t for t in texts)

    async def test_multi_person_independent_cancel(self, session_factory):
        """B's /stop cancels B's pending; A's processing untouched."""
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world)
        a_item = await _item(session_factory, world, seq=1, state="processing",
                             sender_key=ALICE_KEY, execution_id=exec_id)
        b_item = await _item(session_factory, world, seq=2, state="pending", sender_key=BOB_KEY)
        await _run(session_factory, world, text="/stop", sender=BOB_KEY)
        assert (await _load_item(session_factory, a_item)).state == "processing"
        assert (await _load_item(session_factory, b_item)).state == "cancelled"
        texts = await _feedback_texts(session_factory)
        assert any("不是你的" in t for t in texts)

    async def test_forbidden_on_others_inflight_only(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world)
        a_item = await _item(session_factory, world, seq=1, state="processing",
                             sender_key=ALICE_KEY, execution_id=exec_id)
        await _run(session_factory, world, text="/stop", sender=BOB_KEY)
        item = await _load_item(session_factory, a_item)
        assert item.state == "processing"  # unaffected, no detail leak
        async with session_factory() as session:
            execution = await session.get(TaskExecution, exec_id)
        assert execution.status == "running"
        texts = await _feedback_texts(session_factory)
        assert any("没有权限" in t for t in texts)

    async def test_admin_manage_can_stop_others(self, session_factory):
        # bob is admin this time
        world = await _seed_world(session_factory, role="member")
        async with session_factory() as session, session.begin():
            from sqlalchemy import update as sql_update

            await session.execute(
                sql_update(Member).where(Member.id == world["member_b"]).values(role="admin")
            )
        exec_id = await _execution(session_factory, world)
        a_item = await _item(session_factory, world, seq=1, state="processing",
                             sender_key=ALICE_KEY, execution_id=exec_id)
        await _run(session_factory, world, text="/stop", sender=BOB_KEY)
        assert (await _load_item(session_factory, a_item)).state == "cancelling"

    async def test_unmapped_identity_link_prompt(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world)
        item_id = await _item(session_factory, world, seq=1, state="processing",
                              sender_key=ALICE_KEY, execution_id=exec_id)
        await _run(session_factory, world, text="/stop", sender="stranger-key")
        assert (await _load_item(session_factory, item_id)).state == "processing"
        texts = await _feedback_texts(session_factory)
        assert any("连接你的外部账号" in t for t in texts)

    async def test_repeat_stop_idempotent(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world)
        await _item(session_factory, world, seq=1, state="cancelling",
                    sender_key=ALICE_KEY, execution_id=exec_id)
        await _run(session_factory, world, text="/stop", sender=ALICE_KEY)
        texts = await _feedback_texts(session_factory)
        assert any("正在停止中" in t for t in texts)

    async def test_cross_provider_bare_key_no_impersonation(self, session_factory):
        """Same user_key string under another provider maps to another user —
        bare-key comparison would impersonate; triple resolution must not."""
        world = await _seed_world(session_factory)
        # map the SAME string 'staff-alice' under github → bob's user
        async with session_factory() as session, session.begin():
            session.add(
                ExternalIdentity(provider="github", provider_tenant_key="gh-inst",
                                 external_user_key=ALICE_KEY, user_id=world["user_b"])
            )
        # item sender is dingtalk:corp:staff-alice (→ user_a) but we hack the
        # stored triple to github so resolution yields user_b ≠ actor user_a
        exec_id = await _execution(session_factory, world)
        item_id = uuid.uuid4()
        async with session_factory() as session, session.begin():
            session.add(
                IntegrationMessageQueue(
                    id=item_id, workspace_id=world["ws"],
                    integration_id=world["integration"], binding_id=world["binding"],
                    conversation_key=CONV_KEY, seq=1,
                    dispatch_mode="serial_conversation", state="processing",
                    execution_id=exec_id, target_agent_id=world["agent"],
                    sender_identity_key=f"github:gh-inst:{ALICE_KEY}",
                )
            )
        # actor alice (dingtalk triple → user_a) tries to stop it: the item
        # owner resolves to user_b — NOT the same user → no cancel.
        await _run(session_factory, world, text="/stop", sender=ALICE_KEY)
        item = await _load_item(session_factory, item_id)
        assert item.state == "processing"  # impersonation failed (triples differ)


class TestBtw:
    async def test_append_to_processing_execution(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world)
        await _item(session_factory, world, seq=1, state="processing",
                    sender_key=ALICE_KEY, execution_id=exec_id)
        outcome, _ = await _run(
            session_factory, world, text="/btw use staging env", sender=ALICE_KEY
        )
        assert outcome.handled is True
        assert outcome.passthrough_text is None
        from mesh.db.models.integration import ExecutionContextAppend

        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(ExecutionContextAppend).where(
                            ExecutionContextAppend.execution_id == exec_id
                        )
                    )
                ).scalars().all()
            )
        assert len(rows) == 1
        assert rows[0].source == "im_btw"
        assert rows[0].payload["text"] == "use staging env"
        assert rows[0].payload["sender_display"] == "Alice"
        texts = await _feedback_texts(session_factory)
        assert any("已补充" in t for t in texts)

    async def test_cancelling_refused(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world)
        await _item(session_factory, world, seq=1, state="cancelling",
                    sender_key=ALICE_KEY, execution_id=exec_id)
        outcome, _ = await _run(session_factory, world, text="/btw note", sender=ALICE_KEY)
        assert outcome.handled is True
        texts = await _feedback_texts(session_factory)
        assert any("正在停止" in t for t in texts)

    async def test_no_inflight_passthrough(self, session_factory):
        world = await _seed_world(session_factory)
        outcome, _ = await _run(
            session_factory, world, text="/btw check the logs", sender=ALICE_KEY
        )
        assert outcome.handled is True
        assert outcome.passthrough_text == "check the logs"
        texts = await _feedback_texts(session_factory)
        assert any("已按新消息排队" in t for t in texts)

    async def test_no_args_usage(self, session_factory):
        world = await _seed_world(session_factory)
        outcome, _ = await _run(session_factory, world, text="/btw", sender=ALICE_KEY)
        assert outcome.handled is True
        texts = await _feedback_texts(session_factory)
        assert any("用法" in t for t in texts)

    async def test_caps_enforced(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world)
        await _item(session_factory, world, seq=1, state="processing",
                    sender_key=ALICE_KEY, execution_id=exec_id)
        settings = _settings(context_append_max_count=1)
        await _run(session_factory, world, text="/btw one", sender=ALICE_KEY, settings=settings)
        await _run(session_factory, world, text="/btw two", sender=ALICE_KEY, settings=settings)
        texts = await _feedback_texts(session_factory)
        assert any("上限" in t for t in texts)
        from mesh.db.models.integration import ExecutionContextAppend

        async with session_factory() as session:
            count = len(
                
                    (
                        await session.execute(
                            select(ExecutionContextAppend).where(
                                ExecutionContextAppend.execution_id == exec_id
                            )
                        )
                    ).scalars().all()
                
            )
        assert count == 1  # second refused

    async def test_others_processing_forbidden(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world)
        await _item(session_factory, world, seq=1, state="processing",
                    sender_key=ALICE_KEY, execution_id=exec_id)
        outcome, _ = await _run(session_factory, world, text="/btw note", sender=BOB_KEY)
        assert outcome.handled is True
        assert outcome.passthrough_text is None
        texts = await _feedback_texts(session_factory)
        assert any("没有权限" in t for t in texts)


class TestParsingAndAudit:
    async def test_not_a_command_returns_none(self, session_factory):
        world = await _seed_world(session_factory)
        outcome, _ = await _run(session_factory, world, text="hello /stop world", sender=ALICE_KEY)
        assert outcome is None

    async def test_unknown_command_help(self, session_factory):
        world = await _seed_world(session_factory)
        outcome, _ = await _run(session_factory, world, text="/frobnicate", sender=ALICE_KEY)
        assert outcome.handled is True
        texts = await _feedback_texts(session_factory)
        assert any("可用命令" in t for t in texts)
        # commands never enqueue
        async with session_factory() as session:
            count = len(
                
                    (
                        await session.execute(select(IntegrationMessageQueue))
                    ).scalars().all()
                
            )
        assert count == 0

    async def test_case_insensitive(self, session_factory):
        world = await _seed_world(session_factory)
        outcome, _ = await _run(session_factory, world, text="/HELP", sender=ALICE_KEY)
        assert outcome.handled is True

    async def test_audit_payload_written(self, session_factory):
        world = await _seed_world(session_factory)
        outcome, event_id = await _run(
            session_factory, world, text="/btw check the logs", sender=ALICE_KEY
        )
        async with session_factory() as session:
            row = await session.get(IntegrationEvent, event_id)
        assert row.payload["_mesh_command"]["name"] == "btw"
        assert row.payload["_mesh_command"]["result"] == "passthrough"


class TestEdgeBranches:
    async def test_stop_all_terminal_no_in_flight_text(self, session_factory):
        world = await _seed_world(session_factory)
        await _item(session_factory, world, seq=1, state="done", sender_key=ALICE_KEY)
        outcome, _ = await _run(session_factory, world, text="/stop", sender=ALICE_KEY)
        assert outcome.handled is True
        texts = await _feedback_texts(session_factory)
        assert any("当前没有进行中的任务" in t for t in texts)

    async def test_btw_processing_without_execution_passthrough(self, session_factory):
        """Defensive: processing item lacking a bound execution (unreachable
        via the consumer contract) falls through as an ordinary message."""
        world = await _seed_world(session_factory)
        await _item(session_factory, world, seq=1, state="processing",
                    sender_key=ALICE_KEY, execution_id=None)
        outcome, _ = await _run(
            session_factory, world, text="/btw note here", sender=ALICE_KEY
        )
        assert outcome.handled is True
        assert outcome.passthrough_text == "note here"

    async def test_btw_execution_cancelling_at_runtime_not_acceptable(self, session_factory):
        """Item still processing but the execution already cancelling at the
        runtime layer → append gate refuses (append_not_acceptable)."""
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world, status="cancelling")
        await _item(session_factory, world, seq=1, state="processing",
                    sender_key=ALICE_KEY, execution_id=exec_id)
        outcome, _ = await _run(session_factory, world, text="/btw late note", sender=ALICE_KEY)
        assert outcome.handled is True
        texts = await _feedback_texts(session_factory)
        assert any("正在停止" in t for t in texts)

    async def test_stop_item_with_malformed_sender_triple_is_foreign(self, session_factory):
        """An item whose sender triple fails validation has no resolvable
        owner — it is treated as someone else's item (never bare-key matched)."""
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world)
        item_id = uuid.uuid4()
        async with session_factory() as session, session.begin():
            session.add(
                IntegrationMessageQueue(
                    id=item_id, workspace_id=world["ws"],
                    integration_id=world["integration"], binding_id=world["binding"],
                    conversation_key=CONV_KEY, seq=1,
                    dispatch_mode="serial_conversation", state="processing",
                    execution_id=exec_id, target_agent_id=world["agent"],
                    sender_identity_key="not-a-valid-triple",  # unparseable
                )
            )
        await _run(session_factory, world, text="/stop", sender=ALICE_KEY)
        item = await _load_item(session_factory, item_id)
        assert item.state == "processing"  # untouched — not alice's
        texts = await _feedback_texts(session_factory)
        assert any("没有权限" in t for t in texts)


class TestCopyConsistency:
    """§3.7 two-stage feedback is ONE bot voice (MES-121 regression guard).

    The immediate stage (commands.py) and the terminal stage
    (queue_events.stopped_feedback_text) must stay in the same language with
    the same wording basis — the spec-pinned Chinese literals — and quote the
    task excerpt identically (「…」) under stage-distinct emojis (⏳ → 🛑).
    """

    def test_stop_two_stage_same_voice_and_quoting(self):
        from mesh.integrations.commands import (
            stopping_feedback_text,
            stopping_with_cancelled_feedback_text,
        )
        from mesh.integrations.queue_events import stopped_feedback_text

        excerpt = "部署生产环境"
        immediate = stopping_feedback_text(excerpt)
        combined = stopping_with_cancelled_feedback_text(excerpt, 3)
        terminal = stopped_feedback_text(excerpt)
        # Spec §3.7 pinned literals
        assert immediate == f"⏳ 正在停止任务「{excerpt}」…"
        assert combined == f"⏳ 正在停止任务「{excerpt}」…，并已取消 3 条排队消息"
        assert terminal == f"🛑 已停止任务「{excerpt}」"
        # Both stages: same 「…」 excerpt quoting + CJK copy
        for text in (immediate, combined, terminal):
            assert f"「{excerpt}」" in text
            assert any("一" <= ch <= "鿿" for ch in text)

    def test_feedback_constants_match_spec_literals(self):
        from mesh.integrations import commands as cmd

        pinned = [
            (cmd._CANCELLING_IN_PROGRESS_TEXT, "任务正在停止中"),
            (cmd._TERMINAL_NO_TASK_TEXT, "当前没有进行中的任务"),
            (cmd._NOTHING_TO_STOP_TEXT, "当前没有进行中或排队的任务（你的）"),
            (cmd._FORBIDDEN_TEXT, "你没有权限操作该任务"),
            (cmd._BTW_OK_TEXT, "已补充给正在处理的任务（将在下一步生效）"),
            (cmd._BTW_CANCELLING_TEXT, "任务正在停止，无法补充；停止完成后可重新派发"),
            (cmd._BTW_NO_ITEM_HINT, "当前没有进行中的任务，已按新消息排队"),
            (cmd._BTW_LIMIT_TEXT, "补充已达上限，请直接新建任务说明"),
            (
                cmd._LINK_PROMPT_TEXT,
                "请先在 Mesh 站内连接你的外部账号（网页端：设置 → 外部身份），连接成功后即可使用命令",
            ),
            (cmd._BTW_USAGE_TEXT, "用法：/btw <补充说明> — 向正在处理的任务追加上下文"),
        ]
        for actual, expected in pinned:
            assert actual == expected

    def test_all_feedback_copy_is_chinese(self):
        """Every user-facing command literal carries CJK text and contains no
        English sentence fragment (the MES-121 zh/en drift, guarded)."""
        import re as _re

        from mesh.integrations import commands as cmd

        literals = [
            cmd.HELP_TEXT,
            cmd._LINK_PROMPT_TEXT,
            cmd._FORBIDDEN_TEXT,
            cmd._NOTHING_TO_STOP_TEXT,
            cmd._CANCELLING_IN_PROGRESS_TEXT,
            cmd._TERMINAL_NO_TASK_TEXT,
            cmd._BTW_OK_TEXT,
            cmd._BTW_CANCELLING_TEXT,
            cmd._BTW_NO_ITEM_HINT,
            cmd._BTW_LIMIT_TEXT,
            cmd._BTW_USAGE_TEXT,
            cmd.stopping_feedback_text("x"),
            cmd.stopping_with_cancelled_feedback_text("x", 1),
        ]
        for text in literals:
            assert _re.search(r"[一-鿿]", text), f"no CJK in {text!r}"
            # No two consecutive English words — commands ("/stop", "/btw")
            # and the product name stay, but English prose must not.
            assert not _re.search(r"[A-Za-z]{2,}\s+[A-Za-z]{4,}", text), f"EN prose in {text!r}"


class TestImmediateFeedbackDeliveryFields:
    """MES-122: immediate-stage command feedback carries self-specified
    conversation delivery fields — the payload must not depend on a queue
    item (feedback fires for empty queues too: /help, unknown commands,
    /stop with nothing in flight). conversationType "1"=单聊/"2"=群聊."""

    async def test_stop_immediate_feedback_direct_carries_type_and_target(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world, status="running")
        await _item(
            session_factory, world, seq=1, state="processing",
            sender_key=ALICE_KEY, execution_id=exec_id, excerpt="deploy prod",
        )
        await _run(
            session_factory, world, text="/stop", sender=ALICE_KEY,
            event_payload={"conversationType": "1", "text": {"content": "/stop"}},
        )
        immediate = [
            e for e in await _feedback_events(session_factory)
            if e.payload.get("stage") == "immediate"
        ]
        assert len(immediate) == 1
        assert immediate[0].payload["conversation_type"] == "direct"
        assert immediate[0].payload["target_user_key"] == ALICE_KEY

    async def test_stop_immediate_feedback_group_carries_group_type(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world, status="running")
        await _item(
            session_factory, world, seq=1, state="processing",
            sender_key=ALICE_KEY, execution_id=exec_id,
        )
        await _run(
            session_factory, world, text="/stop", sender=ALICE_KEY,
            event_payload={"conversationType": "2", "text": {"content": "/stop"}},
        )
        immediate = [
            e for e in await _feedback_events(session_factory)
            if e.payload.get("stage") == "immediate"
        ]
        assert len(immediate) == 1
        assert immediate[0].payload["conversation_type"] == "group"
        assert "target_user_key" not in immediate[0].payload  # group needs none

    async def test_help_feedback_direct_self_sufficient_with_empty_queue(self, session_factory):
        """Empty conversation queue — queue-item derivation would find
        nothing, so the payload carries the fields itself."""
        world = await _seed_world(session_factory)
        await _run(
            session_factory, world, text="/help", sender=ALICE_KEY,
            event_payload={"conversationType": "1", "text": {"content": "/help"}},
        )
        feedbacks = await _feedback_events(session_factory)
        assert len(feedbacks) == 1
        assert feedbacks[0].payload["conversation_type"] == "direct"
        assert feedbacks[0].payload["target_user_key"] == ALICE_KEY

    async def test_btw_feedback_direct_carries_type_and_target(self, session_factory):
        world = await _seed_world(session_factory)
        exec_id = await _execution(session_factory, world, status="running")
        await _item(
            session_factory, world, seq=1, state="processing",
            sender_key=ALICE_KEY, execution_id=exec_id,
        )
        await _run(
            session_factory, world, text="/btw check the staging logs first",
            sender=ALICE_KEY,
            event_payload={"conversationType": "1", "text": {"content": "/btw x"}},
        )
        immediate = [
            e for e in await _feedback_events(session_factory)
            if e.payload.get("stage") == "immediate"
        ]
        assert len(immediate) == 1
        assert immediate[0].payload["conversation_type"] == "direct"
        assert immediate[0].payload["target_user_key"] == ALICE_KEY
