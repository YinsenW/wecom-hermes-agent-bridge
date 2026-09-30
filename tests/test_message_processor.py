from __future__ import annotations

import asyncio
import json

import pytest

from wecom_hermes_bridge.hermes_api import HermesAPIClient, HermesSession
from wecom_hermes_bridge.message_processor import (
    CustomerMessageProcessor,
    HANDOFF_REPLY,
    outbound_msgid,
    truncate_utf8,
)
from wecom_hermes_bridge.storage import InboundMessage, SQLiteStore
from wecom_hermes_bridge.wecom_api import ServiceState


class FakeHermes:
    def __init__(self, reply: str = "这是自动回复") -> None:
        self.reply = reply
        self.chat_calls = 0
        self.system_prompts: list[str] = []
        self.session_keys: list[str] = []
        self.chat_keys: list[str] = []
        self.session_ids: list[str] = []

    async def get_or_create_session(self, **kwargs) -> HermesSession:
        self.system_prompts.append(kwargs["system_prompt"])
        self.session_keys.append(kwargs["external_session_key"])
        return HermesSession(
            HermesAPIClient.session_id_for(kwargs["external_session_key"])
        )

    async def chat(self, **kwargs) -> str:
        self.chat_calls += 1
        self.chat_keys.append(kwargs["external_session_key"])
        self.session_ids.append(kwargs["session"].session_id)
        return self.reply


class FakeWeCom:
    def __init__(self, state: int = 0) -> None:
        self.state = state
        self.transitions: list[int] = []
        self.sent: list[tuple[str, str]] = []
        self.event_sent: list[tuple[str, str]] = []
        self.state_calls = 0

    async def get_service_state(self, **_kwargs) -> ServiceState:
        self.state_calls += 1
        return ServiceState(self.state)

    async def transition_service_state(self, *, target_state: int, **_kwargs) -> str:
        self.transitions.append(target_state)
        self.state = target_state
        return "event-code" if target_state == 2 else ""

    async def send_text(self, *, msgid: str, content: str, **_kwargs) -> str:
        self.sent.append((msgid, content))
        return msgid

    async def send_event_text(self, *, msgid: str, content: str, **_kwargs) -> str:
        self.event_sent.append((msgid, content))
        return msgid


class FakeKnowledge:
    def build_context(self, question: str) -> str:
        assert question == "知识库有哪些知识工具？"
        return "只读知识证据：knowledge.search"


def make_store(tmp_path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "bridge.db")
    store.initialize()
    return store


def insert_text(
    store: SQLiteStore,
    *,
    msgid: str,
    content: str,
    open_kfid: str = "wk-one",
    external_userid: str = "wm-user",
    msgtype: str = "text",
) -> InboundMessage:
    message = InboundMessage(
        msgid=msgid,
        open_kfid=open_kfid,
        external_userid=external_userid,
        send_time=100,
        origin=3,
        msgtype=msgtype,
        payload_json=json.dumps({"text": {"content": content}}),
    )
    assert store.insert_inbound_message(message)
    return message


@pytest.mark.asyncio
async def test_automatic_reply_moves_session_to_bot_and_sends_once(tmp_path) -> None:
    store = make_store(tmp_path)
    message = insert_text(store, msgid="incoming-1", content="订单到哪里了？")
    hermes = FakeHermes("正在为您查询。")
    wecom = FakeWeCom(state=0)
    processor = CustomerMessageProcessor(
        corp_id="ww-corp",
        store=store,
        wecom=wecom,
        hermes=hermes,
        dry_run=False,
    )

    await processor.process_message(message)
    await processor.process_message(message)

    reply_id = outbound_msgid("reply", message.msgid)
    assert wecom.transitions == [1]
    assert wecom.sent == [(reply_id, "正在为您查询。")]
    assert store.outbound_status(reply_id) == "sent"
    assert hermes.chat_calls == 1


@pytest.mark.asyncio
async def test_explicit_handoff_skips_hermes_and_uses_event_message(tmp_path) -> None:
    store = make_store(tmp_path)
    message = insert_text(store, msgid="incoming-2", content="请帮我转人工客服")
    hermes = FakeHermes()
    wecom = FakeWeCom(state=1)
    processor = CustomerMessageProcessor(
        corp_id="ww-corp",
        store=store,
        wecom=wecom,
        hermes=hermes,
        dry_run=False,
    )

    await processor.process_message(message)

    assert hermes.chat_calls == 0
    assert wecom.transitions == [2]
    assert len(wecom.event_sent) == 1
    assert store.outbound_status(outbound_msgid("handoff", message.msgid)) == "sent"


