from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CallbackTrigger:
    id: int
    open_kfid: str
    pull_token: str
    received_at: int


@dataclass(frozen=True, slots=True)
class InboundMessage:
    msgid: str
    open_kfid: str | None
    external_userid: str | None
    send_time: int
    origin: int
    msgtype: str
    payload_json: str


@dataclass(frozen=True, slots=True)
class Conversation:
    open_kfid: str
    external_userid: str
    generation: int
    hermes_session_id: str | None
    human_locked: bool


class SQLiteStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS callback_trigger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    open_kfid TEXT NOT NULL,
                    pull_token TEXT NOT NULL,
                    received_at INTEGER NOT NULL,
                    processed_at INTEGER,
                    last_error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_callback_trigger_pending
                ON callback_trigger(processed_at, received_at);

                CREATE TABLE IF NOT EXISTS kv_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS inbound_message (
                    msgid TEXT PRIMARY KEY,
                    open_kfid TEXT,
                    external_userid TEXT,
                    send_time INTEGER NOT NULL,
                    origin INTEGER NOT NULL,
                    msgtype TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    received_at INTEGER NOT NULL,
                    processed_at INTEGER,
                    last_error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_inbound_message_pending
                ON inbound_message(processed_at, send_time);

                CREATE TABLE IF NOT EXISTS conversation (
                    open_kfid TEXT NOT NULL,
                    external_userid TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 1,
                    hermes_session_id TEXT,
                    human_locked INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(open_kfid, external_userid)
                );

                CREATE TABLE IF NOT EXISTS outbound_message (
                    msgid TEXT PRIMARY KEY,
                    inbound_msgid TEXT NOT NULL,
                    open_kfid TEXT NOT NULL,
                    external_userid TEXT NOT NULL,
                    msgtype TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    sent_at INTEGER,
                    last_error TEXT,
                    FOREIGN KEY(inbound_msgid) REFERENCES inbound_message(msgid)
                );
                """
            )

    def enqueue_callback_trigger(self, open_kfid: str, pull_token: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO callback_trigger(open_kfid, pull_token, received_at)
                VALUES (?, ?, ?)
                """,
                (open_kfid, pull_token, int(time.time())),
            )
            return int(cursor.lastrowid)

    def pending_trigger_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM callback_trigger WHERE processed_at IS NULL"
            ).fetchone()
        return int(row[0])

    def next_pending_trigger(self) -> CallbackTrigger | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, open_kfid, pull_token, received_at
                FROM callback_trigger
                WHERE processed_at IS NULL
                ORDER BY received_at, id
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return CallbackTrigger(
            id=int(row[0]),
            open_kfid=str(row[1]),
            pull_token=str(row[2]),
            received_at=int(row[3]),
        )

    def mark_trigger_processed(self, trigger_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE callback_trigger
                SET processed_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (int(time.time()), trigger_id),
            )

    def mark_trigger_error(self, trigger_id: int, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE callback_trigger SET last_error = ? WHERE id = ?",
                (error[:1000], trigger_id),
            )

    def get_state(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM kv_state WHERE key = ?",
                (key,),
            ).fetchone()
        return None if row is None else str(row[0])

    def set_state(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO kv_state(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, int(time.time())),
            )

    def insert_inbound_message(self, message: InboundMessage) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO inbound_message(
                    msgid, open_kfid, external_userid, send_time, origin,
                    msgtype, payload_json, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.msgid,
                    message.open_kfid,
                    message.external_userid,
                    message.send_time,
                    message.origin,
                    message.msgtype,
                    message.payload_json,
                    int(time.time()),
                ),
            )
            return cursor.rowcount == 1

    def inbound_message_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM inbound_message").fetchone()
        return int(row[0])

    def inbound_processed(self, msgid: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT processed_at FROM inbound_message WHERE msgid = ?",
                (msgid,),
            ).fetchone()
        return row is not None and row[0] is not None

    def next_pending_customer_message(self) -> InboundMessage | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT msgid, open_kfid, external_userid, send_time,
                       origin, msgtype, payload_json
                FROM inbound_message
                WHERE processed_at IS NULL AND origin = 3
                ORDER BY send_time, received_at
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return InboundMessage(
            msgid=str(row[0]),
            open_kfid=None if row[1] is None else str(row[1]),
            external_userid=None if row[2] is None else str(row[2]),
            send_time=int(row[3]),
            origin=int(row[4]),
            msgtype=str(row[5]),
            payload_json=str(row[6]),
        )

    def mark_inbound_processed(self, msgid: str, *, error: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE inbound_message
                SET processed_at = ?, last_error = ?
                WHERE msgid = ?
                """,
                (int(time.time()), None if error is None else error[:1000], msgid),
            )

    def mark_inbound_error(self, msgid: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE inbound_message
                SET last_error = ?
                WHERE msgid = ? AND processed_at IS NULL
                """,
                (error[:1000], msgid),
            )

    def pending_customer_message_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM inbound_message
                WHERE processed_at IS NULL AND origin = 3
                """
            ).fetchone()
        return int(row[0])

    def get_or_create_conversation(
        self,
        *,
        open_kfid: str,
        external_userid: str,
    ) -> Conversation:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO conversation(
                    open_kfid, external_userid, updated_at
                ) VALUES (?, ?, ?)
                """,
                (open_kfid, external_userid, now),
            )
            row = connection.execute(
                """
                SELECT open_kfid, external_userid, generation,
                       hermes_session_id, human_locked
                FROM conversation
                WHERE open_kfid = ? AND external_userid = ?
                """,
                (open_kfid, external_userid),
            ).fetchone()
        return Conversation(
            open_kfid=str(row[0]),
            external_userid=str(row[1]),
            generation=int(row[2]),
            hermes_session_id=None if row[3] is None else str(row[3]),
            human_locked=bool(row[4]),
        )

    def set_conversation_hermes_session(
        self,
        *,
        open_kfid: str,
        external_userid: str,
        hermes_session_id: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE conversation
                SET hermes_session_id = ?, updated_at = ?
                WHERE open_kfid = ? AND external_userid = ?
                """,
                (hermes_session_id, int(time.time()), open_kfid, external_userid),
            )

    def set_conversation_human_locked(
        self,
        *,
        open_kfid: str,
        external_userid: str,
        locked: bool,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE conversation
                SET human_locked = ?, updated_at = ?
                WHERE open_kfid = ? AND external_userid = ?
                """,
                (int(locked), int(time.time()), open_kfid, external_userid),
            )

    def restart_conversation(
        self,
        *,
        open_kfid: str,
        external_userid: str,
    ) -> Conversation:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE conversation
                SET generation = generation + 1,
                    hermes_session_id = NULL,
                    human_locked = 0,
                    updated_at = ?
                WHERE open_kfid = ? AND external_userid = ?
                """,
                (int(time.time()), open_kfid, external_userid),
            )
        return self.get_or_create_conversation(
            open_kfid=open_kfid,
            external_userid=external_userid,
        )

    def upsert_outbound_message(
        self,
        *,
        msgid: str,
        inbound_msgid: str,
        open_kfid: str,
        external_userid: str,
        content: str,
        status: str,
        error: str | None = None,
    ) -> None:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO outbound_message(
                    msgid, inbound_msgid, open_kfid, external_userid,
                    msgtype, content, status, created_at, sent_at, last_error
                ) VALUES (?, ?, ?, ?, 'text', ?, ?, ?, ?, ?)
                ON CONFLICT(msgid) DO UPDATE SET
                    status = excluded.status,
                    sent_at = excluded.sent_at,
                    last_error = excluded.last_error
                """,
                (
                    msgid,
                    inbound_msgid,
                    open_kfid,
                    external_userid,
                    content,
                    status,
                    now,
                    now if status in {"sent", "dry_run"} else None,
                    None if error is None else error[:1000],
                ),
            )

    def outbound_status(self, msgid: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM outbound_message WHERE msgid = ?",
                (msgid,),
            ).fetchone()
        return None if row is None else str(row[0])

    def outbound_content(self, msgid: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT content FROM outbound_message WHERE msgid = ?",
                (msgid,),
            ).fetchone()
        return None if row is None else str(row[0])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        return connection
