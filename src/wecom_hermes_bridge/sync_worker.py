from __future__ import annotations

import asyncio
import json
import time
from typing import Protocol

from .storage import CallbackTrigger, InboundMessage, SQLiteStore
from .wecom_api import SyncPage


class SyncClient(Protocol):
    async def sync_messages(
        self,
        *,
        open_kfid: str,
        cursor: str | None,
        pull_token: str | None,
    ) -> SyncPage: ...


class MessageSyncWorker:
    def __init__(self, *, store: SQLiteStore, wecom: SyncClient) -> None:
        self._store = store
        self._wecom = wecom

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            trigger = self._store.next_pending_trigger()
            if trigger is None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=0.5)
                except TimeoutError:
                    continue
                continue
            try:
                await self.process_trigger(trigger)
            except Exception as exc:
                self._store.mark_trigger_error(trigger.id, type(exc).__name__)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=2)
                except TimeoutError:
                    continue

    async def process_trigger(self, trigger: CallbackTrigger) -> int:
        cursor_key = f"sync_cursor:{trigger.open_kfid}"
        cursor = self._store.get_state(cursor_key)
        pull_token = trigger.pull_token if trigger.received_at >= int(time.time()) - 540 else None
        inserted = 0

        for _page_number in range(100):
            page = await self._wecom.sync_messages(
                open_kfid=trigger.open_kfid,
                cursor=cursor,
                pull_token=pull_token,
            )
            for raw_message in page.messages:
                msgid = str(raw_message.get("msgid") or "")
                if not msgid:
                    continue
                message = InboundMessage(
                    msgid=msgid,
                    open_kfid=_optional_string(raw_message.get("open_kfid")),
                    external_userid=_optional_string(raw_message.get("external_userid")),
                    send_time=int(raw_message.get("send_time") or 0),
                    origin=int(raw_message.get("origin") or 0),
                    msgtype=str(raw_message.get("msgtype") or "unknown"),
                    payload_json=json.dumps(
                        raw_message,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
                if self._store.insert_inbound_message(message):
                    inserted += 1

            cursor = page.next_cursor
            if cursor:
                self._store.set_state(cursor_key, cursor)
            if not page.has_more:
                self._store.mark_trigger_processed(trigger.id)
                return inserted

        raise RuntimeError("sync_msg exceeded the 100-page safety limit")


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