@pytest.mark.asyncio
async def test_dry_run_records_but_does_not_mutate_wecom(tmp_path) -> None:
    store = make_store(tmp_path)
    message = insert_text(store, msgid="incoming-3", content="你好")
    hermes = FakeHermes("您好")
    wecom = FakeWeCom(state=0)
    processor = CustomerMessageProcessor(
        corp_id="ww-corp",
        store=store,
        wecom=wecom,
        hermes=hermes,
        dry_run=True,
    )

    await processor.process_message(message)

    assert wecom.transitions == []
    assert wecom.sent == []
    assert store.outbound_status(outbound_msgid("reply", message.msgid)) == "dry_run"


@pytest.mark.asyncio
async def test_new_bot_session_unlocks_after_human_service_ends(tmp_path) -> None:
    store = make_store(tmp_path)
    first = insert_text(store, msgid="incoming-4", content="转人工")
    hermes = FakeHermes("新会话回复")
    wecom = FakeWeCom(state=1)
    processor = CustomerMessageProcessor(
        corp_id="ww-corp",
        store=store,
        wecom=wecom,
        hermes=hermes,
        dry_run=False,
    )
    await processor.process_message(first)

    wecom.state = 0
    second = insert_text(store, msgid="incoming-5", content="重新咨询")
    await processor.process_message(second)

    conversation = store.get_or_create_conversation(
        open_kfid="wk-one", external_userid="wm-user"
    )
    assert conversation.generation == 2
    assert conversation.human_locked is False
    assert hermes.chat_calls == 1
    assert wecom.sent[-1][1] == "新会话回复"


@pytest.mark.asyncio
async def test_relevant_knowledge_is_added_to_hermes_instructions(tmp_path) -> None:
    store = make_store(tmp_path)
    message = insert_text(
        store,
        msgid="incoming-knowledge",
        content="知识库有哪些知识工具？",
    )
    hermes = FakeHermes("知识库提供检索工具。")
    wecom = FakeWeCom(state=0)
    processor = CustomerMessageProcessor(
        corp_id="ww-corp",
        store=store,
        wecom=wecom,
        hermes=hermes,
        dry_run=False,
        knowledge=FakeKnowledge(),
    )

    await processor.process_message(message)

    assert len(hermes.system_prompts) == 1
    assert "只读知识证据：knowledge.search" in hermes.system_prompts[0]
    assert wecom.sent[-1][1] == "知识库提供检索工具。"


def test_utf8_truncation_respects_byte_limit() -> None:
    result = truncate_utf8("你" * 1000, 2048)
    assert len(result.encode("utf-8")) <= 2048
    assert result.endswith("…")


class FailingSendWeCom(FakeWeCom):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(state=1)
        self.store = store
        self.failures_left = 2
        self.attempts: list[tuple[str, str]] = []

    async def send_text(self, *, msgid: str, content: str, **kwargs) -> str:
        # Preparation must be durable before even the first send attempt.
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT content, status FROM outbound_message WHERE msgid = ?", (msgid,)
            ).fetchone()
        assert row == (content, "pending")
        self.attempts.append((msgid, content))
        if self.failures_left:
            self.failures_left -= 1
            raise RuntimeError("synthetic send failure")
        return await super().send_text(msgid=msgid, content=content, **kwargs)


def make_processor(store, wecom, hermes, *, dry_run=False) -> CustomerMessageProcessor:
    return CustomerMessageProcessor(
        corp_id="ww-corp", store=store, wecom=wecom, hermes=hermes, dry_run=dry_run
    )


def inbound_error(store: SQLiteStore, message: InboundMessage) -> str | None:
    with store._connect() as connection:
        return connection.execute(
            "SELECT last_error FROM inbound_message WHERE msgid = ?", (message.msgid,)
        ).fetchone()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("reopen", [False, True], ids=["same-process", "reopened-store"])
