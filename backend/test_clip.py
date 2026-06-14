import asyncio
import logging
import db
import story_engine

logging.basicConfig(level=logging.INFO)

async def main():
    await db.init_db()
    
    # Run Story Engine to test the AI images, new TTS, and xfade!
    print("--- Running Story Engine ---")
    story_id = await story_engine.build_story_short("world_history")
    print("Story result ID:", story_id)

if __name__ == "__main__":
    asyncio.run(main())
