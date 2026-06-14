"""
YouTube Shorts Factory — FastAPI Application
==============================================
Entry-point for the local macOS backend.

Start with:
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Or just:
    python main.py
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ── Load environment before anything else ────────────────────────
load_dotenv(Path(__file__).resolve().parent / ".env")

from db import DATA_DIR, init_db, init_api_key_slots, get_dashboard_stats  # noqa: E402

# ── Router imports ──
from routers.clipper_routes import router as clipper_router
from routers.generator_routes import router as generator_router
from routers.channel_routes import router as channel_router
from routers.scheduler_routes import router as scheduler_router


# ══════════════════════════════════════════════════════════════════
#  Logging
# ══════════════════════════════════════════════════════════════════

LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _configure_logging() -> None:
    """Verbose console + rotating file logger."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s │ %(levelname)-7s │ %(name)-22s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Console ──
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    # ── Rotating file (5 MB × 3 backups) ──
    file_handler = RotatingFileHandler(
        LOG_DIR / "factory.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Quieten noisy third-party loggers
    for name in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
#  ngrok tunnel helper
# ══════════════════════════════════════════════════════════════════

_ngrok_tunnel = None  # keep reference to prevent GC


def _start_ngrok(port: int) -> str | None:
    """Open an ngrok tunnel and return the public URL.
    Returns None if ngrok is not configured."""
    token = os.getenv("NGROK_AUTHTOKEN", "").strip()
    if not token:
        logger.warning("NGROK_AUTHTOKEN not set — skipping tunnel")
        return None

    try:
        from pyngrok import conf as ngrok_conf
        from pyngrok import ngrok

        ngrok_conf.get_default().auth_token = token

        domain = os.getenv("NGROK_DOMAIN", "").strip()
        kwargs: dict = {"bind_tls": True}
        if domain:
            kwargs["hostname"] = domain

        global _ngrok_tunnel
        _ngrok_tunnel = ngrok.connect(port, **kwargs)
        public_url: str = _ngrok_tunnel.public_url  # type: ignore[assignment]
        logger.info("🌐  ngrok tunnel open: %s", public_url)
        return public_url
    except Exception as exc:
        logger.error("ngrok failed to start: %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════
#  Lifespan (startup / shutdown)
# ══════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ── Startup ──────────────────────────────────────────────────
    logger.info("═" * 60)
    logger.info("  YouTube Shorts Factory — starting up")
    logger.info("═" * 60)

    # 1. Database
    await init_db()
    await init_api_key_slots(count=4)

    # 2. ngrok tunnel
    port = int(os.getenv("PORT", "8000"))
    ngrok_url = _start_ngrok(port)
    if ngrok_url:
        app.state.ngrok_url = ngrok_url
    else:
        app.state.ngrok_url = f"http://localhost:{port}"

    # 3. APScheduler
    try:
        import scheduler
        scheduler.start_scheduler()
    except Exception as exc:
        logger.error("Failed to start background scheduler: %s", exc)

    logger.info("✅  Startup complete — backend ready")

    yield

    # ── Shutdown ─────────────────────────────────────────────────
    logger.info("Shutting down …")

    # Teardown scheduler
    try:
        import scheduler
        scheduler.stop_scheduler()
    except Exception as exc:
        logger.error("Failed to stop background scheduler: %s", exc)

    # Close ngrok
    if _ngrok_tunnel is not None:
        try:
            from pyngrok import ngrok
            ngrok.disconnect(_ngrok_tunnel.public_url)
        except Exception:
            pass

    logger.info("👋  Shutdown complete")


# ══════════════════════════════════════════════════════════════════
#  Application instance
# ══════════════════════════════════════════════════════════════════

from fastapi.staticfiles import StaticFiles

app = FastAPI(
    title="YouTube Shorts Factory",
    description="Automated Shorts clipping, AI video generation, and multi-channel publishing.",
    version="0.1.0",
    lifespan=lifespan,
)

# Serve generated clips and stories as static files
app.mount("/outputs", StaticFiles(directory=str(DATA_DIR / "outputs")), name="outputs")

# ── CORS ─────────────────────────────────────────────────────────
_origins = [
    o.strip()
    for o in os.getenv("FRONTEND_ORIGINS", "http://localhost:3000,http://localhost:3050").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Global exception handler ────────────────────────────────────

@app.exception_handler(Exception)
async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)},
    )


# ── Routers ──────────────────────────────────────────────────────
app.include_router(clipper_router)
app.include_router(generator_router)
app.include_router(channel_router)
app.include_router(scheduler_router)


# ══════════════════════════════════════════════════════════════════
#  Health + Dashboard endpoints
# ══════════════════════════════════════════════════════════════════

@app.get("/api/health", tags=["System"])
async def health_check():
    """Lightweight liveness probe."""
    return {
        "status": "ok",
        "service": "youtube-shorts-factory",
        "ngrok_url": getattr(app.state, "ngrok_url", None),
    }


@app.get("/api/stats", tags=["System"])
async def dashboard_stats():
    """Aggregate counts for the frontend dashboard."""
    return await get_dashboard_stats()


# ══════════════════════════════════════════════════════════════════
#  Direct-run convenience
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
        log_level="info",
    )