async def test_send_retry_reuses_persisted_truncated_reply_and_deduplicates(
    tmp_path, reopen
) -> None:
    store = make_store(tmp_path)
    message = insert_text(store, msgid="retry", content="你好")
    hermes = FakeHermes("你" * 1000)
    wecom = FailingSendWeCom(store)
    processor = make_processor(store, wecom, hermes)
    expected = (outbound_msgid("reply", message.msgid), truncate_utf8(hermes.reply, 2048))

    with pytest.raises(RuntimeError, match="synthetic send failure"):
        await processor.process_message(message)
    assert store.outbound_status(expected[0]) == "pending"
    assert store.pending_customer_message_count() == 1
    assert hermes.chat_calls == 1

    hermes.reply = "a different generated reply"
    retry_hermes = hermes
    if reopen:
        store = make_store(tmp_path)
        retry_hermes = FakeHermes("a different generated reply")
        processor = make_processor(store, wecom, retry_hermes)
    with pytest.raises(RuntimeError, match="synthetic send failure"):
        await processor.process_message(message)
    await processor.process_message(message)

    assert wecom.attempts == [expected] * 3
    assert wecom.sent == [expected]
    assert hermes.chat_calls == 1
    assert retry_hermes.chat_calls == (0 if reopen else 1)
    assert len(retry_hermes.system_prompts) == (0 if reopen else 1)
    assert store.pending_customer_message_count() == 0
    assert store.outbound_status(expected[0]) == "sent"
    with store._connect() as connection:
        assert connection.execute(
            "SELECT content FROM outbound_message WHERE msgid = ?", (expected[0],)
        ).fetchone() == (expected[1],)

    # Simulate a restart after send completion, even if the inbound is replayed.
    restarted_hermes = FakeHermes()
    restarted = make_processor(make_store(tmp_path), wecom, restarted_hermes)
    await restarted.process_message(message)
    assert restarted_hermes.chat_calls == 0
    assert restarted_hermes.system_prompts == []
    assert wecom.attempts == [expected] * 3


MESSAGE_BRANCHES = [("text", "你好"), ("image", ""), ("text", "请转人工客服")]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [2, 3, 4])
@pytest.mark.parametrize(
    "msgtype,content", MESSAGE_BRANCHES, ids=["normal", "nontext", "handoff"]
)
async def test_all_branches_skip_human_or_inactive_service(
    tmp_path, state, msgtype, content
) -> None:
    store = make_store(tmp_path)
    message = insert_text(store, msgid="guard", content=content, msgtype=msgtype)
    hermes, wecom = FakeHermes(), FakeWeCom(state)
    await make_processor(store, wecom, hermes).process_message(message)

    assert wecom.transitions == []
    assert wecom.sent == wecom.event_sent == []
    assert hermes.chat_calls == 0
    assert hermes.system_prompts == []
    assert store.outbound_status(outbound_msgid("reply", message.msgid)) is None
    assert store.outbound_status(outbound_msgid("handoff", message.msgid)) is None
    assert store.pending_customer_message_count() == 0
    assert inbound_error(store, message) == (
        "inactive_session" if state == 4 else "human_service"
    )
    conversation = store.get_or_create_conversation(
        open_kfid="wk-one", external_userid="wm-user"
    )
    assert conversation.generation == 1
    assert conversation.human_locked is (state in {2, 3})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "msgtype,content", MESSAGE_BRANCHES, ids=["normal", "nontext", "handoff"]
)
async def test_dry_run_locked_conversation_blocks_every_branch(
    tmp_path, msgtype, content
) -> None:
    store = make_store(tmp_path)
    message = insert_text(store, msgid="locked", content=content, msgtype=msgtype)
    store.get_or_create_conversation(open_kfid="wk-one", external_userid="wm-user")
    store.set_conversation_human_locked(
        open_kfid="wk-one", external_userid="wm-user", locked=True
    )
    hermes, wecom = FakeHermes(), FakeWeCom(state=0)
    await make_processor(store, wecom, hermes, dry_run=True).process_message(message)

    assert hermes.chat_calls == 0
    assert hermes.system_prompts == []
    assert wecom.state_calls == 0
    assert wecom.transitions == []
    assert wecom.sent == wecom.event_sent == []
    assert store.outbound_status(outbound_msgid("reply", message.msgid)) is None
    assert store.outbound_status(outbound_msgid("handoff", message.msgid)) is None
    assert inbound_error(store, message) == "human_locked"
    assert store.pending_customer_message_count() == 0


