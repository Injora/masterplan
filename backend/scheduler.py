"""
YouTube Shorts Factory — Content Scheduler
============================================
Automated scheduling using APScheduler.
Coordinates clipping pipelines, story generation, and YouTube uploads.
"""

from __future__ import annotations

import asyncio
import logging
import os
import json
from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import db
import clipper
import story_engine
from uploader import get_youtube_client, upload_video

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = AsyncIOScheduler()

# ══════════════════════════════════════════════════════════════════
#  1. Scheduled Jobs
# ══════════════════════════════════════════════════════════════════

async def job_daily_shorts_pipeline() -> None:
    """Dual-Pipeline: Generate 1 AI Story and 5 Viral Clips, then upload to ALL active channels."""
    logger.info("⏰ [SCHEDULED] Starting Daily Dual-Pipeline Shorts generation...")
    
    # Fetch active channels
    channels = await db.list_channels(active_only=True)
    if not channels:
        logger.info("⏰ [SCHEDULED] No active channels configured — skipping daily pipeline.")
        return
        
    logger.info("⏰ [SCHEDULED] Found %d active channel(s) for dual-pipeline uploads.", len(channels))
    from gemini_rotator import gemini
    
    # =========================================================================
    # STEP 1: AI Story Generation
    # =========================================================================
    logger.info("⏰ [SCHEDULED] Starting Step 1: AI Story Generation...")
    story_prompt = (
        "Suggest 3 highly viral, globally trending topics right now. These can be related to "
        "world history, modern pop culture, incredible true stories, or major events. "
        "Return ONLY the 3 topics separated by the pipe character '|', with no extra text or numbering."
    )
    
    story_topic_selected = "The true story of the Titanic's sister ship" # Fallback
    try:
        raw_topics = await gemini.generate_content(story_prompt)
        topics = [t.strip().strip('"').strip("'") for t in raw_topics.split("|") if t.strip()]
        for t in topics:
            if not await db.is_source_processed(t):
                story_topic_selected = t
                break
    except Exception as exc:
        logger.error("🧠 [SCHEDULED] Failed to get trending topics, using fallback: %s", exc)
        
    logger.info("🧠 [SCHEDULED] Selected unique AI Story topic: '%s'", story_topic_selected)
    
    try:
        story_id = await story_engine.build_story_short(theme="world_history", custom_prompt=story_topic_selected)
        await db.mark_source_processed('ai_story_topic', story_topic_selected)
        logger.info("⏰ [SCHEDULED] Story Engine generated Story ID %d", story_id)
        
        # Queue for ALL channels
        for channel in channels:
            log_id = await db.create_upload_log(
                content_id=story_id,
                content_type="story",
                channel_id=channel["id"]
            )
            logger.info("⏰ [SCHEDULED] Queued Story ID %d for Channel ID %d (Log ID: %d)", story_id, channel["id"], log_id)
            
    except Exception as exc:
        logger.error("⏰ [SCHEDULED] Story Engine pipeline failed: %s", exc)

    # =========================================================================
    # STEP 2: Viral Video Slicing
    # =========================================================================
    logger.info("⏰ [SCHEDULED] Starting Step 2: Viral Video Slicing...")
    clipper_prompt = (
        "Pick ONE of these 3 categories at random and suggest a SPECIFIC, highly viral YouTube search query for it:\n"
        "1. SPORTS: A specific recent epic sports moment (e.g. 'UFC 305 knockout highlights', 'NBA finals clutch shots 2025', 'cricket world cup best sixes')\n"
        "2. STREAMERS: A specific popular streamer rage/funny moment (e.g. 'IShowSpeed funniest rage moments', 'xQc heated argument clips', 'Kai Cenat funny moments')\n"
        "3. CONTROVERSIAL PODCASTS: A specific heated podcast debate (e.g. 'Joe Rogan heated argument guest', 'Flagrant podcast roast best moments', 'Fresh and Fit podcast debate')\n"
        "Respond with ONLY the YouTube search query (4 to 8 words max). No extra text."
    )
    
    viral_query = "UFC knockout highlights best moments"
    try:
        raw_query = await gemini.generate_content(clipper_prompt)
        viral_query = raw_query.strip().strip('"').strip("'")
    except Exception as exc:
        logger.error("🧠 [SCHEDULED] Failed to get clipper query, using fallback: %s", exc)
        
    logger.info("🧠 [SCHEDULED] Selected viral clipper query: '%s'", viral_query)
    
    try:
        # We set max_clips_per_video=5 and min_virality_score=1 to ensure we get exactly 5 clips
        # The clipper logic already skips processed videos thanks to our db.is_source_processed addition
        result = await clipper.run_clipper_pipeline(
            niche=viral_query,
            search_count=5, # Expand search to ensure we find an unprocessed one
            max_clips_per_video=5,
            min_virality_score=1
        )
        
        created_clip_ids = result.get("clip_ids", [])
        processed_youtube_ids = result.get("processed_youtube_ids", [])
        
        for yt_id in processed_youtube_ids:
            await db.mark_source_processed('youtube_video_id', yt_id)
            
        logger.info("⏰ [SCHEDULED] Clipper completed: Created %d clip(s)", len(created_clip_ids))
        
        # Queue clips for ALL channels
        for clip_id in created_clip_ids:
            for channel in channels:
                log_id = await db.create_upload_log(
                    content_id=clip_id,
                    content_type="clip",
                    channel_id=channel["id"]
                )
                logger.info("⏰ [SCHEDULED] Queued Clip ID %d for Channel ID %d (Log ID: %d)", clip_id, channel["id"], log_id)
                
    except Exception as exc:
        logger.error("⏰ [SCHEDULED] Clipper pipeline execution failed: %s", exc)


