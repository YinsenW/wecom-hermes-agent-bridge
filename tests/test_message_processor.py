from __future__ import annotations

import json

import pytest

from wecom_hermes_bridge.hermes_api import HermesSession
from wecom_hermes_bridge.message_processor import (
    CustomerMessageProcessor,
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

    async def get_or_create_session(self, **kwargs) -> HermesSession:
        self.system_prompts.append(kwargs["system_prompt"])
        return HermesSession("hermes-session-1")

    async def chat(self, **_kwargs) -> str:
        self.chat_calls += 1
        return self.reply


class FakeWeCom:
    def __init__(self, state: int = 0) -> None:
        self.state = state
        self.transitions: list[int] = []
        self.sent: list[tuple[str, str]] = []
        self.event_sent: list[tuple[str, str]] = []

    async def get_service_state(self, **_kwargs) -> ServiceState:
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


def insert_text(store: SQLiteStore, *, msgid: str, content: str) -> InboundMessage:
    message = InboundMessage(
        msgid=msgid,
        open_kfid="wk-one",
        external_userid="wm-user",
        send_time=100,
        origin=3,
        msgtype="text",
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