class AwaitedHermes(FakeHermes):
    def __init__(self, *, fail: bool) -> None:
        super().__init__()
        self.fail = fail
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    async def chat(self, **kwargs) -> str:
        self.started.set()
        await self.resume.wait()
        reply = await super().chat(**kwargs)
        if self.fail:
            raise RuntimeError("synthetic model failure")
        return reply


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [2, 3, 4])
@pytest.mark.parametrize("fail", [False, True], ids=["reply", "model-failure"])
async def test_takeover_while_chat_is_awaited_suppresses_reply_and_handoff(
    tmp_path, state, fail
) -> None:
    store = make_store(tmp_path)
    message = insert_text(store, msgid="takeover", content="你好")
    hermes, wecom = AwaitedHermes(fail=fail), FakeWeCom(state=1)
    processor = make_processor(store, wecom, hermes)
    task = asyncio.create_task(processor.process_message(message))
    try:
        await asyncio.wait_for(hermes.started.wait(), timeout=2)
        wecom.state = state
        hermes.resume.set()
        await asyncio.wait_for(task, timeout=2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert hermes.chat_calls == 1
    assert wecom.transitions == []
    assert wecom.sent == wecom.event_sent == []
    assert inbound_error(store, message) == (
        "inactive_session" if state == 4 else "human_service"
    )
    assert store.pending_customer_message_count() == 0
    assert store.outbound_status(outbound_msgid("handoff", message.msgid)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [2, 3, 4])
@pytest.mark.parametrize(
    "takeover_on_final_check", [False, True], ids=["before-retry", "before-send"]
)
async def test_pending_reply_retry_respects_takeover_without_regeneration(
    tmp_path, state, takeover_on_final_check
) -> None:
    store = make_store(tmp_path)
    message = insert_text(store, msgid="pending-takeover", content="你好")
    wecom = FailingSendWeCom(store)
    first_hermes = FakeHermes()
    with pytest.raises(RuntimeError, match="synthetic send failure"):
        await make_processor(store, wecom, first_hermes).process_message(message)

    class TakeoverWeCom(FakeWeCom):
        async def get_service_state(self, **kwargs):
            result = await super().get_service_state(**kwargs)
            self.state = state
            return result

    retry_wecom = TakeoverWeCom(1 if takeover_on_final_check else state)
    retry_hermes = FakeHermes()
    retry_processor = make_processor(make_store(tmp_path), retry_wecom, retry_hermes)
    await retry_processor.process_message(message)

    assert retry_hermes.chat_calls == 0
    assert retry_hermes.system_prompts == []
    assert retry_wecom.transitions == []
    assert retry_wecom.sent == retry_wecom.event_sent == []
    assert store.outbound_status(outbound_msgid("reply", message.msgid)) == "pending"
    assert store.pending_customer_message_count() == 0
    assert inbound_error(store, message) == (
        "inactive_session" if state == 4 else "human_service"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [False, True], ids=["live-mock", "dry-run"])
@pytest.mark.parametrize("stage", ["session", "chat"])
async def test_model_failure_handoffs_once_and_locks_conversation(
    tmp_path, dry_run, stage
) -> None:
    class BrokenHermes(FakeHermes):
        async def get_or_create_session(self, **kwargs):
            if stage == "session":
                raise RuntimeError("synthetic session failure")
            return await super().get_or_create_session(**kwargs)

        async def chat(self, **kwargs):
            await super().chat(**kwargs)
            raise RuntimeError("synthetic model failure")

    store = make_store(tmp_path)
    message = insert_text(store, msgid="model-failure", content="你好")
    hermes, wecom = BrokenHermes(), FakeWeCom(state=1)
    processor = make_processor(store, wecom, hermes, dry_run=dry_run)
    await processor.process_message(message)
    await processor.process_message(message)

    assert wecom.sent == []
    assert wecom.transitions == ([] if dry_run else [2])
    assert wecom.event_sent == (
        [] if dry_run else [(outbound_msgid("handoff", message.msgid), HANDOFF_REPLY)]
    )
    assert store.outbound_status(outbound_msgid("handoff", message.msgid)) == (
        "dry_run" if dry_run else "sent"
    )
    assert store.pending_customer_message_count() == 0
    assert hermes.chat_calls == (0 if stage == "session" else 1)
    assert store.get_or_create_conversation(
        open_kfid="wk-one", external_userid="wm-user"
    ).human_locked


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other_identity",
    [("wk-one", "wm-other"), ("wk-other", "wm-user")],
    ids=["customer", "kf-account"],
)
async def test_sessions_isolate_identity_and_generation_after_human_return(
    tmp_path, other_identity
) -> None:
    store = make_store(tmp_path)
    hermes, wecom = FakeHermes(), FakeWeCom(state=1)
    processor = make_processor(store, wecom, hermes)
    first = insert_text(store, msgid="isolation-first", content="你好")
    other = insert_text(
        store, msgid="isolation-other", content="你好",
        open_kfid=other_identity[0], external_userid=other_identity[1],
    )
    await processor.process_message(first)
    await processor.process_message(other)
    handoff = insert_text(store, msgid="isolation-handoff", content="转人工")
    await processor.process_message(handoff)
    wecom.state = 1
    returned = insert_text(store, msgid="isolation-return", content="你好")
    await processor.process_message(returned)
    other_again = insert_text(
        store, msgid="isolation-other-again", content="你好",
        open_kfid=other_identity[0], external_userid=other_identity[1],
    )
    await processor.process_message(other_again)

    other_key = f"ww-corp:{other_identity[0]}:{other_identity[1]}:1"
    assert hermes.session_keys == hermes.chat_keys == [
        "ww-corp:wk-one:wm-user:1", other_key, "ww-corp:wk-one:wm-user:2", other_key
    ]
    assert len(set(hermes.session_ids[:3])) == 3
    assert hermes.session_ids[1] == hermes.session_ids[3]
    assert store.get_or_create_conversation(
        open_kfid=other_identity[0], external_userid=other_identity[1]
    ).generation == 1
    assert store.get_or_create_conversation(
        open_kfid="wk-one", external_userid="wm-user"
    ).generation == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [False, True], ids=["live-mock", "dry-run"])
