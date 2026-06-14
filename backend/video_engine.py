"""
YouTube Shorts Factory — Video Engine
=======================================
Core media-processing layer.  Every heavy operation (FFmpeg, yt-dlp)
runs as an **async subprocess** so the FastAPI event loop is never blocked.

FFmpeg encoding uses Apple Silicon hardware acceleration
(``h264_videotoolbox``) with an automatic fallback to ``libx264``.

Public Functions
----------------
download_video        — Pull a YouTube video via yt-dlp
get_transcript        — Fetch timed transcript via youtube-transcript-api
get_audio_duration    — Probe audio/video duration via ffprobe
extract_and_crop_clip — Single-pass slice + 9:16 center-crop
generate_tts          — Edge-TTS narration + word-boundary timestamps
assemble_story_video  — Ken Burns images + TTS + BGM + word-highlight captions
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import edge_tts

logger = logging.getLogger(__name__)

# ── Runtime directories ──────────────────────────────────────────
DATA_DIR = Path(__file__).resolve().parent / "data"
DOWNLOADS_DIR = DATA_DIR / "downloads"
OUTPUTS_DIR = DATA_DIR / "outputs"
AUDIO_DIR = DATA_DIR / "audio"

for _d in (DOWNLOADS_DIR, OUTPUTS_DIR, AUDIO_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════
#  Exceptions
# ══════════════════════════════════════════════════════════════════

class VideoProcessingError(Exception):
    """Raised when an FFmpeg / yt-dlp operation fails."""


class TranscriptNotFoundError(Exception):
    """Raised when a video transcript cannot be fetched."""


# ══════════════════════════════════════════════════════════════════
#  Data structures
# ══════════════════════════════════════════════════════════════════

@dataclass
class TTSResult:
    """Return value of :func:`generate_tts`."""
    audio_path: str
    word_boundaries: list[dict[str, Any]] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class WordBoundary:
    text: str
    start: float   # seconds
    end: float     # seconds
    duration: float # seconds


# ══════════════════════════════════════════════════════════════════
#  Encoder detection (run once on first call)
# ══════════════════════════════════════════════════════════════════

_encoder_cache: dict[str, Any] | None = None


async def _detect_encoder() -> tuple[str, list[str]]:
    """Detect the best available H.264 encoder on this machine.
    Returns ``(encoder_name, extra_flags)``."""
    global _encoder_cache
    if _encoder_cache:
        return _encoder_cache["encoder"], _encoder_cache["flags"]

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-encoders",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode(errors="replace")

    if "h264_videotoolbox" in output:
        enc, flags = "h264_videotoolbox", ["-b:v", "8M", "-allow_sw", "1"]
        logger.info("🎬  Encoder: h264_videotoolbox (Apple Silicon HW)")
    else:
        enc, flags = "libx264", ["-preset", "medium", "-crf", "23"]
        logger.info("🎬  Encoder: libx264 (software fallback)")

    _encoder_cache = {"encoder": enc, "flags": flags}
    return enc, flags


# ══════════════════════════════════════════════════════════════════
#  Helper: run FFmpeg / any subprocess
# ══════════════════════════════════════════════════════════════════

async def _run_subprocess(
    cmd: list[str],
    *,
    description: str = "subprocess",
    timeout: int = 600,
) -> tuple[str, str]:
    """Run a command asynchronously.  Raises on non-zero exit."""
    logger.info("▶  %s: %s", description, " ".join(cmd[:6]) + " …")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise VideoProcessingError(
            f"{description} timed out after {timeout}s"
        )

    stdout = stdout_b.decode(errors="replace")
    stderr = stderr_b.decode(errors="replace")

    if proc.returncode != 0:
        # Log last 30 lines of stderr for diagnostics
        tail = "\n".join(stderr.strip().splitlines()[-30:])
        logger.error("✖  %s failed (rc=%d):\n%s", description, proc.returncode, tail)
        raise VideoProcessingError(
            f"{description} exited with code {proc.returncode}: {tail[-500:]}"
        )

    logger.info("✔  %s completed successfully", description)
    return stdout, stderr


# ══════════════════════════════════════════════════════════════════
#  1. Download video
# ══════════════════════════════════════════════════════════════════

async def download_video(url: str, output_dir: str | None = None) -> str:
    """Download a YouTube video in the best available quality via yt-dlp.

    Returns the absolute path to the downloaded ``.mp4`` file.
    """
    import yt_dlp  # heavy import — keep lazy

    dest = output_dir or str(DOWNLOADS_DIR)
    Path(dest).mkdir(parents=True, exist_ok=True)

    outtmpl = os.path.join(dest, "%(id)s.%(ext)s")

    ydl_opts: dict[str, Any] = {
        "format": (
            "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/"
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "best[ext=mp4]/best"
        ),
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
        "noprogress": False,
        "socket_timeout": 30,
        "retries": 3,
    }

    def _do_download() -> str:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info("⬇  Downloading: %s", url)
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
            logger.info(
                "⬇  Download complete: %s  (%s, %.1f MB)",
                os.path.basename(filepath),
                info.get("resolution", "?"),
                os.path.getsize(filepath) / (1024 * 1024),
            )
            return filepath

    return await asyncio.to_thread(_do_download)


# ══════════════════════════════════════════════════════════════════
#  2. Transcript extraction
# ══════════════════════════════════════════════════════════════════

async def get_transcript(youtube_id: str) -> list[dict[str, Any]]:
    """Fetch the transcript for a YouTube video.

    Returns a list of ``{"text", "start", "duration"}`` dicts.
    Falls back to auto-generated captions when manual subs are absent.
    """
    from youtube_transcript_api import YouTubeTranscriptApi  # lazy

    def _fetch() -> list[dict[str, Any]]:
        try:
            logger.info("📜  Fetching transcript for video %s", youtube_id)
            ytt_api = YouTubeTranscriptApi()
            transcript = ytt_api.fetch(youtube_id)
            snippets = [
                {
                    "text": seg.text,
                    "start": seg.start,
                    "duration": seg.duration,
                }
                for seg in transcript.snippets
            ]
            logger.info(
                "📜  Transcript fetched: %d segments, %.0f s total",
                len(snippets),
                sum(s["duration"] for s in snippets) if snippets else 0,
            )
            return snippets
        except Exception as exc:
            logger.error("📜  Transcript fetch failed for %s: %s", youtube_id, exc)
            raise TranscriptNotFoundError(
                f"Could not retrieve transcript for {youtube_id}: {exc}"
            ) from exc

    return await asyncio.to_thread(_fetch)


# ══════════════════════════════════════════════════════════════════
#  3. Audio duration probe
# ══════════════════════════════════════════════════════════════════

async def get_audio_duration(path: str) -> float:
    """Return the duration (seconds) of an audio or video file."""
    stdout, _ = await _run_subprocess(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_entries", "format=duration",
            path,
        ],
        description=f"ffprobe duration [{os.path.basename(path)}]",
    )
    data = json.loads(stdout)
    dur = float(data["format"]["duration"])
    logger.debug("Duration of %s: %.2f s", os.path.basename(path), dur)
    return dur


# ══════════════════════════════════════════════════════════════════
#  4. Extract clip + 9:16 center-crop  (single FFmpeg pass)
# ══════════════════════════════════════════════════════════════════

async def extract_and_crop_clip(
    input_path: str,
    output_path: str,
    start_sec: float,
    end_sec: float,
    transcript_words: list[dict[str, Any]] | None = None,
) -> str:
    """Slice a time-range and center-crop to 1080×1920 (9:16) in one pass.

    * NO watermarks added
    * Audio is preserved and re-encoded to AAC
    * Uses hardware encoder on Apple Silicon
    """
    duration = end_sec - start_sec
    if duration <= 0:
        raise VideoProcessingError(
            f"Invalid time range: start={start_sec}, end={end_sec}"
        )

    encoder, enc_flags = await _detect_encoder()

    # The filter duplicates the video, creating a blurred background and a scaled foreground overlay
    filter_parts = [
        "split=2[bg][fg]",
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:20,eq=brightness=-0.1[bg_blurred]",
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg_scaled]",
        "[bg_blurred][fg_scaled]overlay=(W-w)/2:(H-h)/2"
    ]
    
    if transcript_words:
        subs_path = output_path.rsplit(".", 1)[0] + ".ass"
        _generate_ass_subtitles(transcript_words, subs_path)
        subs_escaped = _esc(os.path.abspath(subs_path))
        filter_parts[-1] += f",subtitles=filename='{subs_escaped}'"

    vf = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_sec:.3f}",
        "-i", input_path,
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-c:v", encoder, *enc_flags,
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]

    logger.info(
        "✂  Extracting clip: %.1f–%.1f s  (%.1f s) → %s",
        start_sec, end_sec, duration, os.path.basename(output_path),
    )
    await _run_subprocess(cmd, description="extract_and_crop_clip")
    return output_path


# ══════════════════════════════════════════════════════════════════
#  5. Text-to-Speech via Edge-TTS
# ══════════════════════════════════════════════════════════════════

# You can use any edge-tts voice. en-US-AndrewMultilingualNeural is highly energetic.
TTS_VOICES = {
    "ancient_greece":  "en-US-AndrewMultilingualNeural",
    "world_history":   "en-US-AndrewMultilingualNeural",
    "scary_stories":   "en-US-AndrewMultilingualNeural",
    "default":         "en-US-AndrewMultilingualNeural",
    "male":            "en-US-AndrewMultilingualNeural",
}


async def generate_tts(
    text: str,
    voice: str,
    output_path: str,
) -> TTSResult:
    """Generate speech audio + per-word timestamps using Microsoft Edge TTS.

    Parameters
    ----------
    text : str
        The full narration script.
    voice : str
        Edge-TTS voice identifier, e.g. ``en-US-ChristopherNeural``.
    output_path : str
        Where to write the output MP3 file.

    Returns
    -------
    TTSResult
        Contains ``audio_path``, ``word_boundaries``, and ``duration_seconds``.

    Notes
    -----
    Edge-TTS v7+ emits ``SentenceBoundary`` events rather than per-word
    ``WordBoundary`` events.  When only sentence boundaries are received,
    this function synthesises per-word timing by distributing the sentence
    duration proportionally across its words (weighted by character length).
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    communicate = edge_tts.Communicate(text, voice, rate="+20%")
    word_boundaries: list[dict[str, Any]] = []
    sentence_boundaries: list[dict[str, Any]] = []

    logger.info("🔊  Generating TTS  (voice=%s, %d chars)", voice, len(text))

    with open(output_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # edge-tts < 7.x provides per-word events
                offset_sec = chunk["offset"] / 10_000_000
                dur_sec = chunk["duration"] / 10_000_000
                word_boundaries.append({
                    "text": chunk["text"],
                    "start": offset_sec,
                    "duration": dur_sec,
                    "end": offset_sec + dur_sec,
                })
            elif chunk["type"] == "SentenceBoundary":
                # edge-tts 7.x+ provides per-sentence events
                offset_sec = chunk["offset"] / 10_000_000
                dur_sec = chunk["duration"] / 10_000_000
                sentence_boundaries.append({
                    "text": chunk["text"],
                    "start": offset_sec,
                    "duration": dur_sec,
                    "end": offset_sec + dur_sec,
                })

    # If we only got sentence boundaries, synthesise word-level timing
    if not word_boundaries and sentence_boundaries:
        word_boundaries = _words_from_sentences(sentence_boundaries)
        logger.info(
            "🔊  Synthesised %d word boundaries from %d sentence boundaries",
            len(word_boundaries), len(sentence_boundaries),
        )

    # Get actual audio duration from the rendered file
    dur = await get_audio_duration(output_path)
    file_size = os.path.getsize(output_path) / 1024

    logger.info(
        "🔊  TTS complete: %.1f s, %.0f KB, %d word boundaries",
        dur, file_size, len(word_boundaries),
    )

    return TTSResult(
        audio_path=output_path,
        word_boundaries=word_boundaries,
        duration_seconds=dur,
    )


def _words_from_sentences(
    sentences: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Split sentence-level timing into estimated per-word timing.

    Each word's share of the sentence duration is proportional to its
    character length (a simple but surprisingly effective heuristic for
    TTS pacing).
    """
    words: list[dict[str, Any]] = []

    for sent in sentences:
        raw_words = sent["text"].split()
        if not raw_words:
            continue

        total_chars = sum(len(w) for w in raw_words)
        if total_chars == 0:
            total_chars = len(raw_words)  # fall back to equal split

        cursor = sent["start"]
        sent_dur = sent["duration"]

        for w in raw_words:
            # Proportional duration by character count
            w_dur = sent_dur * (len(w) / total_chars) if total_chars else sent_dur / len(raw_words)
            words.append({
                "text": w,
                "start": round(cursor, 4),
                "duration": round(w_dur, 4),
                "end": round(cursor + w_dur, 4),
            })
            cursor += w_dur

    return words


# ══════════════════════════════════════════════════════════════════
#  6. ASS subtitle generation (word-by-word highlight)
# ══════════════════════════════════════════════════════════════════

_ASS_HEADER = """\
[Script Info]
Title: YouTube Shorts Factory — Auto Captions
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,64,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,2,0,1,5,2,5,40,40,340,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# Colors (ASS uses &HBBGGRR order)
_CLR_WHITE = r"{\c&HFFFFFF&}"
_CLR_HIGHLIGHT = r"{\c&H00FFFF&}"  # bright yellow in BGR


def _secs_to_ass(seconds: float) -> str:
    """Convert seconds → ``H:MM:SS.CC`` for ASS timestamps."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds % 1) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _generate_ass_subtitles(
    word_boundaries: list[dict[str, Any]],
    output_path: str,
    *,
    words_per_group: int = 4,
) -> str:
    """Build an ASS subtitle file with word-by-word yellow highlighting.

    Each caption line shows a group of words; the currently spoken word
    is rendered in bright yellow while the rest stay white.
    """
    if not word_boundaries:
        logger.warning("No word boundaries — skipping subtitle generation")
        # Write a minimal valid ASS file so FFmpeg doesn't choke
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(_ASS_HEADER)
        return output_path

    # ── Group words into display phrases ─────────────────────────
    groups: list[list[dict[str, Any]]] = []
    for i in range(0, len(word_boundaries), words_per_group):
        groups.append(word_boundaries[i : i + words_per_group])

    # ── Build dialogue events ────────────────────────────────────
    events: list[str] = []

    for group in groups:
        for word_idx, current_word in enumerate(group):
            # Each word gets its own dialogue line where IT is highlighted
            parts: list[str] = []
            for j, w in enumerate(group):
                clean = w["text"].replace("\\", "")
                if j == word_idx:
                    parts.append(f"{_CLR_HIGHLIGHT}{clean}{_CLR_WHITE}")
                else:
                    parts.append(clean)

            line_text = " ".join(parts)
            start = _secs_to_ass(current_word["start"])
            end = _secs_to_ass(current_word["end"])

            events.append(
                f"Dialogue: 0,{start},{end},Default,,0,0,0,,"
                f"{{\\an5}}{_CLR_WHITE}{line_text}"
            )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(_ASS_HEADER)
        f.write("\n".join(events))
        f.write("\n")

    logger.info(
        "📝  ASS subtitle file written: %d events → %s",
        len(events), os.path.basename(output_path),
    )
    return output_path


# ══════════════════════════════════════════════════════════════════
#  7. FFmpeg path escaping
# ══════════════════════════════════════════════════════════════════

def _esc(path: str) -> str:
    """Escape a file path for use inside an FFmpeg filter string."""
    # FFmpeg filter option values require escaping of \ : ' [ ]
    return (
        path
        .replace("\\", "\\\\\\\\")
        .replace(":", "\\:")
        .replace("'", "'\\''")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


# ══════════════════════════════════════════════════════════════════
#  8. Assemble story video (the big one)
# ══════════════════════════════════════════════════════════════════

# Two alternating Ken Burns motion presets (slow zoom in / slow zoom out)
_KB_ZOOM_IN = (
    "z='min(zoom+0.0015,1.25)'"
    ":x='iw/2-(iw/zoom/2)'"
    ":y='ih/2-(ih/zoom/2)'"
)
_KB_ZOOM_OUT = (
    "z='if(lte(zoom,1.0),1.25,max(1.001,zoom-0.0015))'"
    ":x='iw/2-(iw/zoom/2)'"
    ":y='ih/2-(ih/zoom/2)'"
)


async def assemble_story_video(
    images: list[str],
    audio_path: str,
    script_segments: list[dict[str, Any]],
    bgm_path: str | None,
    output_path: str,
) -> str:
    """Render a vertical Short from images, TTS audio, and captions.

    Pipeline
    --------
    1. Each image → scale-up → Ken Burns (alternating zoom in/out)
    2. Concatenate all image segments into one video track
    3. TTS audio + BGM mixed (BGM at 15 % volume)
    4. ASS subtitles burned in (word-by-word highlight)
    5. Encode with hardware H.264

    Parameters
    ----------
    images : list[str]
        Paths to the source images (≥ 1).
    audio_path : str
        TTS narration audio file.
    script_segments : list[dict]
        Word-boundary dicts from :func:`generate_tts`.
    bgm_path : str | None
        Optional background-music file.  Pass ``None`` to skip BGM.
    output_path : str
        Destination path for the final ``.mp4``.
    """
    if not images:
        raise VideoProcessingError("assemble_story_video requires at least 1 image")

    encoder, enc_flags = await _detect_encoder()

    # ── Determine durations ──────────────────────────────────────
    total_duration = await get_audio_duration(audio_path)
    N = len(images)
    fade_duration = 1.0  # 1 second crossfade

    # If N images, there are N-1 transitions. Total transition overlap = (N-1) * fade_duration
    # To hit exactly `total_duration`, each image must be extended.
    per_image_dur = (total_duration + (N - 1) * fade_duration) / N if N > 0 else 0
    fps = 30

    logger.info(
        "🎞  Assembling story: %d images × %.1f s = %.1f s total (with %.1f s fades)",
        N, per_image_dur, total_duration, fade_duration
    )

    # ── Generate ASS subtitle file ───────────────────────────────
    subs_path = output_path.rsplit(".", 1)[0] + ".ass"
    _generate_ass_subtitles(script_segments, subs_path)

    # ── Build FFmpeg command ─────────────────────────────────────
    cmd: list[str] = ["ffmpeg", "-y"]

    # Add each image as an input
    for img in images:
        cmd.extend(["-i", img])

    # Add TTS audio
    tts_input_idx = len(images)
    cmd.extend(["-i", audio_path])

    # Add BGM audio (looped) if provided
    bgm_input_idx: int | None = None
    if bgm_path and os.path.isfile(bgm_path):
        bgm_input_idx = tts_input_idx + 1
        cmd.extend(["-stream_loop", "-1", "-i", bgm_path])

    # ── Complex filter graph ─────────────────────────────────────
    filter_parts: list[str] = []
    for i, _img in enumerate(images):
        frames = int(math.ceil(per_image_dur * fps))
        kb = _KB_ZOOM_IN if i % 2 == 0 else _KB_ZOOM_OUT
        label = f"v{i}"

        # Scale image up (2× output res) so Ken Burns zoom has headroom,
        # then crop to exact 2160×3840, apply zoompan, enforce pixel format
        filter_parts.append(
            f"[{i}:v]"
            f"scale=2160:3840:force_original_aspect_ratio=increase,"
            f"crop=2160:3840,"
            f"zoompan={kb}:d={frames}:s=1080x1920:fps={fps},"
            f"format=yuv420p"
            f"[{label}]"
        )

    # Daisy-chain the xfade filters
    if N == 1:
        # Just one image, no crossfade
        filter_parts.append(f"[v0]copy[video_raw]")
    else:
        last_out = "v0"
        for i in range(1, N):
            offset = i * per_image_dur - i * fade_duration
            out_label = f"xfade{i}"
            if i == N - 1:
                out_label = "video_raw"
            filter_parts.append(
                f"[{last_out}][v{i}]xfade=transition=fade:duration={fade_duration}:offset={offset:.2f}[{out_label}]"
            )
            last_out = out_label

    # Burn subtitles onto the video track
    subs_escaped = _esc(os.path.abspath(subs_path))
    filter_parts.append(
        f"[video_raw]subtitles=filename='{subs_escaped}'[video]"
    )

    # Audio mixing
    if bgm_input_idx is not None:
        filter_parts.append(f"[{tts_input_idx}:a]volume=1.0[tts_a]")
        filter_parts.append(
            f"[{bgm_input_idx}:a]volume=0.15,"
            f"atrim=duration={total_duration + 1},"
            f"asetpts=PTS-STARTPTS[bgm_a]"
        )
        filter_parts.append(
            "[tts_a][bgm_a]amix=inputs=2:duration=first:dropout_transition=2[audio]"
        )
        audio_map = "[audio]"
    else:
        audio_map = f"{tts_input_idx}:a"

    # Combine everything into the -filter_complex argument
    fc = ";\n".join(filter_parts)
    cmd.extend(["-filter_complex", fc])

    # Output mapping + encoding
    cmd.extend([
        "-map", "[video]",
        "-map", audio_map,
        "-c:v", encoder, *enc_flags,
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        output_path,
    ])

    logger.info("🎞  Running FFmpeg assembly (this may take a while) …")
    await _run_subprocess(
        cmd,
        description="assemble_story_video",
        timeout=900,  # 15 min max for long/complex videos
    )

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    logger.info(
        "🎞  ✅  Story video assembled: %s (%.1f MB, %.1f s)",
        os.path.basename(output_path), size_mb, total_duration,
    )

    return output_path


# ══════════════════════════════════════════════════════════════════
#  Utilities
# ══════════════════════════════════════════════════════════════════

async def get_video_info(path: str) -> dict[str, Any]:
    """Return resolution, duration, and codec info for a video file."""
    stdout, _ = await _run_subprocess(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams", "-show_format",
            path,
        ],
        description=f"ffprobe info [{os.path.basename(path)}]",
    )
    return json.loads(stdout)


def extract_youtube_id(url: str) -> str | None:
    """Parse a YouTube video ID from various URL formats."""
    patterns = [
        r"(?:v=|\/v\/|youtu\.be\/)([a-zA-Z0-9_-]{11})",
        r"(?:shorts\/)([a-zA-Z0-9_-]{11})",
        r"(?:embed\/)([a-zA-Z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    # Maybe the input *is* the ID already
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", url):
        return url
    return None
