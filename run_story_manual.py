import asyncio
import logging
from backend import db, story_engine, scheduler, uploader

logging.basicConfig(level=logging.INFO)

async def main():
    print("--- Starting Manual Story Engine Run ---")
    await db.init_db()
    
    # 1. Generate the story
    story_id = await story_engine.build_story_short("world_history")
    print(f"Generated Story ID: {story_id}")
    
    # 2. Queue for all channels
    channels = await db.get_active_channels()
    for channel in channels:
        log_id = await db.create_upload_log(
            content_id=story_id,
            content_type="story",
            channel_id=channel["id"]
        )
        print(f"Queued Story ID {story_id} for Channel ID {channel['id']} (Log ID: {log_id})")
        
    # 3. Process the upload queue to immediately upload it
    print("--- Processing Upload Queue ---")
    await scheduler.job_process_upload_queue()
    print("--- Done! ---")

if __name__ == "__main__":
    asyncio.run(main())