@pytest.mark.parametrize(
    "msgtype,content", MESSAGE_BRANCHES[1:], ids=["nontext", "handoff"]
)
async def test_available_service_handoff_preserves_wording_and_dedup(
    tmp_path, dry_run, msgtype, content
) -> None:
    store = make_store(tmp_path)
    message = insert_text(store, msgid="available-handoff", content=content, msgtype=msgtype)
    hermes, wecom = FakeHermes(), FakeWeCom(state=0)
    processor = make_processor(store, wecom, hermes, dry_run=dry_run)
    await processor.process_message(message)
    restarted = make_processor(make_store(tmp_path), wecom, hermes, dry_run=dry_run)
    await restarted.process_message(message)

    assert hermes.chat_calls == 0
    assert hermes.system_prompts == []
    assert wecom.sent == []
    assert wecom.transitions == ([] if dry_run else [2])
    assert wecom.event_sent == (
        [] if dry_run else [(outbound_msgid("handoff", message.msgid), HANDOFF_REPLY)]
    )
    assert wecom.state_calls == (0 if dry_run else 2)
    assert store.outbound_status(outbound_msgid("handoff", message.msgid)) == (
        "dry_run" if dry_run else "sent"
    )
    assert store.get_or_create_conversation(
        open_kfid="wk-one", external_userid="wm-user"
    ).human_locked


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [2, 3, 4])
@pytest.mark.parametrize(
    "msgtype,content", MESSAGE_BRANCHES[1:], ids=["nontext", "handoff"]
)
async def test_takeover_before_handoff_transition_is_checked(
    tmp_path, state, msgtype, content
) -> None:
    class TakeoverWeCom(FakeWeCom):
        async def get_service_state(self, **kwargs):
            result = await super().get_service_state(**kwargs)
            self.state = state
            return result

    store = make_store(tmp_path)
    message = insert_text(store, msgid="handoff-takeover", content=content, msgtype=msgtype)
    hermes, wecom = FakeHermes(), TakeoverWeCom(state=1)
    await make_processor(store, wecom, hermes).process_message(message)

    assert hermes.chat_calls == 0
    assert wecom.transitions == []
    assert wecom.sent == wecom.event_sent == []
    assert store.outbound_status(outbound_msgid("handoff", message.msgid)) is None
    assert inbound_error(store, message) == (
        "inactive_session" if state == 4 else "human_service"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("handoff", [False, True], ids=["normal-reply", "handoff"])
async def test_final_state_lookup_failure_has_no_external_side_effect(
    tmp_path, handoff
) -> None:
    class FailedStateWeCom(FakeWeCom):
        async def get_service_state(self, **kwargs):
            if self.state_calls == 1:
                raise RuntimeError("synthetic state lookup failure")
            return await super().get_service_state(**kwargs)

    store = make_store(tmp_path)
    message = insert_text(store, msgid="state-failure", content="转人工" if handoff else "你好")
    hermes, wecom = FakeHermes(), FailedStateWeCom(state=1)
    processor = make_processor(store, wecom, hermes)
    with pytest.raises(RuntimeError, match="synthetic state lookup failure"):
        await processor.process_message(message)
    assert wecom.transitions == []
    assert wecom.sent == wecom.event_sent == []
    assert store.pending_customer_message_count() == 1
    assert store.outbound_status(outbound_msgid("reply", message.msgid)) == (
        None if handoff else "pending"
    )
    assert store.outbound_status(outbound_msgid("handoff", message.msgid)) is None

    # A successful lookup later permits retry, with no repeated normal generation.
    wecom.state_calls = 2
    await processor.process_message(message)
    assert hermes.chat_calls == (0 if handoff else 1)
    assert wecom.transitions == ([2] if handoff else [])
    assert len(wecom.event_sent if handoff else wecom.sent) == 1
    assert store.pending_customer_message_count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [False, True], ids=["live-mock", "dry-run"])
