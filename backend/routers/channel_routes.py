from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
import db
import uploader
import os
import asyncio

router = APIRouter(prefix="/channels", tags=["Channels"])

class ChannelCreate(BaseModel):
    name: str
    target_niche: str

@router.get("")
async def get_channels():
    """List all configured YouTube channels."""
    try:
        conn = await db._get_conn()
        try:
            conn.row_factory = db.aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM channels")
            rows = await cursor.fetchall()
            channels = []
            for row in rows:
                channel_dict = dict(row)
                # Check if authenticated (token file exists)
                token_path = uploader._get_token_path(channel_dict["id"])
                channel_dict["authenticated"] = token_path.exists()
                channels.append(channel_dict)
            return {"channels": channels}
        finally:
            await conn.close()
    except Exception as exc:
         raise HTTPException(status_code=500, detail=str(exc))

@router.post("")
async def add_channel(channel: ChannelCreate):
    """Register a new channel for uploads."""
    try:
         # Register channel with dynamic dummy channel ID using our updated helper
         channel_id = await db.create_channel(
             name=channel.name,
             target_niche=channel.target_niche
         )
         return {"status": "success", "channel_id": channel_id}
    except Exception as exc:
         raise HTTPException(status_code=500, detail=str(exc))

@router.post("/{channel_id}/authenticate")
async def authenticate_channel_route(channel_id: int, background_tasks: BackgroundTasks):
    """Trigger YouTube OAuth2 authentication flow for a channel."""
    channel = await db.get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail=f"Channel {channel_id} not found")

    client_secrets = channel.get("credentials_path")
    if not client_secrets:
        client_secrets = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secret.json")

    if not os.path.exists(client_secrets):
        raise HTTPException(
            status_code=400,
            detail=f"YouTube Client Secret file '{client_secrets}' not found. Please place it in the backend directory."
        )

    # Run the interactive OAuth flow in the background so it doesn't block the FastAPI request
    async def _run_auth():
        await uploader.authenticate_channel(channel_id, client_secrets)

    background_tasks.add_task(_run_auth)
    
    return {
        "status": "initiated",
        "message": f"OAuth flow initiated for channel '{channel['name']}'. Please check the browser window on the host machine to authorize."
    }
