"""
YouTube Shorts Factory — AI Story Engine
==========================================
Automated script generation → TTS → visuals → video assembly pipeline
for history and horror-themed YouTube Shorts.

Supported themes:
    • ``ancient_greece``  — tales from the Hellenic world
    • ``world_history``   — pivotal moments across civilisations
    • ``scary_stories``   — creepy / paranormal horror shorts

Public Functions
----------------
    generate_story_script  — Produce TTS narration and scene visual descriptions
    fetch_pexels_visuals   — Download stock images from Pexels API
    build_story_short      — Orchestrate the complete pipeline end-to-end
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

import db
from gemini_rotator import gemini, AllKeysExhaustedError
from video_engine import (
    AUDIO_DIR,
    DATA_DIR,
    OUTPUTS_DIR,
    TTSResult,
    TTS_VOICES,
    VideoProcessingError,
    assemble_story_video,
    generate_tts,
)

logger = logging.getLogger(__name__)

# ── Directories ──────────────────────────────────────────────────
IMAGES_DIR = DATA_DIR / "downloads" / "story_images"
BGM_DIR = DATA_DIR / "bgm"

for _d in (IMAGES_DIR, BGM_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════
#  Pydantic schemas (Gemini structured output)
# ══════════════════════════════════════════════════════════════════

class CaptionSegment(BaseModel):
    """A timed phrase for on-screen captions."""

    text: str = Field(description="A short phrase (5-10 words) from the script")
    order: int = Field(description="Sequential order of this phrase in the script")


class StoryOutput(BaseModel):
    """Complete story package returned by Gemini."""

    title: str = Field(
        description="A viral-worthy YouTube Shorts title (max 70 chars, include emoji)"
    )
    script: str = Field(
        description="The full narration script, 120-140 words, "
        "designed for 50 seconds of speech"
    )
    scene_descriptions: list[str] = Field(
        description="5-7 vivid image search queries for stock photos, "
        "each describing a key visual moment in the story"
    )
    tags: list[str] = Field(
        description="15-20 YouTube discovery tags"
    )
    description: str = Field(
        description="SEO-optimised YouTube description (2-3 sentences + hashtags)"
    )


# ══════════════════════════════════════════════════════════════════
#  Theme configuration
# ══════════════════════════════════════════════════════════════════

_THEME_CONFIG: dict[str, dict[str, Any]] = {
    "ancient_greece": {
        "label": "Ancient Greece",
        "voice": TTS_VOICES["ancient_greece"],
        "bgm_file": "epic_orchestral.mp3",
        "system_prompt": (
            "You are a dramatic storyteller specialising in Ancient Greek "
            "history and mythology.  Your narrations are vivid, gripping, "
            "and full of tension.  Use short punchy sentences.  Open with "
            "a powerful hook that makes the viewer unable to scroll away."
        ),
        "style_hint": (
            "Set in Ancient Greece. Include references to gods, heroes, "
            "battles, philosophy, or city-states like Athens and Sparta."
        ),
    },
    "world_history": {
        "label": "World History",
        "voice": TTS_VOICES["world_history"],
        "bgm_file": "epic_orchestral.mp3",
        "system_prompt": (
            "You are a master storyteller writing scripts identical to the 'Zack D. Films' YouTube shorts. "
            "Narrate intensely curious or shocking historical facts. "
            "Use extremely short, punchy sentences. ALWAYS open with a massive hook ('You might think...', 'Have you ever wondered...'). "
            "Build tension instantly. Speak extremely fast and energetically. End with a mind-blowing reveal. Do NOT use emojis in the script."
        ),
        "style_hint": (
            "Focus on obscure science, bizarre history, or how everyday things secretly work. "
            "Keep the script under 130 words. Keep it absolutely thrilling."
        ),
    },
    "scary_stories": {
        "label": "Scary Stories",
        "voice": TTS_VOICES["scary_stories"],
        "bgm_file": "dark_ambient.mp3",
        "system_prompt": (
            "You are a master of horror fiction who writes short, deeply "
            "unsettling campfire stories.  Build atmosphere slowly, then "
            "deliver a gut-punch twist in the final two sentences.  "
            "Use second-person ('you') to pull the viewer inside the story."
        ),
        "style_hint": (
            "Themes: paranormal encounters, abandoned places, unexplained "
            "disappearances, folklore creatures, sleep paralysis, "
            "or creepy true-crime adjacent fiction."
        ),
    },
}

AVAILABLE_THEMES = list(_THEME_CONFIG.keys())


def get_theme_config(theme: str) -> dict[str, Any]:
    """Return config for a theme, falling back to ``world_history``."""
    return _THEME_CONFIG.get(theme, _THEME_CONFIG["world_history"])


# ══════════════════════════════════════════════════════════════════
#  1. Generate story assets via Gemini
# ══════════════════════════════════════════════════════════════════

async def generate_story_assets(
    theme: str,
    custom_prompt: str | None = None,
) -> dict[str, Any]:
    """Ask Gemini to write a complete story package.

    Parameters
    ----------
    theme : str
        One of ``AVAILABLE_THEMES``.
    custom_prompt : str | None
        Optional additional creative direction appended to the prompt.

    Returns
    -------
    dict
        Keys: ``title``, ``script``, ``scene_descriptions``, ``tags``,
        ``description``.
    """
    cfg = get_theme_config(theme)

    logger.info(
        "✍️  Generating story assets  (theme=%s, label='%s')",
        theme, cfg["label"],
    )

    user_prompt = (
        f"Create a YouTube Shorts narration script with the theme: "
        f"{cfg['label']}.\n\n"
        f"Style guidance: {cfg['style_hint']}\n\n"
        f"Requirements:\n"
        f"• The script must be EXACTLY 120-140 words (optimised for ~50 "
        f"seconds of narration).\n"
        f"• Begin with an irresistible hook in the first sentence.\n"
        f"• Maintain relentless pacing — no filler.\n"
        f"• End with a surprising twist or powerful punchline.\n"
        f"• Provide 5-7 scene_descriptions — each is a concise stock-image "
        f"search query for STICKMAN or STICK FIGURE style illustrations "
        f"(e.g. 'stickman running from explosion', 'stick figure shocked "
        f"surprised face', 'stickman battle sword fight', 'stick figure "
        f"standing on mountain top') that maps to a visual transition every "
        f"7-10 seconds.\n"
        f"• The title must include one emoji and be max 70 characters.\n"
        f"• Generate 15-20 YouTube discovery tags.\n"
        f"• Write an SEO description (2-3 sentences + 5-8 hashtags)."
    )

    if custom_prompt:
        user_prompt += f"\n\nAdditional creative direction:\n{custom_prompt}"

    result = await gemini.generate_json(
        user_prompt,
        schema=StoryOutput,
        system_instruction=cfg["system_prompt"],
        temperature=0.8,
    )

    # Validate & fix script length
    script = result.get("script", "")
    word_count = len(script.split())
    logger.info(
        "✍️  Story generated: '%s'  (%d words, %d scenes)",
        result.get("title", "?")[:50],
        word_count,
        len(result.get("scene_descriptions", [])),
    )

    if word_count < 80:
        logger.warning("   ⚠️  Script is short (%d words) — may be under 40s", word_count)
    elif word_count > 180:
        logger.warning("   ⚠️  Script is long (%d words) — may exceed 60s", word_count)

    return result


# ══════════════════════════════════════════════════════════════════
#  2. Fetch stock images from Pexels
# ══════════════════════════════════════════════════════════════════

_PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"


async def fetch_pexels_visuals(
    scene_descriptions: list[str],
) -> list[str]:
    """Download one high-quality image per scene description from Pexels.

    Parameters
    ----------
    scene_descriptions : list[str]
        Search queries for each visual scene.

    Returns
    -------
    list[str]
        Absolute paths to downloaded images.  May be shorter than
        ``scene_descriptions`` if some searches fail.
    """
    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key or api_key.startswith("your_"):
        logger.warning(
            "⚠️  PEXELS_API_KEY not configured — cannot fetch stock images.  "
            "Set it in backend/.env"
        )
        return []

    image_paths: list[str] = []
    timestamp = int(time.time())

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, desc in enumerate(scene_descriptions):
            try:
                logger.info(
                    "🖼  Pexels search %d/%d: '%s'",
                    i + 1, len(scene_descriptions), desc[:60],
                )

                # Search with portrait orientation preferred
                resp = await client.get(
                    _PEXELS_SEARCH_URL,
                    params={
                        "query": desc,
                        "orientation": "portrait",
                        "per_page": 3,
                        "size": "large",
                    },
                    headers={"Authorization": api_key},
                )

                if resp.status_code == 429:
                    logger.warning("   ⚠️  Pexels rate limit hit — pausing 10s")
                    await asyncio.sleep(10)
                    resp = await client.get(
                        _PEXELS_SEARCH_URL,
                        params={
                            "query": desc,
                            "orientation": "portrait",
                            "per_page": 1,
                        },
                        headers={"Authorization": api_key},
                    )

                if resp.status_code != 200:
                    logger.error(
                        "   ✖  Pexels API error %d: %s",
                        resp.status_code, resp.text[:200],
                    )
                    continue

                data = resp.json()
                photos = data.get("photos", [])
                if not photos:
                    # Retry with simpler query (first 2 words)
                    simple_query = " ".join(desc.split()[:3])
                    logger.debug(
                        "   No results — retrying with '%s'", simple_query
                    )
                    resp2 = await client.get(
                        _PEXELS_SEARCH_URL,
                        params={
                            "query": simple_query,
                            "per_page": 1,
                        },
                        headers={"Authorization": api_key},
                    )
                    if resp2.status_code == 200:
                        photos = resp2.json().get("photos", [])

                if not photos:
                    logger.warning("   ⚠️  No images found for: '%s'", desc[:60])
                    continue

                # Pick best photo — prefer portrait-ish aspect ratio
                photo = photos[0]
                # Prefer portrait src, fall back to large2x
                img_url = (
                    photo.get("src", {}).get("portrait")
                    or photo.get("src", {}).get("large2x")
                    or photo.get("src", {}).get("large")
                    or photo.get("src", {}).get("original")
                )

                if not img_url:
                    logger.warning("   ⚠️  No image URL in Pexels response")
                    continue

                # Download image
                img_resp = await client.get(img_url, follow_redirects=True)
                if img_resp.status_code != 200:
                    logger.error("   ✖  Image download failed: %d", img_resp.status_code)
                    continue

                ext = "jpg"
                if "png" in img_url.lower():
                    ext = "png"

                img_path = str(
                    IMAGES_DIR / f"scene_{i:02d}_{timestamp}.{ext}"
                )
                with open(img_path, "wb") as f:
                    f.write(img_resp.content)

                size_kb = len(img_resp.content) / 1024
                logger.info(
                    "   ✔  Downloaded: %s (%.0f KB)",
                    os.path.basename(img_path), size_kb,
                )
                image_paths.append(img_path)

                # Pexels rate limit: be a good citizen
                await asyncio.sleep(0.5)

            except httpx.TimeoutException:
                logger.error("   ✖  Timeout fetching image for: '%s'", desc[:60])
                continue
            except Exception as exc:
                logger.error("   ✖  Unexpected error fetching image: %s", exc)
                continue

    logger.info(
        "🖼  Pexels complete: %d/%d images downloaded",
        len(image_paths), len(scene_descriptions),
    )
    return image_paths


# ══════════════════════════════════════════════════════════════════
#  3. Build full story Short
# ══════════════════════════════════════════════════════════════════

async def build_story_short(
    theme: str,
    custom_prompt: str | None = None,
) -> int:
    """Full pipeline: script → TTS → images → video → database.

    Parameters
    ----------
    theme : str
        One of ``AVAILABLE_THEMES``.
    custom_prompt : str | None
        Extra creative direction for the AI.

    Returns
    -------
    int
        Database ID of the new story record.

    Raises
    ------
    AllKeysExhaustedError
        If Gemini is unreachable.
    VideoProcessingError
        If FFmpeg assembly fails.
    """
    cfg = get_theme_config(theme)
    timestamp = int(time.time())
    interim_files: list[str] = []  # track files for cleanup on failure

    logger.info("═" * 60)
    logger.info("  STORY ENGINE START — theme: %s", cfg["label"])
    logger.info("═" * 60)

    # Create a preliminary DB record so progress is visible
    story_id = await db.create_story(theme, prompt=custom_prompt or "")
    await db.update_story(story_id, status="processing")

    try:
        # ── 1. Generate story script via Gemini ──────────────────
        assets = await generate_story_assets(theme, custom_prompt)

        script = assets.get("script", "")
        title = assets.get("title", f"{cfg['label']} Story")
        scene_descs = assets.get("scene_descriptions", [])
        tags = assets.get("tags", [])
        description = assets.get("description", "")

        await db.update_story(
            story_id,
            script=script,
            title=title,
            description=description,
            tags=tags,
        )

        # ── 2. Generate TTS voiceover ────────────────────────────
        audio_path = str(AUDIO_DIR / f"story_{story_id}_{timestamp}.mp3")
        tts_result: TTSResult = await generate_tts(
            script, cfg["voice"], audio_path
        )
        interim_files.append(audio_path)

        await db.update_story(story_id, audio_path=tts_result.audio_path)

        logger.info(
            "🔊  Voiceover ready: %.1f s, %d word boundaries",
            tts_result.duration_seconds, len(tts_result.word_boundaries),
        )

        # ── 3. Fetch stock images from Pexels ────────────────────
        image_paths = await fetch_pexels_visuals(scene_descs)
        interim_files.extend(image_paths)

        if not image_paths:
            logger.warning(
                "⚠️  No images fetched — cannot assemble video.  "
                "Check PEXELS_API_KEY in .env"
            )
            await db.update_story(
                story_id,
                status="error",
                error_message="No stock images available — set PEXELS_API_KEY",
            )
            return story_id

        # ── 4. Select background music ───────────────────────────
        bgm_path = _resolve_bgm(cfg.get("bgm_file", ""))

        # ── 5. Assemble final video ──────────────────────────────
        output_filename = f"story_{theme}_{story_id}_{timestamp}.mp4"
        output_path = str(OUTPUTS_DIR / output_filename)

        await assemble_story_video(
            images=image_paths,
            audio_path=tts_result.audio_path,
            script_segments=tts_result.word_boundaries,
            bgm_path=bgm_path,
            output_path=output_path,
        )

        # ── 6. Finalise DB record ────────────────────────────────
        await db.update_story(
            story_id,
            output_path=output_path,
            status="ready",
        )

        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        logger.info("═" * 60)
        logger.info(
            "  STORY ENGINE DONE — id=%d  '%s'  (%.1f MB, %.1fs)",
            story_id, title[:40], size_mb, tts_result.duration_seconds,
        )
        logger.info("═" * 60)

        return story_id

    except Exception as exc:
        logger.exception("✖  Story pipeline failed for id=%d: %s", story_id, exc)
        await db.update_story(
            story_id,
            status="error",
            error_message=str(exc)[:500],
        )
        raise

    finally:
        # ── Cleanup interim files (keep audio + output) ──────────
        for img_path in interim_files:
            if img_path.startswith(str(IMAGES_DIR)) and os.path.isfile(img_path):
                try:
                    os.remove(img_path)
                    logger.debug("🗑  Removed interim image: %s", img_path)
                except OSError:
                    pass


# ══════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════

def _resolve_bgm(filename: str) -> str | None:
    """Return the absolute path to a BGM file, or None if not found."""
    if not filename:
        return None
    path = BGM_DIR / filename
    if path.is_file():
        logger.info("🎵  BGM track: %s", filename)
        return str(path)
    logger.warning(
        "🎵  BGM file not found: %s — video will have TTS audio only.  "
        "Place royalty-free MP3 files in %s",
        filename, BGM_DIR,
    )
    return None
