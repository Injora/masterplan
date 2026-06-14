"""
YouTube Shorts Factory — Database Layer
========================================
Async SQLite via aiosqlite.  All tables are created on first run.
Provides typed CRUD helpers consumed by routers and pipeline modules.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "shorts.db"

# Ensure runtime dirs exist on import
for _sub in ("downloads", "outputs", "audio", "credentials"):
    (DATA_DIR / _sub).mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════
#  Schema
# ══════════════════════════════════════════════════════════════════

_SCHEMA_SQL = """
-- Videos already used by the Clipper (deduplication)
CREATE TABLE IF NOT EXISTS source_videos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_id      TEXT    NOT NULL UNIQUE,
    channel_name    TEXT    NOT NULL DEFAULT '',
    title           TEXT    NOT NULL DEFAULT '',
    url             TEXT    NOT NULL DEFAULT '',
    niche           TEXT    NOT NULL DEFAULT '',
    used_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Individual clips cut from source videos
CREATE TABLE IF NOT EXISTS clips (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_video_id INTEGER NOT NULL REFERENCES source_videos(id),
    start_time      REAL    NOT NULL,
    end_time        REAL    NOT NULL,
    output_path     TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'pending'
                        CHECK(status IN (
                            'pending','processing','ready',
                            'uploading','uploaded','error'
                        )),
    title           TEXT    NOT NULL DEFAULT '',
    description     TEXT    NOT NULL DEFAULT '',
    tags            TEXT    NOT NULL DEFAULT '[]',
    error_message   TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- AI-generated story videos
CREATE TABLE IF NOT EXISTS stories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    theme           TEXT    NOT NULL DEFAULT '',
    prompt          TEXT    NOT NULL DEFAULT '',
    script          TEXT    NOT NULL DEFAULT '',
    audio_path      TEXT    NOT NULL DEFAULT '',
    output_path     TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'pending'
                        CHECK(status IN (
                            'pending','processing','ready',
                            'uploading','uploaded','error'
                        )),
    title           TEXT    NOT NULL DEFAULT '',
    description     TEXT    NOT NULL DEFAULT '',
    tags            TEXT    NOT NULL DEFAULT '[]',
    error_message   TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Connected YouTube channels
CREATE TABLE IF NOT EXISTS channels (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL DEFAULT '',
    channel_id      TEXT    NOT NULL UNIQUE,
    target_niche    TEXT    NOT NULL DEFAULT '',
    credentials_path TEXT   NOT NULL DEFAULT '',
    is_active       INTEGER NOT NULL DEFAULT 1,
    daily_uploads   INTEGER NOT NULL DEFAULT 0,
    last_reset_date TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Per-channel schedule slots
CREATE TABLE IF NOT EXISTS schedule_slots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id      INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    day_of_week     INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
    time_utc        TEXT    NOT NULL,
    content_type    TEXT    NOT NULL DEFAULT 'clip'
                        CHECK(content_type IN ('clip','story')),
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Upload history / audit trail
CREATE TABLE IF NOT EXISTS upload_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id      INTEGER NOT NULL,
    content_type    TEXT    NOT NULL CHECK(content_type IN ('clip','story')),
    channel_id      INTEGER NOT NULL REFERENCES channels(id),
    youtube_video_id TEXT   NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','uploading','success','error')),
    error_message   TEXT    NOT NULL DEFAULT '',
    uploaded_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Gemini API key health tracking
CREATE TABLE IF NOT EXISTS api_key_usage (
    key_index       INTEGER PRIMARY KEY,
    label           TEXT    NOT NULL DEFAULT '',
    total_requests  INTEGER NOT NULL DEFAULT 0,
    total_failures  INTEGER NOT NULL DEFAULT 0,
    last_429_at     TEXT    NOT NULL DEFAULT '',
    last_success_at TEXT    NOT NULL DEFAULT '',
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Processed sources to avoid reusing topics or videos
CREATE TABLE IF NOT EXISTS processed_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL, -- 'ai_story_topic' or 'youtube_video_id'
    identifier TEXT UNIQUE NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_source_videos_youtube_id
    ON source_videos(youtube_id);
CREATE INDEX IF NOT EXISTS idx_clips_status
    ON clips(status);
CREATE INDEX IF NOT EXISTS idx_stories_status
    ON stories(status);
CREATE INDEX IF NOT EXISTS idx_upload_log_channel
    ON upload_log(channel_id, status);
CREATE INDEX IF NOT EXISTS idx_schedule_slots_channel
    ON schedule_slots(channel_id, is_active);
"""


# ══════════════════════════════════════════════════════════════════
#  Connection management
# ══════════════════════════════════════════════════════════════════

_db_lock = asyncio.Lock()


async def _get_conn() -> aiosqlite.Connection:
    """Open a connection with WAL mode + foreign keys enabled."""
    conn = await aiosqlite.connect(str(DB_PATH))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def init_db() -> None:
    """Create all tables and indexes.  Safe to call repeatedly."""
    async with _db_lock:
        conn = await _get_conn()
        try:
            await conn.executescript(_SCHEMA_SQL)
            await conn.executescript(_INDEXES_SQL)
            
            # Safely migrate existing databases to add target_niche if missing
            cursor = await conn.execute("PRAGMA table_info(channels)")
            columns = await cursor.fetchall()
            has_niche = any(col["name"] == "target_niche" for col in columns)
            if not has_niche:
                logger.info("Adding target_niche column to channels table...")
                await conn.execute("ALTER TABLE channels ADD COLUMN target_niche TEXT NOT NULL DEFAULT ''")
                await conn.commit()
                
            await conn.commit()
            logger.info("Database initialised at %s", DB_PATH)
        finally:
            await conn.close()


# ══════════════════════════════════════════════════════════════════
#  Generic helpers
# ══════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _rows_to_list(rows: list[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════
#  Source Videos (deduplication)
# ══════════════════════════════════════════════════════════════════

async def is_video_used(youtube_id: str) -> bool:
    """Return True if this YouTube video ID has already been clipped."""
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            "SELECT 1 FROM source_videos WHERE youtube_id = ?",
            (youtube_id,),
        )
        return (await cursor.fetchone()) is not None
    finally:
        await conn.close()


async def mark_video_used(
    youtube_id: str,
    *,
    channel_name: str = "",
    title: str = "",
    url: str = "",
    niche: str = "",
) -> int:
    """Insert a source video record and return its row id.
    If the video already exists, return the existing row id.
    """
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            "SELECT id FROM source_videos WHERE youtube_id = ?",
            (youtube_id,),
        )
        row = await cursor.fetchone()
        if row:
            return row[0]

        cursor = await conn.execute(
            """INSERT INTO source_videos
                   (youtube_id, channel_name, title, url, niche)
               VALUES (?, ?, ?, ?, ?)""",
            (youtube_id, channel_name, title, url, niche),
        )
        await conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]
    finally:
        await conn.close()


async def list_source_videos(
    limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM source_videos ORDER BY used_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return _rows_to_list(await cursor.fetchall())
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════
#  Clips
# ══════════════════════════════════════════════════════════════════

async def create_clip(
    source_video_id: int,
    start_time: float,
    end_time: float,
    *,
    output_path: str = "",
    title: str = "",
    description: str = "",
    tags: list[str] | None = None,
) -> int:
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            """INSERT INTO clips
                   (source_video_id, start_time, end_time,
                    output_path, title, description, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                source_video_id,
                start_time,
                end_time,
                output_path,
                title,
                description,
                json.dumps(tags or []),
            ),
        )
        await conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]
    finally:
        await conn.close()


async def get_clip(clip_id: int) -> dict[str, Any] | None:
    conn = await _get_conn()
    try:
        cursor = await conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,))
        return _row_to_dict(await cursor.fetchone())
    finally:
        await conn.close()


