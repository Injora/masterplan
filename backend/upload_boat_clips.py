import asyncio
import logging
from pathlib import Path
import db
from uploader import upload_video, get_youtube_client
import os
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    await db.init_db()
    
    clip_ids = [8]
    
    channels = await db.list_channels(active_only=True)
    if not channels:
        logger.error("No active channels found in DB!")
        return
        
    for channel in channels:
        logger.info(f"--- Processing channel {channel['name']} (ID: {channel['id']}) ---")
        
        youtube_client = await get_youtube_client(channel['id'])
        if not youtube_client:
            logger.error(f"Failed to authenticate channel {channel['id']}")
            continue
            
        for clip_id in clip_ids:
            clip = await db.get_story(clip_id)
            if not clip:
                logger.error(f"Clip {clip_id} not found!")
                continue
                
            filepath = clip['output_path']
            if not os.path.exists(filepath):
                logger.error(f"File not found: {filepath}")
                continue
            
            title = clip.get('title')
            if not title:
                title = f"Insane viral clip {clip_id} #shorts"
            
            desc = clip.get('description')
            if not desc:
                desc = "Check out this insane moment! #shorts #viral"
            
            logger.info(f"Uploading clip {clip_id} to channel {channel['id']} ...")
            response = await upload_video(
                youtube=youtube_client,
                file_path=filepath,
                title=title[:100],  # YouTube title limit
                description=desc,
                tags=["shorts", "viral", "trending"],
                category_id=24  # Entertainment
            )
            
            if response:
                logger.info(f"✅ Uploaded clip {clip_id} to channel {channel['id']} successfully! Video ID: {response.get('id')}")
            else:
                logger.error(f"❌ Failed to upload clip {clip_id} to channel {channel['id']}")

if __name__ == "__main__":
    asyncio.run(main())
