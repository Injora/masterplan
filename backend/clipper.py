"""
YouTube Shorts Factory — Viral Clipper Pipeline
=================================================
Automated discovery → analysis → extraction → metadata pipeline.

Public Functions
----------------
search_trending_videos    — yt-dlp niche search
filter_unused_videos      — SQLite dedup against source_videos
analyze_video_for_virality— Gemini transcript analysis → viral segments
process_viral_clips       — Download + crop + metadata + DB insert
run_clipper_pipeline      — Full end-to-end convenience wrapper
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

import db
from gemini_rotator import gemini, AllKeysExhaustedError
from video_engine import (
    DOWNLOADS_DIR,
    OUTPUTS_DIR,
    VideoProcessingError,
    TranscriptNotFoundError,
    download_video,
    extract_and_crop_clip,
    extract_youtube_id,
    get_transcript,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
#  Pydantic schemas (Gemini structured output)
# ══════════════════════════════════════════════════════════════════

class ViralSegment(BaseModel):
    """A single high-retention clip candidate identified by Gemini."""

    start_time: float = Field(description="Start timestamp in seconds")
    end_time: float = Field(description="End timestamp in seconds")
    hook_reason: str = Field(
        description="Why this segment would go viral — the hook or tension point"
    )
    virality_score: int = Field(
        ge=1, le=10,
        description="Predicted virality on a 1-10 scale (10 = highest)"
    )
    proposed_title: str = Field(
        description="A click-worthy YouTube Shorts title (max 70 chars)"
    )


class ViralAnalysisResult(BaseModel):
    """Gemini's analysis of a video transcript for viral-worthy moments."""

    segments: list[ViralSegment] = Field(
        description="Ranked list of the best clip candidates"
    )


class ClipMetadata(BaseModel):
    """Optimised title / description / tags for a YouTube Short."""

    title: str = Field(
        description="CTR-optimised title including #Shorts (max 70 chars)"
    )
    description: str = Field(
        description="SEO-rich description, 2-3 sentences with hashtags"
    )
    tags: list[str] = Field(
        description="15-20 discovery tags for YouTube search"
    )


# ══════════════════════════════════════════════════════════════════
#  Niche → search query mapping
# ══════════════════════════════════════════════════════════════════

_NICHE_QUERIES: dict[str, str] = {
    "gaming": "gaming highlights epic moments",
    "tech": "tech review trending 2025 best gadgets",
    "sports": "sports highlights incredible moments",
    "comedy": "comedy stand-up funny viral moments",
    "education": "educational interesting facts explained",
    "science": "science explained mind-blowing",
    "news": "trending news stories this week",
    "finance": "finance investing tips viral",
    "fitness": "fitness workout motivation transformation",
    "cooking": "cooking recipe viral food hack",
    "music": "music performance amazing talent",
    "animals": "animals funny cute viral moments",
}


def _build_search_query(niche: str) -> str:
    """Turn a niche keyword into a YouTube search query."""
    return _NICHE_QUERIES.get(niche.lower(), f"{niche} viral moments trending")


# ══════════════════════════════════════════════════════════════════
#  1. Search for trending videos
# ══════════════════════════════════════════════════════════════════

async def search_trending_videos(
    niche: str,
    count: int = 5,
) -> list[dict[str, Any]]:
    """Search YouTube for trending videos in a given niche via yt-dlp.

    Parameters
    ----------
    niche : str
        A keyword like ``"gaming"``, ``"tech"``, or any free-form topic.
    count : int
        Number of results to return (max ~20 for yt-dlp search).

    Returns
    -------
    list[dict]
        Each dict contains ``youtube_id``, ``title``, ``url``, ``channel``,
        ``duration``, and ``view_count``.
    """
    import yt_dlp

    query = _build_search_query(niche)
    logger.info("🔍  Searching YouTube: '%s'  (niche=%s, count=%d)", query, niche, count)

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "force_generic_extractor": False,
        "socket_timeout": 15,
    }

    def _search() -> list[dict[str, Any]]:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(
                f"ytsearch{count}:{query}", download=False
            )
            entries = result.get("entries", []) if result else []

            videos: list[dict[str, Any]] = []
            for entry in entries:
                if not entry or not entry.get("id"):
                    continue
                vid_id = entry["id"]
                videos.append({
                    "youtube_id": vid_id,
                    "title": entry.get("title", ""),
                    "url": entry.get(
                        "url",
                        f"https://www.youtube.com/watch?v={vid_id}",
                    ),
                    "channel": entry.get(
                        "channel", entry.get("uploader", "Unknown")
                    ),
                    "duration": entry.get("duration") or 0,
                    "view_count": entry.get("view_count") or 0,
                })

            return videos

    videos = await asyncio.to_thread(_search)
    logger.info(
        "🔍  Found %d videos for niche '%s'", len(videos), niche
    )
    for v in videos:
        logger.debug(
            "   • [%s] %s  (%ds, %s views)",
            v["youtube_id"],
            v["title"][:60],
            v["duration"],
            f'{v["view_count"]:,}' if v["view_count"] else "?",
        )
    return videos