async def job_run_clipper() -> None:
    """Scheduled task: Run a manual tech niche clipper run."""
    target_niche = "tech" 
    logger.info("⏰ [SCHEDULED] Running Clipper Pipeline for niche: %s", target_niche)
    try:
        result = await clipper.run_clipper_pipeline(
            target_niche,
            search_count=3,
            max_clips_per_video=2
        )
        logger.info("⏰ [SCHEDULED] Clipper finished: Created %d clips", result.get("clips_created", 0))
    except Exception as exc:
        logger.error("⏰ [SCHEDULED] Clipper job failed: %s", exc)


async def job_run_story_engine() -> None:
    """Scheduled task: Generate a story short."""
    target_theme = "world_history"
    logger.info("⏰ [SCHEDULED] Running Story Engine for theme: %s", target_theme)
    try:
        story_id = await story_engine.build_story_short(target_theme)
        logger.info("⏰ [SCHEDULED] Story Engine finished: Created story ID %d", story_id)
    except Exception as exc:
        logger.error("⏰ [SCHEDULED] Story Engine job failed: %s", exc)


async def job_process_upload_queue() -> None:
    """Scheduled task: Find pending upload logs and publish them to YouTube."""
    logger.info("⏰ [SCHEDULED] Checking upload queue...")
    
    query = "SELECT * FROM upload_log WHERE status = 'pending'"
    conn = await db._get_conn()
    pending_logs = []
    try:
        conn.row_factory = db.aiosqlite.Row
        cursor = await conn.execute(query)
        rows = await cursor.fetchall()
        pending_logs = [dict(row) for row in rows]
    except Exception as exc:
        logger.error("Database error fetching pending upload logs: %s", exc)
    finally:
        await conn.close()

    for upload in pending_logs:
        log_id = upload["id"]
        content_id = upload["content_id"]
        content_type = upload["content_type"]
        channel_db_id = upload["channel_id"]
        
        logger.info("📤 Processing queued upload log ID %d: %s ID %d for Channel %d", log_id, content_type, content_id, channel_db_id)
        
        # 1. Update status to uploading in upload_log
        await db.update_upload_log(log_id, status="uploading")
        
        # 2. Fetch the actual content details (clip or story)
        item = None
        if content_type == "clip":
            item = await db.get_clip(content_id)
        elif content_type == "story":
            item = await db.get_story(content_id)
            
        if not item:
            msg = f"{content_type.capitalize()} ID {content_id} not found in database"
            logger.error("✖  %s", msg)
            await db.update_upload_log(log_id, status="error", error_message=msg)
            continue
            
        file_path = item.get("output_path", "")
        title = item.get("title", "")
        description = item.get("description", "")
        
        # Parse tags
        tags = []
        if item.get("tags"):
            if isinstance(item["tags"], str):
                 try:
                     tags = json.loads(item["tags"])
                 except Exception:
                     tags = [t.strip() for t in item["tags"].split(",") if t.strip()]
            elif isinstance(item["tags"], list):
                 tags = item["tags"]
                 
        if not file_path or not os.path.exists(file_path):
            msg = f"Video file not found at: {file_path}"
            logger.error("✖  %s", msg)
            await db.update_upload_log(log_id, status="error", error_message=msg[:500])
            if content_type == "clip":
                await db.update_clip(content_id, status="error", error_message=msg[:500])
            elif content_type == "story":
                await db.update_story(content_id, status="error", error_message=msg[:500])
            continue

        # Get YouTube Client
        youtube = await get_youtube_client(channel_db_id)
        if not youtube:
             msg = f"Channel {channel_db_id} not authenticated (missing OAuth token)"
             logger.warning("   ⚠️  %s", msg)
             await db.update_upload_log(log_id, status="error", error_message=msg)
             continue

        # Perform Upload
        try:
            response = await upload_video(
                youtube,
                file_path,
                title,
                description,
                tags,
                privacy_status="public"
            )
            
            if response and response.get("id"):
                video_id = response["id"]
                url = f"https://www.youtube.com/shorts/{video_id}"
                logger.info("✅ Successfully uploaded to YouTube: %s", url)
                
                await db.update_upload_log(
                    log_id,
                    status="success",
                    youtube_video_id=video_id
                )
                
                if content_type == "clip":
                    await db.update_clip(content_id, status="uploaded")
                elif content_type == "story":
                    await db.update_story(content_id, status="uploaded")
                
                await db.increment_daily_upload(channel_db_id)
            else:
                 msg = "Upload failed: YouTube API did not return video ID"
                 logger.error("✖  %s", msg)
                 await db.update_upload_log(log_id, status="error", error_message=msg)
                 if content_type == "clip":
                     await db.update_clip(content_id, status="error", error_message=msg)
                 elif content_type == "story":
                     await db.update_story(content_id, status="error", error_message=msg)
                 
        except Exception as exc:
            msg = f"Unexpected upload exception: {exc}"
            logger.error("✖  %s", msg)
            await db.update_upload_log(log_id, status="error", error_message=msg[:500])
            if content_type == "clip":
                await db.update_clip(content_id, status="error", error_message=msg[:500])
            elif content_type == "story":
                await db.update_story(content_id, status="error", error_message=msg[:500])


# ══════════════════════════════════════════════════════════════════
#  2. Scheduler Control
# ══════════════════════════════════════════════════════════════════

def start_scheduler() -> None:
    """Initialize and start the background scheduler."""
    if scheduler.running:
        logger.warning("Scheduler is already running.")
        return

    logger.info("⏱️  Initializing background scheduler...")
    
    # 1. Daily publishing at 14:00 (2:00 PM) local time
    scheduler.add_job(
        job_daily_shorts_pipeline,
        trigger=CronTrigger(hour=14, minute=0),
        id="job_daily_shorts",
        replace_existing=True,
        max_instances=1
    )
    
    # 2. Check upload queue every 30 minutes
    scheduler.add_job(
        job_process_upload_queue, 
        trigger=IntervalTrigger(minutes=30),
        id="job_upload_queue",
        replace_existing=True,
        max_instances=1
    )

    scheduler.start()
    logger.info("⏱️  Scheduler started successfully")


def stop_scheduler() -> None:
    """Stop the background scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("⏱️  Scheduler stopped")
