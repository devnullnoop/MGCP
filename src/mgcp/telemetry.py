"""Telemetry and analytics for MGCP (Memory Graph Core Primitives)."""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

logger = logging.getLogger(__name__)

DEFAULT_TELEMETRY_PATH = "~/.mgcp/telemetry.db"


class EventType(str, Enum):
    QUERY = "query"
    RETRIEVE = "retrieve"
    SPIDER = "spider"
    ADD = "add"
    REFINE = "refine"
    BOOTSTRAP = "bootstrap"
    SESSION_START = "session_start"
    SESSION_END = "session_end"


@dataclass
class TelemetryEvent:
    """A telemetry event."""

    id: str
    timestamp: datetime
    session_id: str
    event_type: EventType
    payload: dict[str, Any]


TELEMETRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSON NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    query_count INTEGER DEFAULT 0,
    lessons_retrieved INTEGER DEFAULT 0,
    unique_lessons TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS lesson_stats (
    lesson_id TEXT PRIMARY KEY,
    total_retrievals INTEGER DEFAULT 0,
    last_retrieved TEXT,
    total_score REAL DEFAULT 0.0,
    retrieval_count INTEGER DEFAULT 0
);
"""


class TelemetryLogger:
    """Logs telemetry events and provides analytics."""

    def __init__(self, db_path: str = DEFAULT_TELEMETRY_PATH):
        self.db_path = Path(os.path.expanduser(db_path))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False
        self._subscribers: list[asyncio.Queue] = []
        self._current_session_id: str | None = None

    async def _get_conn(self) -> aiosqlite.Connection:
        """Get database connection."""
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        if not self._initialized:
            await conn.executescript(TELEMETRY_SCHEMA)
            await conn.commit()
            self._initialized = True
        return conn

    async def start_session(self) -> str:
        """Start a new session, return session ID."""
        session_id = str(uuid4())[:8]
        self._current_session_id = session_id

        await self._emit(TelemetryEvent(
            id=str(uuid4()),
            timestamp=datetime.now(UTC),
            session_id=session_id,
            event_type=EventType.SESSION_START,
            payload={},
        ))

        conn = await self._get_conn()
        try:
            await conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (session_id, datetime.now(UTC).isoformat()),
            )
            await conn.commit()
        finally:
            await conn.close()

        return session_id

    async def end_session(self, session_id: str) -> None:
        """End a session."""
        await self._emit(TelemetryEvent(
            id=str(uuid4()),
            timestamp=datetime.now(UTC),
            session_id=session_id,
            event_type=EventType.SESSION_END,
            payload={},
        ))

        conn = await self._get_conn()
        try:
            await conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), session_id),
            )
            await conn.commit()
        finally:
            await conn.close()

    @property
    def session_id(self) -> str:
        """Get current session ID, creating one if needed."""
        if self._current_session_id is None:
            # Synchronous fallback - just generate an ID
            self._current_session_id = str(uuid4())[:8]
        return self._current_session_id

    async def log_query(
        self,
        query_text: str,
        source: str = "tool",
    ) -> str:
        """Log a query event, return event ID for correlation."""
        event_id = str(uuid4())

        await self._emit(TelemetryEvent(
            id=event_id,
            timestamp=datetime.now(UTC),
            session_id=self.session_id,
            event_type=EventType.QUERY,
            payload={
                "query_text": query_text,
                "source": source,
            },
        ))

        return event_id

    async def log_retrieve(
        self,
        query_id: str,
        lesson_ids: list[str],
        scores: list[float],
        latency_ms: float,
    ) -> None:
        """Log lessons retrieved for a query."""
        await self._emit(TelemetryEvent(
            id=str(uuid4()),
            timestamp=datetime.now(UTC),
            session_id=self.session_id,
            event_type=EventType.RETRIEVE,
            payload={
                "query_id": query_id,
                "lesson_ids": lesson_ids,
                "scores": scores,
                "latency_ms": latency_ms,
            },
        ))

        # Update lesson stats
        await self._update_lesson_stats(lesson_ids, scores)

        # Update session stats
        await self._update_session_stats(lesson_ids)

    async def log_spider(
        self,
        start_id: str,
        depth: int,
        visited_ids: list[str],
        paths: list[list[str]],
    ) -> None:
        """Log a graph traversal."""
        await self._emit(TelemetryEvent(
            id=str(uuid4()),
            timestamp=datetime.now(UTC),
            session_id=self.session_id,
            event_type=EventType.SPIDER,
            payload={
                "start_lesson_id": start_id,
                "depth": depth,
                "visited_ids": visited_ids,
                "paths": paths,
            },
        ))

    async def log_add(self, lesson_id: str, trigger: str) -> None:
        """Log a new lesson being added."""
        await self._emit(TelemetryEvent(
            id=str(uuid4()),
            timestamp=datetime.now(UTC),
            session_id=self.session_id,
            event_type=EventType.ADD,
            payload={
                "lesson_id": lesson_id,
                "trigger": trigger,
            },
        ))

    async def log_refine(
        self,
        lesson_id: str,
        old_version: int,
        new_version: int,
        refinement: str,
    ) -> None:
        """Log a lesson refinement."""
        await self._emit(TelemetryEvent(
            id=str(uuid4()),
            timestamp=datetime.now(UTC),
            session_id=self.session_id,
            event_type=EventType.REFINE,
            payload={
                "lesson_id": lesson_id,
                "old_version": old_version,
                "new_version": new_version,
                "refinement": refinement,
            },
        ))

    async def _emit(self, event: TelemetryEvent) -> None:
        """Persist event and notify subscribers."""
        conn = await self._get_conn()
        try:
            await conn.execute(
                "INSERT INTO events (id, timestamp, session_id, event_type, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    event.id,
                    event.timestamp.isoformat(),
                    event.session_id,
                    event.event_type.value,
                    json.dumps(event.payload),
                ),
            )
            await conn.commit()
        finally:
            await conn.close()

        # Notify real-time subscribers
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Drop if queue is full

    async def _update_lesson_stats(
        self,
        lesson_ids: list[str],
        scores: list[float],
    ) -> None:
        """Update aggregate lesson statistics."""
        conn = await self._get_conn()
        try:
            now = datetime.now(UTC).isoformat()
            for lesson_id, score in zip(lesson_ids, scores):
                await conn.execute(
                    """
                    INSERT INTO lesson_stats (lesson_id, total_retrievals, last_retrieved, total_score, retrieval_count)
                    VALUES (?, 1, ?, ?, 1)
                    ON CONFLICT(lesson_id) DO UPDATE SET
                        total_retrievals = total_retrievals + 1,
                        last_retrieved = ?,
                        total_score = total_score + ?,
                        retrieval_count = retrieval_count + 1
                    """,
                    (lesson_id, now, score, now, score),
                )
            await conn.commit()
        finally:
            await conn.close()

    async def _update_session_stats(self, lesson_ids: list[str]) -> None:
        """Update session statistics."""
        conn = await self._get_conn()
        try:
            # Get current unique lessons
            cursor = await conn.execute(
                "SELECT unique_lessons FROM sessions WHERE id = ?",
                (self.session_id,),
            )
            row = await cursor.fetchone()
            if row:
                existing = set(json.loads(row["unique_lessons"]))
                existing.update(lesson_ids)

                await conn.execute(
                    """
                    UPDATE sessions SET
                        query_count = query_count + 1,
                        lessons_retrieved = lessons_retrieved + ?,
                        unique_lessons = ?
                    WHERE id = ?
                    """,
                    (len(lesson_ids), json.dumps(list(existing)), self.session_id),
                )
                await conn.commit()
        finally:
            await conn.close()

    def subscribe(self) -> asyncio.Queue:
        """Subscribe to real-time events."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Unsubscribe from events."""
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    # Analytics queries

    async def get_lesson_usage(self) -> list[dict]:
        """Get usage statistics for all lessons."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT
                    lesson_id,
                    total_retrievals,
                    last_retrieved,
                    CASE WHEN retrieval_count > 0
                         THEN total_score / retrieval_count
                         ELSE 0 END as avg_score
                FROM lesson_stats
                ORDER BY total_retrievals DESC
                """
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await conn.close()

    async def get_session_history(self, limit: int = 20) -> list[dict]:
        """Get recent session history."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT * FROM sessions
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await conn.close()

    async def get_session_events(self, session_id: str) -> list[dict]:
        """Get all events for a session."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT * FROM events
                WHERE session_id = ?
                ORDER BY timestamp
                """,
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    **dict(row),
                    "payload": json.loads(row["payload"]),
                }
                for row in rows
            ]
        finally:
            await conn.close()

    async def get_common_queries(self, limit: int = 20) -> list[dict]:
        """Get most common queries."""
        conn = await self._get_conn()
        try:
            cursor = await conn.execute(
                """
                SELECT
                    json_extract(payload, '$.query_text') as query_text,
                    COUNT(*) as frequency
                FROM events
                WHERE event_type = 'query'
                GROUP BY query_text
                ORDER BY frequency DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            await conn.close()
