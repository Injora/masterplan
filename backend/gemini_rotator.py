"""
YouTube Shorts Factory — Gemini Rotational Client
===================================================
Manages 4 Gemini API keys with automatic failover on 429 (rate-limit)
errors.  Each key gets up to 3 retries with exponential backoff before
the rotator moves to the next key.  If all keys are exhausted, a clean
`AllKeysExhaustedError` is raised.

Usage:
    from gemini_rotator import gemini
    response = await gemini.generate_text("Write me a viral short title")
    structured = await gemini.generate_json(prompt, schema=MyPydanticModel)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Type

from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel
from tenacity import (
    AsyncRetrying,
    RetryError,
    stop_after_attempt,
    wait_random_exponential,
)

import db

load_dotenv()
logger = logging.getLogger(__name__)

# Default model — fast + cheap, ideal for metadata & script generation
DEFAULT_MODEL = "gemini-2.5-flash"


# ══════════════════════════════════════════════════════════════════
#  Exceptions
# ══════════════════════════════════════════════════════════════════

class AllKeysExhaustedError(Exception):
    """Raised when every Gemini API key has been tried and failed."""

    def __init__(self, errors: list[KeyError_] | None = None):
        self.key_errors = errors or []
        detail = "; ".join(
            f"Key {e.key_index}: {e.message}" for e in self.key_errors
        )
        super().__init__(
            f"All {len(self.key_errors)} Gemini API keys exhausted. "
            f"Details — {detail}"
        )


@dataclass
class KeyError_:
    """Record of a single key's failure."""
    key_index: int
    message: str
    timestamp: float = field(default_factory=time.time)


class _RateLimitHit(Exception):
    """Internal signal: the current key hit a 429 rate-limit."""

    def __init__(self, key_index: int, original: Exception):
        self.key_index = key_index
        self.original = original
        super().__init__(f"Key {key_index} rate-limited: {original}")


# ══════════════════════════════════════════════════════════════════
#  Rate-limit detection helpers
# ══════════════════════════════════════════════════════════════════

def _is_rate_limit(exc: Exception) -> bool:
    """Return True if *exc* represents a 429 / RESOURCE_EXHAUSTED error,
    regardless of which layer of the SDK raised it."""

    # 1. google-genai ClientError with .code
    if hasattr(exc, "code") and exc.code == 429:  # type: ignore[union-attr]
        return True

    # 2. google-api-core ResourceExhausted (sometimes wrapped)
    cls_name = type(exc).__name__
    if cls_name in ("ResourceExhausted", "TooManyRequests"):
        return True

    # 3. Fall back to string matching (defensive)
    err_lower = str(exc).lower()
    if "429" in err_lower or "resource_exhausted" in err_lower or "rate limit" in err_lower:
        return True

    return False


def _is_retryable_server_error(exc: Exception) -> bool:
    """503/500 errors that may resolve on retry."""
    if hasattr(exc, "code") and exc.code in (500, 502, 503):  # type: ignore[union-attr]
        return True
    cls_name = type(exc).__name__
    return cls_name in ("ServerError", "ServiceUnavailable", "InternalServerError")


# ══════════════════════════════════════════════════════════════════
#  GeminiRotator
# ══════════════════════════════════════════════════════════════════