async def list_clips(
    status: str | None = None, limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    conn = await _get_conn()
    try:
        if status:
            cursor = await conn.execute(
                "SELECT * FROM clips WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM clips ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return _rows_to_list(await cursor.fetchall())
    finally:
        await conn.close()


async def update_clip(clip_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now_iso()
    if "tags" in fields and isinstance(fields["tags"], list):
        fields["tags"] = json.dumps(fields["tags"])
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = await _get_conn()
    try:
        await conn.execute(
            f"UPDATE clips SET {set_clause} WHERE id = ?",
            (*fields.values(), clip_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def delete_clip(clip_id: int) -> bool:
    conn = await _get_conn()
    try:
        cursor = await conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════
#  Stories
# ══════════════════════════════════════════════════════════════════

async def create_story(
    theme: str,
    *,
    prompt: str = "",
    script: str = "",
    audio_path: str = "",
    output_path: str = "",
    title: str = "",
    description: str = "",
    tags: list[str] | None = None,
) -> int:
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            """INSERT INTO stories
                   (theme, prompt, script, audio_path, output_path,
                    title, description, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                theme,
                prompt,
                script,
                audio_path,
                output_path,
                title,
                description,
                json.dumps(tags or []),
            ),
        )
        await conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]
    finally:
        await conn.close()


async def get_story(story_id: int) -> dict[str, Any] | None:
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM stories WHERE id = ?", (story_id,)
        )
        return _row_to_dict(await cursor.fetchone())
    finally:
        await conn.close()


async def list_stories(
    status: str | None = None, limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    conn = await _get_conn()
    try:
        if status:
            cursor = await conn.execute(
                "SELECT * FROM stories WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM stories ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return _rows_to_list(await cursor.fetchall())
    finally:
        await conn.close()


async def update_story(story_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now_iso()
    if "tags" in fields and isinstance(fields["tags"], list):
        fields["tags"] = json.dumps(fields["tags"])
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = await _get_conn()
    try:
        await conn.execute(
            f"UPDATE stories SET {set_clause} WHERE id = ?",
            (*fields.values(), story_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def delete_story(story_id: int) -> bool:
    conn = await _get_conn()
    try:
        cursor = await conn.execute("DELETE FROM stories WHERE id = ?", (story_id,))
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════
#  Channels
# ══════════════════════════════════════════════════════════════════

async def create_channel(
    name: str,
    target_niche: str = "",
    channel_id: str | None = None,
    credentials_path: str = "",
) -> int:
    if not channel_id:
        import time
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9]', '', name).lower()
        channel_id = f"UC_dummy_{safe_name}_{int(time.time())}"
        
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            """INSERT INTO channels (name, channel_id, target_niche, credentials_path, last_reset_date)
               VALUES (?, ?, ?, ?, ?)""",
            (name, channel_id, target_niche, credentials_path, _now_iso()[:10]),
        )
        await conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]
    finally:
        await conn.close()


async def get_channel(channel_db_id: int) -> dict[str, Any] | None:
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM channels WHERE id = ?", (channel_db_id,)
        )
        return _row_to_dict(await cursor.fetchone())
    finally:
        await conn.close()


async def list_channels(active_only: bool = False) -> list[dict[str, Any]]:
    conn = await _get_conn()
    try:
        if active_only:
            cursor = await conn.execute(
                "SELECT * FROM channels WHERE is_active = 1 ORDER BY created_at"
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM channels ORDER BY created_at"
            )
        return _rows_to_list(await cursor.fetchall())
    finally:
        await conn.close()


async def update_channel(channel_db_id: int, **fields: Any) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = await _get_conn()
    try:
        await conn.execute(
            f"UPDATE channels SET {set_clause} WHERE id = ?",
            (*fields.values(), channel_db_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def delete_channel(channel_db_id: int) -> bool:
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            "DELETE FROM channels WHERE id = ?", (channel_db_id,)
        )
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()


async def increment_daily_upload(channel_db_id: int) -> int:
    """Increment daily upload counter; resets if the date has changed.
    Returns the NEW count after incrementing."""
    today = _now_iso()[:10]
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            "SELECT daily_uploads, last_reset_date FROM channels WHERE id = ?",
            (channel_db_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"Channel {channel_db_id} not found")

        if row["last_reset_date"] != today:
            new_count = 1
            await conn.execute(
                "UPDATE channels SET daily_uploads = 1, last_reset_date = ? WHERE id = ?",
                (today, channel_db_id),
            )
        else:
            new_count = row["daily_uploads"] + 1
            await conn.execute(
                "UPDATE channels SET daily_uploads = ? WHERE id = ?",
                (new_count, channel_db_id),
            )
        await conn.commit()
        return new_count
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════
#  Schedule Slots
# ══════════════════════════════════════════════════════════════════

async def create_schedule_slot(
    channel_id: int,
    day_of_week: int,
    time_utc: str,
    content_type: str = "clip",
) -> int:
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            """INSERT INTO schedule_slots
                   (channel_id, day_of_week, time_utc, content_type)
               VALUES (?, ?, ?, ?)""",
            (channel_id, day_of_week, time_utc, content_type),
        )
        await conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]
    finally:
        await conn.close()


async def list_schedule_slots(
    channel_id: int | None = None, active_only: bool = True
) -> list[dict[str, Any]]:
    conn = await _get_conn()
    try:
        conditions: list[str] = []
        params: list[Any] = []
        if channel_id is not None:
            conditions.append("channel_id = ?")
            params.append(channel_id)
        if active_only:
            conditions.append("is_active = 1")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor = await conn.execute(
            f"SELECT * FROM schedule_slots {where} ORDER BY day_of_week, time_utc",
            params,
        )
        return _rows_to_list(await cursor.fetchall())
    finally:
        await conn.close()


async def update_schedule_slot(slot_id: int, **fields: Any) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = await _get_conn()
    try:
        await conn.execute(
            f"UPDATE schedule_slots SET {set_clause} WHERE id = ?",
            (*fields.values(), slot_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def delete_schedule_slot(slot_id: int) -> bool:
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            "DELETE FROM schedule_slots WHERE id = ?", (slot_id,)
        )
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════
#  Upload Log
# ══════════════════════════════════════════════════════════════════

async def create_upload_log(
    content_id: int,
    content_type: str,
    channel_id: int,
) -> int:
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            """INSERT INTO upload_log (content_id, content_type, channel_id)
               VALUES (?, ?, ?)""",
            (content_id, content_type, channel_id),
        )
        await conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]
    finally:
        await conn.close()


async def update_upload_log(log_id: int, **fields: Any) -> None:
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = await _get_conn()
    try:
        await conn.execute(
            f"UPDATE upload_log SET {set_clause} WHERE id = ?",
            (*fields.values(), log_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def list_upload_logs(
    channel_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    conn = await _get_conn()
    try:
        conditions: list[str] = []
        params: list[Any] = []
        if channel_id is not None:
            conditions.append("channel_id = ?")
            params.append(channel_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])
        cursor = await conn.execute(
            f"SELECT * FROM upload_log {where} ORDER BY uploaded_at DESC LIMIT ? OFFSET ?",
            params,
        )
        return _rows_to_list(await cursor.fetchall())
    finally:
        await conn.close()


async def get_pending_uploads(
    content_type: str, limit: int = 5
) -> list[dict[str, Any]]:
    """Return the oldest 'ready' items of the given type, suitable for uploading."""
    table = "clips" if content_type == "clip" else "stories"
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            f"SELECT * FROM {table} WHERE status = 'ready' ORDER BY created_at ASC LIMIT ?",
            (limit,),
        )
        return _rows_to_list(await cursor.fetchall())
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════
#  API Key Usage
# ══════════════════════════════════════════════════════════════════

async def init_api_key_slots(count: int = 4) -> None:
    """Ensure rows exist for each key index (0..count-1)."""
    conn = await _get_conn()
    try:
        for i in range(count):
            await conn.execute(
                """INSERT OR IGNORE INTO api_key_usage (key_index, label)
                   VALUES (?, ?)""",
                (i, f"Key {i + 1}"),
            )
        await conn.commit()
    finally:
        await conn.close()


async def record_api_key_request(key_index: int, *, success: bool) -> None:
    now = _now_iso()
    conn = await _get_conn()
    try:
        if success:
            await conn.execute(
                """UPDATE api_key_usage
                   SET total_requests = total_requests + 1,
                       last_success_at = ?,
                       updated_at = ?
                   WHERE key_index = ?""",
                (now, now, key_index),
            )
        else:
            await conn.execute(
                """UPDATE api_key_usage
                   SET total_requests = total_requests + 1,
                       total_failures = total_failures + 1,
                       last_429_at = ?,
                       updated_at = ?
                   WHERE key_index = ?""",
                (now, now, key_index),
            )
        await conn.commit()
    finally:
        await conn.close()


async def list_api_key_usage() -> list[dict[str, Any]]:
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM api_key_usage ORDER BY key_index"
        )
        return _rows_to_list(await cursor.fetchall())
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════
#  Aggregate stats (for dashboard)
# ══════════════════════════════════════════════════════════════════

async def get_dashboard_stats() -> dict[str, Any]:
    """Return counts consumed by the frontend dashboard."""
    conn = await _get_conn()
    try:
        stats: dict[str, Any] = {}
        for table, key in [("clips", "clips"), ("stories", "stories")]:
            cursor = await conn.execute(f"SELECT COUNT(*) as total FROM {table}")
            row = await cursor.fetchone()
            stats[f"total_{key}"] = row["total"] if row else 0

            for s in ("pending", "processing", "ready", "uploaded", "error"):
                cursor = await conn.execute(
                    f"SELECT COUNT(*) as c FROM {table} WHERE status = ?", (s,)
                )
                row = await cursor.fetchone()
                stats[f"{key}_{s}"] = row["c"] if row else 0

        cursor = await conn.execute(
            "SELECT COUNT(*) as c FROM channels WHERE is_active = 1"
        )
        row = await cursor.fetchone()
        stats["active_channels"] = row["c"] if row else 0

        cursor = await conn.execute(
            "SELECT COUNT(*) as c FROM upload_log WHERE status = 'success'"
        )
        row = await cursor.fetchone()
        stats["total_uploads"] = row["c"] if row else 0

        return stats
    finally:
        await conn.close()


# ══════════════════════════════════════════════════════════════════
#  Processed Sources Tracking
# ══════════════════════════════════════════════════════════════════

async def is_source_processed(identifier: str) -> bool:
    """Check if a source identifier (AI topic or YouTube Video ID) has been processed."""
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            "SELECT 1 FROM processed_sources WHERE identifier = ?",
            (identifier,),
        )
        row = await cursor.fetchone()
        return row is not None
    finally:
        await conn.close()


async def mark_source_processed(source_type: str, identifier: str) -> None:
    """Mark a source identifier as processed."""
    conn = await _get_conn()
    try:
        await conn.execute(
            """INSERT OR IGNORE INTO processed_sources (source_type, identifier)
               VALUES (?, ?)""",
            (source_type, identifier),
        )
        await conn.commit()
    finally:
        await conn.close()
