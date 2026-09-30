from __future__ import annotations

import time
from pathlib import Path

import pytest

from wecom_hermes_bridge.storage import SQLiteStore
from wecom_hermes_bridge.sync_worker import MessageSyncWorker
from wecom_hermes_bridge.wecom_api import SyncPage


class FakeSyncClient:
    def __init__(self, pages: list[SyncPage]) -> None:
        self.pages = pages
        self.calls: list[dict[str, str | None]] = []

    async def sync_messages(
        self,
        *,
        open_kfid: str,
        cursor: str | None,
        pull_token: str | None,
    ) -> SyncPage:
        self.calls.append(
            {
                "open_kfid": open_kfid,
                "cursor": cursor,
                "pull_token": pull_token,
            }
        )
        return self.pages.pop(0)


def _store(path: Path) -> SQLiteStore:
    store = SQLiteStore(path)
    store.initialize()
    return store


@pytest.mark.asyncio
async def test_sync_persists_cursor_and_deduplicates_messages(tmp_path) -> None:
    store = _store(tmp_path / "bridge.db")
    trigger_id = store.enqueue_callback_trigger("wk-one", "pull-token")
    trigger = store.next_pending_trigger()
    assert trigger is not None
    assert trigger.id == trigger_id

    duplicate = {
        "msgid": "msg-1",
        "open_kfid": "wk-one",
        "external_userid": "wm-user",
        "send_time": 100,
        "origin": 3,
        "msgtype": "text",
        "text": {"content": "hello"},
    }
    client = FakeSyncClient(
        [
            SyncPage(messages=[duplicate], next_cursor="cursor-1", has_more=True),
            SyncPage(messages=[duplicate], next_cursor="cursor-2", has_more=False),
        ]
    )

    inserted = await MessageSyncWorker(store=store, wecom=client).process_trigger(trigger)

    assert inserted == 1
    assert store.inbound_message_count() == 1
    assert store.get_state("sync_cursor:wk-one") == "cursor-2"
    assert store.next_pending_trigger() is None
    assert [call["cursor"] for call in client.calls] == [None, "cursor-1"]


@pytest.mark.asyncio
async def test_expired_callback_token_is_not_sent(tmp_path) -> None:
    store = _store(tmp_path / "bridge.db")
    store.enqueue_callback_trigger("wk-one", "expired-token")
    with store._connect() as connection:
        connection.execute(
            "UPDATE callback_trigger SET received_at = ?",
            (int(time.time()) - 600,),
        )
    trigger = store.next_pending_trigger()
    assert trigger is not None
    client = FakeSyncClient(
        [SyncPage(messages=[], next_cursor="cursor", has_more=False)]
    )

    await MessageSyncWorker(store=store, wecom=client).process_trigger(trigger)

    assert client.calls[0]["pull_token"] is None


@pytest.mark.asyncio
async def test_transient_page_failure_resumes_durable_cursor_and_deduplicates(
    tmp_path,
) -> None:
    class TransientClient(FakeSyncClient):
        async def sync_messages(self, **kwargs):
            if len(self.calls) == 1:
                self.calls.append(kwargs)
                raise RuntimeError("synthetic page failure")
            return await super().sync_messages(**kwargs)

    store = _store(tmp_path / "bridge.db")
    store.enqueue_callback_trigger("wk-one", "synthetic-pull-token")
    store.set_state("sync_cursor:wk-other", "other-cursor")
    trigger = store.next_pending_trigger()
    assert trigger is not None
    first = {
        "msgid": "first",
        "open_kfid": "wk-one",
        "external_userid": "wm-user",
        "send_time": 100,
        "origin": 3,
        "msgtype": "text",
        "text": {"content": "hello"},
    }
    second = {**first, "msgid": "second"}
    client = TransientClient(
        [
            SyncPage(messages=[first], next_cursor="cursor-1", has_more=True),
            SyncPage(messages=[first, second], next_cursor="cursor-2", has_more=False),
        ]
    )
    with pytest.raises(RuntimeError, match="synthetic page failure"):
        await MessageSyncWorker(store=store, wecom=client).process_trigger(trigger)
    assert store.inbound_message_count() == 1
    assert store.get_state("sync_cursor:wk-one") == "cursor-1"
    assert store.pending_trigger_count() == 1

    # Reopen both worker and store: the failed page must resume from cursor-1.
    reopened = _store(tmp_path / "bridge.db")
    pending = reopened.next_pending_trigger()
    assert pending is not None and pending.id == trigger.id
    inserted = await MessageSyncWorker(store=reopened, wecom=client).process_trigger(pending)
    assert inserted == 1
    assert reopened.inbound_message_count() == 2
    assert [call["cursor"] for call in client.calls] == [None, "cursor-1", "cursor-1"]
    assert reopened.get_state("sync_cursor:wk-one") == "cursor-2"
    assert reopened.get_state("sync_cursor:wk-other") == "other-cursor"
    assert reopened.pending_trigger_count() == 0

    # A duplicate callback and page do not reinsert either customer message.
    reopened.enqueue_callback_trigger("wk-one", "synthetic-pull-token")
    duplicate_trigger = reopened.next_pending_trigger()
    assert duplicate_trigger is not None
    duplicate_client = FakeSyncClient(
        [SyncPage(messages=[first, second], next_cursor="cursor-2", has_more=False)]
    )
    duplicate_worker = MessageSyncWorker(store=reopened, wecom=duplicate_client)
    assert await duplicate_worker.process_trigger(duplicate_trigger) == 0
    assert duplicate_client.calls[0]["cursor"] == "cursor-2"
    assert reopened.inbound_message_count() == 2
    assert reopened.pending_trigger_count() == 0