class GeminiRotator:
    """Round-robin Gemini client with per-key retry + automatic failover.

    Parameters
    ----------
    api_keys : list[str]
        1–4 Gemini API keys.  Empty strings are silently skipped.
    """

    def __init__(self, api_keys: list[str]) -> None:
        # Filter out empty / placeholder keys
        self._keys = [k.strip() for k in api_keys if k.strip() and not k.startswith("your_")]
        if not self._keys:
            logger.error(
                "No valid Gemini API keys found.  "
                "Set GEMINI_API_KEY_1 … _4 in backend/.env"
            )
            self._clients: list[genai.Client] = []
            return

        self._clients = [
            genai.Client(
                api_key=k,
                http_options=genai_types.HttpOptions(timeout=120_000)
            )
            for k in self._keys
        ]
        self._current: int = 0
        self._lock = asyncio.Lock()

        logger.info(
            "GeminiRotator initialised with %d key(s)  [active index: 0]",
            len(self._clients),
        )

    # ── Public API ───────────────────────────────────────────────

    async def generate_text(
        self,
        prompt: str,
        *,
        model: str = DEFAULT_MODEL,
        system_instruction: str | None = None,
        temperature: float = 0.7,
        max_output_tokens: int | None = None,
    ) -> str:
        """Generate plain-text content.  Returns the response string."""
        config = genai_types.GenerateContentConfig(
            temperature=temperature,
        )
        if max_output_tokens is not None:
            config.max_output_tokens = max_output_tokens
        if system_instruction:
            config.system_instruction = system_instruction

        response = await self._rotate_and_call(prompt, model=model, config=config)
        return response.text or ""

    async def generate_json(
        self,
        prompt: str,
        *,
        schema: Type[BaseModel] | dict[str, Any] | None = None,
        model: str = DEFAULT_MODEL,
        system_instruction: str | None = None,
        temperature: float = 0.4,
        max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Generate structured JSON output.

        Parameters
        ----------
        schema : Pydantic BaseModel subclass **or** a raw JSON-Schema dict.
            Passed to Gemini's ``response_schema`` for constrained decoding.
        """
        config = genai_types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json",
        )
        if max_output_tokens is not None:
            config.max_output_tokens = max_output_tokens
        if schema is not None:
            config.response_schema = schema
        if system_instruction:
            config.system_instruction = system_instruction

        response = await self._rotate_and_call(prompt, model=model, config=config)
        text = (response.text or "").strip()

        # Defensive JSON parse
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("Gemini returned non-JSON text (error: %s), attempting repair. Raw text:\n%s", str(e), text)
            # Try to extract JSON from markdown code fence
            if "```json" in text:
                text = text.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in text:
                text = text.split("```", 1)[1].split("```", 1)[0].strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError as e2:
                logger.error("JSON repair failed. Text was:\n%s", text)
                raise e2

    # ── Internal rotation engine ─────────────────────────────────

    async def _rotate_and_call(
        self,
        prompt: str,
        *,
        model: str,
        config: genai_types.GenerateContentConfig,
    ) -> Any:
        """Try each key in order.  Per-key: up to 3 retries w/ backoff."""
        if not self._clients:
            raise AllKeysExhaustedError(
                [KeyError_(0, "No API keys configured")]
            )

        key_errors: list[KeyError_] = []
        n = len(self._clients)

        async with self._lock:
            start_index = self._current

        for offset in range(n):
            idx = (start_index + offset) % n
            logger.debug(
                "Attempting Gemini key %d/%d  (model=%s)", idx + 1, n, model
            )

            try:
                return await self._try_with_retries(
                    idx, prompt, model=model, config=config
                )
            except _RateLimitHit as rlh:
                logger.warning(
                    "🔑  Key %d/%d rate-limited — rotating to next key",
                    idx + 1, n,
                )
                key_errors.append(
                    KeyError_(idx, f"429 rate-limit: {rlh.original}")
                )
                # Advance the starting key for subsequent calls
                async with self._lock:
                    self._current = (idx + 1) % n
                continue
            except Exception as exc:
                # Non-retryable error — don't try remaining keys
                logger.error(
                    "Key %d/%d hit a non-retryable error: %s", idx + 1, n, exc
                )
                raise

        raise AllKeysExhaustedError(key_errors)

    async def _try_with_retries(
        self,
        key_index: int,
        prompt: str,
        *,
        model: str,
        config: genai_types.GenerateContentConfig,
    ) -> Any:
        """Attempt a single key up to 3 times with exponential backoff.
        On persistent 429, raises ``_RateLimitHit`` so the caller
        can rotate to the next key."""

        last_exc: Exception | None = None

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_random_exponential(multiplier=1, min=1, max=30),
                reraise=False,
            ):
                with attempt:
                    try:
                        response = await self._call_gemini(
                            key_index, prompt, model=model, config=config
                        )
                        # ── Success ──────────────────────────────
                        await db.record_api_key_request(key_index, success=True)
                        logger.info(
                            "✅  Gemini key %d — response received "
                            "(attempt %d, model=%s)",
                            key_index + 1,
                            attempt.retry_state.attempt_number,
                            model,
                        )
                        return response

                    except Exception as exc:
                        last_exc = exc

                        if _is_rate_limit(exc):
                            await db.record_api_key_request(
                                key_index, success=False
                            )
                            attempt_num = attempt.retry_state.attempt_number
                            logger.warning(
                                "⚠️  Key %d — 429 on attempt %d/3: %s",
                                key_index + 1,
                                attempt_num,
                                exc,
                            )
                            raise  # tenacity will retry

                        if _is_retryable_server_error(exc):
                            attempt_num = attempt.retry_state.attempt_number
                            logger.warning(
                                "⚠️  Key %d — server error on attempt %d/3: %s",
                                key_index + 1,
                                attempt_num,
                                exc,
                            )
                            raise  # tenacity will retry

                        # Non-retryable error → propagate immediately
                        raise

        except RetryError:
            # All 3 retries for this key used up → signal rotation
            raise _RateLimitHit(key_index, last_exc or Exception("unknown"))

    async def _call_gemini(
        self,
        key_index: int,
        prompt: str,
        *,
        model: str,
        config: genai_types.GenerateContentConfig,
    ) -> Any:
        """Execute a single Gemini API call (no retry logic here)."""
        client = self._clients[key_index]
        return await client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )

    # ── Diagnostics ──────────────────────────────────────────────

    @property
    def key_count(self) -> int:
        return len(self._clients)

    @property
    def current_key_index(self) -> int:
        return self._current

    async def get_key_stats(self) -> list[dict[str, Any]]:
        """Pull per-key metrics from the database."""
        return await db.list_api_key_usage()


# ══════════════════════════════════════════════════════════════════
#  Module-level singleton
# ══════════════════════════════════════════════════════════════════

def _load_keys() -> list[str]:
    """Read GEMINI_API_KEY_1 … _9 from the environment."""
    keys: list[str] = []
    for i in range(1, 10):
        key = os.getenv(f"GEMINI_API_KEY_{i}", "")
        keys.append(key)
        if key and not key.startswith("your_"):
            logger.debug("Loaded GEMINI_API_KEY_%d (…%s)", i, key[-4:])
    return keys


gemini = GeminiRotator(_load_keys())
"""Pre-configured singleton — import this from other modules::

    from gemini_rotator import gemini
"""
