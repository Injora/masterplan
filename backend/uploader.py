"""
YouTube Shorts Factory — YouTube API Uploader
===============================================
Handles OAuth2 token refresh, resumable media uploads, and metadata application.

Requires OAuth2 client credentials in ``backend/.env`` or a client_secret.json file.
"""

from __future__ import annotations

import asyncio
import logging
import os
import json
from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
import httpx

import db

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]
TOKEN_DIR = Path(__file__).resolve().parent / "data" / "tokens"
TOKEN_DIR.mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════════════════════════════
#  1. OAuth Authentication
# ══════════════════════════════════════════════════════════════════

def _get_token_path(channel_id: int) -> Path:
    """Return the absolute path to the token file for a channel."""
    return TOKEN_DIR / f"channel_{channel_id}.json"


async def authenticate_channel(channel_id: int, client_secrets_file: str) -> Credentials | None:
    """Authenticate a YouTube channel using OAuth2 flow.

    Saves the resulting credentials to a JSON file tied to the channel_id.
    Note: This flow requires a web browser to complete the interactive login.
    """
    token_path = _get_token_path(channel_id)
    creds = None

    if token_path.exists():
        try:
            with open(token_path, "r") as f:
                creds_data = json.load(f)
            creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
            logger.info("🔑  Loaded existing credentials for channel %d", channel_id)
        except Exception as exc:
            logger.warning("⚠️  Failed to load existing credentials: %s", exc)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("🔄  Refreshing expired token for channel %d", channel_id)
            try:
                creds.refresh(Request())
            except Exception as exc:
                logger.error("✖  Failed to refresh token: %s", exc)
                creds = None
        
        if not creds:
            logger.info("🌐  Starting OAuth flow for channel %d", channel_id)
            if not os.path.exists(client_secrets_file):
                 logger.error("✖  client_secrets_file not found: %s", client_secrets_file)
                 return None
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    client_secrets_file, SCOPES
                )
                # This blocks and opens a browser window
                # run_local_server is a synchronous call
                creds = await asyncio.to_thread(flow.run_local_server, port=0)
            except Exception as exc:
                logger.error("✖  OAuth flow failed: %s", exc)
                return None

        # Save the credentials for the next run
        try:
            with open(token_path, "w") as f:
                f.write(creds.to_json())
            logger.info("💾  Saved new credentials for channel %d", channel_id)
        except Exception as exc:
             logger.error("✖  Failed to save credentials: %s", exc)
             return None

    # Overwrite dummy channel ID and name with authentic details from YouTube
    if creds:
        try:
            youtube = build("youtube", "v3", credentials=creds)
            
            def _fetch_channel_info():
                return youtube.channels().list(mine=True, part="id,snippet").execute()
                
            response = await asyncio.to_thread(_fetch_channel_info)
            if response and response.get("items"):
                item = response["items"][0]
                real_channel_id = item["id"]
                real_name = item["snippet"]["title"]
                
                await db.update_channel(
                    channel_id,
                    channel_id=real_channel_id,
                    name=real_name
                )
                logger.info("📡  Sync'd YouTube channel details for channel %d: %s (%s)", channel_id, real_name, real_channel_id)
        except Exception as exc:
            logger.error("✖  Failed to sync YouTube channel info on authentication: %s", exc)

    return creds

async def get_youtube_client(channel_id: int) -> Resource | None:
    """Build and return an authenticated YouTube API client for a channel."""
    
    # Check if the channel exists in the DB
    try:
        channel = await db.get_channel(channel_id)
        if not channel:
            logger.error("✖  Channel ID %d not found in database", channel_id)
            return None
    except Exception as exc:
         logger.error("✖  Database error retrieving channel: %s", exc)
         return None
        
    client_secrets = channel.get("credentials_path")
    if not client_secrets:
        client_secrets = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secret.json")
        
    creds = await authenticate_channel(channel_id, client_secrets)
    
    if not creds:
        return None

    try:
        youtube = build("youtube", "v3", credentials=creds)
        return youtube
    except Exception as exc:
        logger.error("✖  Failed to build YouTube client: %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════
#  2. Video Upload
# ══════════════════════════════════════════════════════════════════

async def upload_video(
    youtube: Resource,
    file_path: str,
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str = "public",
    category_id: str = "24",  # 24 = Entertainment
) -> dict[str, Any] | None:
    """Upload a video to YouTube using a resumable upload.

    Returns the YouTube API response dict (containing the video ID) or None on failure.
    """
    if not os.path.isfile(file_path):
        logger.error("✖  Video file not found: %s", file_path)
        return None

    logger.info("🚀  Preparing to upload: '%s' (%s)", title[:50], os.path.basename(file_path))

    body = {
        "snippet": {
            "title": title[:100],  # YouTube API limit
            "description": description[:5000], # YouTube API limit
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    try:
        media_body = MediaFileUpload(
            file_path,
            chunksize=-1, # Allows googleapiclient to handle chunking automatically
            resumable=True
        )

        def _execute_upload():
            request = youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media_body
            )
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logger.debug("   Uploading... %d%%", int(status.progress() * 100))
            return response
            
        logger.info("📤  Starting upload stream to YouTube...")
        response = await asyncio.to_thread(_execute_upload)
        
        video_id = response.get("id")
        logger.info("✅  Upload successful! Video ID: %s", video_id)
        return response

    except httpx.HTTPError as exc:
        logger.error("✖  Network error during upload: %s", exc)
        return None
    except Exception as exc:
        logger.error("✖  Unexpected upload error: %s", exc)
        return None