# ══════════════════════════════════════════════════════════════════
#  2. Deduplication filter
# ══════════════════════════════════════════════════════════════════

async def filter_unused_videos(
    video_list: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove videos whose YouTube IDs already exist in ``source_videos``.

    Returns only the videos that have NOT been processed before.
    """
    original_count = len(video_list)
    fresh: list[dict[str, Any]] = []

    for video in video_list:
        yt_id = video.get("youtube_id", "")
        if not yt_id:
            continue
        if await db.is_source_processed(yt_id):
            logger.debug("   ↳ SKIP (already used): %s", yt_id)
        else:
            fresh.append(video)

    skipped = original_count - len(fresh)
    logger.info(
        "🗂  Dedup filter: %d/%d videos are new  (%d already used)",
        len(fresh), original_count, skipped,
    )
    return fresh


# ══════════════════════════════════════════════════════════════════
#  3. Gemini-powered virality analysis
# ══════════════════════════════════════════════════════════════════

_VIRALITY_SYSTEM_PROMPT = """\
You are an elite YouTube Shorts content strategist who has generated over \
500 million views.  Your expertise is identifying the *exact* 30-60 second \
moments in a video that will explode as YouTube Shorts.

Rules:
• Each segment MUST be between 30 and 60 seconds.
• Prefer segments that begin with a strong hook (question, bold claim, \
  or emotional peak) within the first 2 seconds.
• Favour moments of surprise, humour, controversy, or profound insight.
• Avoid intros, outros, sponsor reads, or low-energy filler.
• The proposed_title must be concise, curiosity-driven, and include \
  an emoji.  Max 70 characters.
• virality_score: 1 = mildly interesting, 10 = guaranteed viral.
• Return exactly 5 segments, sorted by virality_score descending.\
"""


async def analyze_video_for_virality(
    youtube_id: str,
) -> list[dict[str, Any]]:
    """Fetch a video's transcript and ask Gemini to find viral segments.

    Parameters
    ----------
    youtube_id : str
        The 11-character YouTube video ID.

    Returns
    -------
    list[dict]
        Sorted list of ``ViralSegment``-shaped dicts (highest score first).

    Raises
    ------
    TranscriptNotFoundError
        If the video has no available transcript.
    AllKeysExhaustedError
        If Gemini cannot be reached on any key.
    """
    logger.info("🧠  Analyzing video %s for viral segments …", youtube_id)

    # 1. Get transcript
    transcript_segments = await get_transcript(youtube_id)
    transcript_text = "\n".join(
        f"[{seg['start']:.1f}s] {seg['text']}" for seg in transcript_segments
    )
    total_duration = sum(s["duration"] for s in transcript_segments)
    logger.info(
        "🧠  Transcript loaded: %d segments, %.0f s total",
        len(transcript_segments), total_duration,
    )

    # 2. Build prompt
    user_prompt = (
        f"Analyze the following video transcript ({total_duration:.0f} seconds "
        f"total) and identify the best viral clip candidates.\n\n"
        f"TRANSCRIPT:\n{transcript_text}"
    )

    # 3. Call Gemini with structured output
    result = await gemini.generate_json(
        user_prompt,
        schema=ViralAnalysisResult,
        system_instruction=_VIRALITY_SYSTEM_PROMPT,
        temperature=0.5,
    )

    segments = result.get("segments", [])

    # 4. Post-process: clamp times, sort by score
    cleaned: list[dict[str, Any]] = []
    for seg in segments:
        start = max(0.0, float(seg.get("start_time", 0)))
        end = min(total_duration, float(seg.get("end_time", start + 45)))
        duration = end - start

        # Enforce 30-60s constraint
        if duration < 25:
            logger.debug("   ↳ Skipping segment (too short: %.1fs)", duration)
            continue
        if duration > 65:
            end = start + 60  # trim to 60s max

        cleaned.append({
            "start_time": round(start, 2),
            "end_time": round(end, 2),
            "duration": round(end - start, 2),
            "hook_reason": seg.get("hook_reason", ""),
            "virality_score": int(seg.get("virality_score", 5)),
            "proposed_title": seg.get("proposed_title", ""),
        })

    # Sort by virality score descending
    cleaned.sort(key=lambda s: s["virality_score"], reverse=True)

    logger.info(
        "🧠  Analysis complete: %d viral segments identified", len(cleaned)
    )
    for i, seg in enumerate(cleaned, 1):
        logger.info(
            "   #%d  [%4.1f–%4.1fs]  score=%d  '%s'",
            i,
            seg["start_time"],
            seg["end_time"],
            seg["virality_score"],
            seg["proposed_title"][:50],
        )

    return cleaned


# ══════════════════════════════════════════════════════════════════
#  4. Process & render clips
# ══════════════════════════════════════════════════════════════════

_METADATA_SYSTEM_PROMPT = """\
You are a YouTube SEO expert.  Generate metadata that maximises \
click-through rate and search discoverability for YouTube Shorts.

Rules for title:
• Max 70 characters.  Include #Shorts at the end.
• Use curiosity gaps, power words, or emotion triggers.
• Include one relevant emoji at the start.

Rules for description:
• 2-3 punchy sentences summarising the clip's value.
• End with 5-8 hashtags (mix broad + niche).

Rules for tags:
• 15-20 single-word or short-phrase tags.
• Mix high-volume and long-tail keywords.\
"""


async def process_viral_clips(
    video_url: str,
    selected_segments: list[dict[str, Any]],
    *,
    niche: str = "",
) -> list[int]:
    """Download a video, extract clips, generate metadata, and queue them.

    Parameters
    ----------
    video_url : str
        Full YouTube URL.
    selected_segments : list[dict]
        Segments to extract (from :func:`analyze_video_for_virality`).
    niche : str
        Optional niche label stored in the DB for tracking.

    Returns
    -------
    list[int]
        Database IDs of the newly created clips.
    """
    if not selected_segments:
        logger.warning("process_viral_clips called with 0 segments — nothing to do")
        return []

    youtube_id = extract_youtube_id(video_url)
    if not youtube_id:
        raise ValueError(f"Cannot parse YouTube ID from: {video_url}")

    clip_ids: list[int] = []
    downloaded_path: str | None = None

    try:
        # ── 1. Download source video ─────────────────────────────
        logger.info(
            "⬇  Downloading source video %s (%d clips to extract) …",
            youtube_id, len(selected_segments),
        )
        downloaded_path = await download_video(video_url)

        # ── 2. Mark video as used ────────────────────────────────
        source_id = await db.mark_video_used(
            youtube_id,
            title=selected_segments[0].get("proposed_title", ""),
            url=video_url,
            niche=niche,
        )
        logger.info("🗂  Source video registered: id=%d", source_id)

        # ── 3. Process each segment ──────────────────────────────
        for i, seg in enumerate(selected_segments, 1):
            start = seg["start_time"]
            end = seg["end_time"]
            clip_filename = (
                f"clip_{youtube_id}_{int(start)}_{int(end)}"
                f"_{int(time.time())}.mp4"
            )
            clip_path = str(OUTPUTS_DIR / clip_filename)

            logger.info(
                "✂  Processing clip %d/%d: %.1f–%.1fs …",
                i, len(selected_segments), start, end,
            )

            try:
                # ── 3a. Extract & crop & subtitles ───────────────
                transcript_words = await _get_transcript_words(youtube_id, start, end)
                await extract_and_crop_clip(
                    downloaded_path, clip_path, start, end, transcript_words
                )

                # ── 3b. Generate SEO metadata via Gemini ─────────
                metadata = await _generate_clip_metadata(seg)

                # ── 3c. Save to database ─────────────────────────
                clip_id = await db.create_clip(
                    source_video_id=source_id,
                    start_time=start,
                    end_time=end,
                    output_path=clip_path,
                    title=metadata["title"],
                    description=metadata["description"],
                    tags=metadata["tags"],
                )
                await db.update_clip(clip_id, status="ready")

                clip_ids.append(clip_id)
                logger.info(
                    "✅  Clip %d saved: id=%d  '%s'",
                    i, clip_id, metadata["title"][:50],
                )

            except (VideoProcessingError, AllKeysExhaustedError) as exc:
                logger.error("✖  Clip %d failed: %s", i, exc)
                # Create a DB record with error status for visibility
                err_id = await db.create_clip(
                    source_video_id=source_id,
                    start_time=start,
                    end_time=end,
                    title=seg.get("proposed_title", ""),
                )
                await db.update_clip(
                    err_id,
                    status="error",
                    error_message=str(exc)[:500],
                )
                continue  # proceed with remaining clips

    finally:
        # ── Cleanup downloaded source ────────────────────────────
        if downloaded_path and os.path.isfile(downloaded_path):
            try:
                os.remove(downloaded_path)
                logger.debug("🗑  Removed source download: %s", downloaded_path)
            except OSError:
                pass

    logger.info(
        "🏁  Clip pipeline complete: %d/%d clips ready",
        len(clip_ids), len(selected_segments),
    )
    return clip_ids

async def _get_transcript_words(youtube_id: str, start_time: float, end_time: float) -> list[dict[str, Any]]:
    """Fetch transcript and format it for subtitle burning."""
    try:
        transcript = await get_transcript(youtube_id)
    except Exception as e:
        logger.warning(f"Could not fetch transcript for {youtube_id}: {e}")
        return []

    words = []
    for entry in transcript:
        entry_start = entry["start"]
        entry_end = entry_start + entry["duration"]
        
        # Check overlap
        if entry_end > start_time and entry_start < end_time:
            # Crop times to clip bounds
            eff_start = max(start_time, entry_start)
            eff_end = min(end_time, entry_end)
            
            text = entry["text"].replace("\n", " ").strip()
            if not text:
                continue
                
            # Split into words and assign proportional times
            parts = text.split()
            if not parts:
                continue
                
            duration_per_word = (eff_end - eff_start) / len(parts)
            for j, w in enumerate(parts):
                word_start = (eff_start + j * duration_per_word) - start_time
                word_end = (eff_start + (j + 1) * duration_per_word) - start_time
                words.append({
                    "text": w,
                    "start": max(0.0, word_start),
                    "end": min(end_time - start_time, word_end),
                })
                
    return words



async def _generate_clip_metadata(
    segment: dict[str, Any],
) -> dict[str, Any]:
    """Ask Gemini to create optimised YouTube metadata for a clip."""
    user_prompt = (
        f"Generate YouTube Shorts metadata for this clip:\n"
        f"• Hook / highlight: {segment.get('hook_reason', 'N/A')}\n"
        f"• Duration: {segment.get('duration', 45):.0f} seconds\n"
        f"• Proposed title idea: {segment.get('proposed_title', 'N/A')}\n"
        f"• Virality score: {segment.get('virality_score', 5)}/10"
    )

    try:
        result = await gemini.generate_json(
            user_prompt,
            schema=ClipMetadata,
            system_instruction=_METADATA_SYSTEM_PROMPT,
            temperature=0.6,
        )
        # Ensure #Shorts is in the title
        title = result.get("title", segment.get("proposed_title", "Untitled"))
        if "#Shorts" not in title and "#shorts" not in title.lower():
            title = title.rstrip() + " #Shorts"

        return {
            "title": title[:100],
            "description": result.get("description", ""),
            "tags": result.get("tags", []),
        }
    except AllKeysExhaustedError:
        logger.warning("Gemini unavailable for metadata — using fallback")
        return {
            "title": segment.get("proposed_title", "Untitled") + " #Shorts",
            "description": segment.get("hook_reason", ""),
            "tags": ["shorts", "viral", "trending"],
        }


# ══════════════════════════════════════════════════════════════════
#  5. Full auto-pipeline
# ══════════════════════════════════════════════════════════════════

async def run_clipper_pipeline(
    niche: str = "",
    *,
    url: str | None = None,
    search_count: int = 5,
    max_clips_per_video: int = 3,
    min_virality_score: int = 6,
) -> dict[str, Any]:
    """End-to-end: search → dedup → analyze → clip → queue.

    Parameters
    ----------
    niche : str
        Topic to search for (e.g. ``"gaming"``). Ignored if url is provided.
    url : str | None
        Direct YouTube URL. If provided, skips search and processes only this video.
    search_count : int
        How many videos to pull from search.
    max_clips_per_video : int
        Cap the number of clips extracted per source video.
    min_virality_score : int
        Only process segments scoring at or above this threshold.

    Returns
    -------
    dict
        Summary with keys ``videos_searched``, ``videos_analyzed``,
        ``clips_created``, ``errors``.
    """
    summary: dict[str, Any] = {
        "niche": niche,
        "videos_searched": 0,
        "videos_analyzed": 0,
        "clips_created": 0,
        "clip_ids": [],
        "processed_youtube_ids": [],
        "errors": [],
    }

    logger.info("═" * 60)
    logger.info("  CLIPPER PIPELINE START — niche: %s", niche)
    logger.info("═" * 60)

    try:
        if url:
            yt_id = extract_youtube_id(url)
            if not yt_id:
                raise ValueError(f"Invalid YouTube URL: {url}")
            videos = [{"youtube_id": yt_id, "url": url, "title": "Direct Link"}]
            summary["videos_searched"] = 1
        else:
            # 1. Search
            videos = await search_trending_videos(niche, count=search_count)
            summary["videos_searched"] = len(videos)

        # 2. Dedup (bypass if direct URL is explicitly provided)
        if url:
            fresh_videos = videos
        else:
            fresh_videos = await filter_unused_videos(videos)

        if not fresh_videos:
            logger.info("No new videos found — pipeline complete (nothing to do)")
            return summary

        # 3. Analyze & process each video
        for video in fresh_videos:
            yt_id = video["youtube_id"]
            try:
                # Analyze
                segments = await analyze_video_for_virality(yt_id)
                summary["videos_analyzed"] += 1

                # Filter by score and cap count
                qualified = [
                    s for s in segments
                    if s["virality_score"] >= min_virality_score
                ][:max_clips_per_video]

                if not qualified:
                    logger.info(
                        "   Video %s — no segments above score %d",
                        yt_id, min_virality_score,
                    )
                    continue

                # Process
                clip_ids = await process_viral_clips(
                    video["url"], qualified, niche=niche
                )
                summary["clips_created"] += len(clip_ids)
                summary["clip_ids"].extend(clip_ids)
                summary["processed_youtube_ids"].append(yt_id)

            except TranscriptNotFoundError:
                msg = f"No transcript for {yt_id}"
                logger.warning("   ⚠️  %s — skipping", msg)
                summary["errors"].append(msg)
            except AllKeysExhaustedError as exc:
                msg = f"Gemini quota exhausted during analysis of {yt_id}"
                logger.error("   ✖  %s", msg)
                summary["errors"].append(msg)
                break  # no point continuing without AI
            except Exception as exc:
                msg = f"Unexpected error on {yt_id}: {exc}"
                logger.exception("   ✖  %s", msg)
                summary["errors"].append(msg)
                continue

    except Exception as exc:
        msg = f"Pipeline-level failure: {exc}"
        logger.exception(msg)
        summary["errors"].append(msg)

    logger.info("═" * 60)
    logger.info(
        "  CLIPPER PIPELINE DONE — %d clips from %d videos",
        summary["clips_created"], summary["videos_analyzed"],
    )
    logger.info("═" * 60)

    return summary