@pytest.mark.parametrize("crash", [True, False], ids=["lock-crash", "completed"])
@pytest.mark.parametrize(
    "msgtype,content,failure_stage",
    [
        ("text", "请转人工客服", None),
        ("image", "", None),
        ("text", "你好", "session"),
        ("text", "你好", "chat"),
    ],
    ids=["explicit", "unsupported", "model-session-failure", "model-chat-failure"],
)
async def test_handoff_lock_recovery_and_historic_replay(
    tmp_path, monkeypatch, dry_run, crash, msgtype, content, failure_stage
) -> None:
    class HandoffHermes(FakeHermes):
        async def get_or_create_session(self, **kwargs):
            if failure_stage == "session":
                raise RuntimeError("synthetic session failure")
            return await super().get_or_create_session(**kwargs)

        async def chat(self, **kwargs):
            reply = await super().chat(**kwargs)
            if failure_stage == "chat":
                raise RuntimeError("synthetic model failure")
            return reply

    store = make_store(tmp_path)
    wecom, initial_hermes = FakeWeCom(state=1), FakeHermes()
    first = insert_text(store, msgid="before-handoff", content="你好")
    await make_processor(store, wecom, initial_hermes, dry_run=dry_run).process_message(first)
    assert initial_hermes.chat_keys == ["ww-corp:wk-one:wm-user:1"]

    handoff = insert_text(store, msgid="recover-handoff", content=content, msgtype=msgtype)
    handoff_id = outbound_msgid("handoff", handoff.msgid)
    expected_status = "dry_run" if dry_run else "sent"
    expected_events = [] if dry_run else [(handoff_id, HANDOFF_REPLY)]
    processor = make_processor(store, wecom, HandoffHermes(), dry_run=dry_run)

    def fail_lock(**_kwargs):
        assert store.outbound_status(handoff_id) == expected_status
        assert store.pending_customer_message_count() == 1
        assert wecom.event_sent == expected_events
        raise RuntimeError("synthetic lock persistence failure")

    if crash:
        with monkeypatch.context() as patch:
            patch.setattr(store, "set_conversation_human_locked", fail_lock)
            with pytest.raises(RuntimeError, match="synthetic lock persistence failure"):
                await processor.process_message(handoff)
        assert store.pending_customer_message_count() == 1
        assert not store.get_or_create_conversation(
            open_kfid="wk-one", external_userid="wm-user"
        ).human_locked
    else:
        await processor.process_message(handoff)

    # Reopen the disk-backed store; a healthy model must never answer this retry.
    store = make_store(tmp_path)
    retry_hermes = FakeHermes()
    retry = make_processor(store, wecom, retry_hermes, dry_run=dry_run)
    before_replay = (wecom.state_calls, list(wecom.transitions), list(wecom.sent))
    if crash:
        # Another local persistence failure must still leave the inbound pending.
        with monkeypatch.context() as patch:
            patch.setattr(store, "set_conversation_human_locked", fail_lock)
            with pytest.raises(RuntimeError, match="synthetic lock persistence failure"):
                await retry.process_message(handoff)
        assert store.pending_customer_message_count() == 1
    await retry.process_message(handoff)
    assert store.get_or_create_conversation(
        open_kfid="wk-one", external_userid="wm-user"
    ).human_locked
    assert store.pending_customer_message_count() == 0
    assert store.outbound_status(handoff_id) == expected_status
    assert wecom.event_sent == expected_events
    assert (wecom.state_calls, wecom.transitions, wecom.sent) == before_replay
    assert retry_hermes.session_keys == retry_hermes.chat_keys == []
    assert store.outbound_status(outbound_msgid("reply", handoff.msgid)) is None

    # Dry-run itself never polls for human return. Use only the local WeCom fake
    # to exercise the existing return path for both persisted outbound statuses.
    wecom.state = 1
    returned = insert_text(store, msgid="after-human-return", content="重新咨询")
    returned_hermes = FakeHermes()
    returned_processor = make_processor(store, wecom, returned_hermes)
    await returned_processor.process_message(returned)
    conversation = store.get_or_create_conversation(
        open_kfid="wk-one", external_userid="wm-user"
    )
    assert conversation.generation == 2
    assert not conversation.human_locked
    assert returned_hermes.chat_keys == ["ww-corp:wk-one:wm-user:2"]

    # A fully processed historic handoff must leave the newer conversation alone.
    store = make_store(tmp_path)
    historic_hermes = FakeHermes()
    historic_processor = make_processor(store, wecom, historic_hermes, dry_run=dry_run)
    before_replay = (wecom.state_calls, list(wecom.transitions), list(wecom.sent))
    await historic_processor.process_message(handoff)
    assert store.get_or_create_conversation(
        open_kfid="wk-one", external_userid="wm-user"
    ) == conversation
    assert (wecom.state_calls, wecom.transitions, wecom.sent) == before_replay
    assert wecom.event_sent == expected_events
    assert historic_hermes.session_keys == historic_hermes.chat_keys == []

    following = insert_text(store, msgid="after-historic-replay", content="继续咨询")
    await make_processor(store, wecom, returned_hermes).process_message(following)
    assert returned_hermes.chat_keys == ["ww-corp:wk-one:wm-user:2"] * 2
    assert store.get_or_create_conversation(
        open_kfid="wk-one", external_userid="wm-user"
    ).generation == 2
    assert store.pending_customer_message_count() == 0
    assert wecom.event_sent == expected_events


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [False, True], ids=["live-mock", "dry-run"])
async def test_completed_normal_outbound_pending_inbound_replay_has_no_side_effects(
    tmp_path, monkeypatch, dry_run
) -> None:
    store = make_store(tmp_path)
    message = insert_text(store, msgid="reply-finalization-crash", content="你好")
    wecom, hermes = FakeWeCom(state=1), FakeHermes()
    reply_id = outbound_msgid("reply", message.msgid)

    def fail_processed(*_args, **_kwargs):
        assert store.outbound_status(reply_id) == ("dry_run" if dry_run else "sent")
        raise RuntimeError("synthetic inbound finalization failure")

    with monkeypatch.context() as patch:
        patch.setattr(store, "mark_inbound_processed", fail_processed)
        with pytest.raises(RuntimeError, match="synthetic inbound finalization failure"):
            await make_processor(store, wecom, hermes, dry_run=dry_run).process_message(message)
    assert store.pending_customer_message_count() == 1
    conversation = store.get_or_create_conversation(
        open_kfid="wk-one", external_userid="wm-user"
    )
    before_replay = (wecom.state_calls, list(wecom.transitions), list(wecom.sent))
    store = make_store(tmp_path)
    retry_hermes = FakeHermes()
    retry = make_processor(store, wecom, retry_hermes, dry_run=dry_run)
    await retry.process_message(message)
    await retry.process_message(message)
    assert store.pending_customer_message_count() == 0
    assert store.get_or_create_conversation(
        open_kfid="wk-one", external_userid="wm-user"
    ) == conversation
    assert retry_hermes.session_keys == retry_hermes.chat_keys == []
    assert (wecom.state_calls, wecom.transitions, wecom.sent) == before_replay
    assert wecom.event_sent == []
