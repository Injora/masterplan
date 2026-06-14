from fastapi import APIRouter, HTTPException, BackgroundTasks
import scheduler
import db

router = APIRouter(prefix="/scheduler", tags=["Scheduler"])

@router.post("/start")
async def start_scheduler_endpoint():
    """Start the APScheduler."""
    scheduler.start_scheduler()
    return {"status": "started"}

@router.post("/stop")
async def stop_scheduler_endpoint():
    """Stop the APScheduler."""
    scheduler.stop_scheduler()
    return {"status": "stopped"}

@router.post("/trigger_upload")
async def trigger_upload(background_tasks: BackgroundTasks):
    """Manually trigger the upload queue processor."""
    background_tasks.add_task(scheduler.job_process_upload_queue)
    return {"status": "triggered"}

@router.post("/trigger_daily_pipeline")
async def trigger_daily_pipeline(background_tasks: BackgroundTasks):
    """Manually trigger the daily morning Shorts automation pipeline."""
    background_tasks.add_task(scheduler.job_daily_shorts_pipeline)
    return {"status": "triggered", "message": "Daily morning pipeline execution started in background."}

@router.post("/upload_item/{content_type}/{content_id}/{channel_id}")
async def upload_item_route(content_type: str, content_id: int, channel_id: int, background_tasks: BackgroundTasks):
    """Queue a specific clip or story for immediate upload to a specific channel."""
    if content_type not in ("clip", "story"):
        raise HTTPException(status_code=400, detail="Invalid content type. Must be 'clip' or 'story'")
        
    # Check if item exists
    if content_type == "clip":
        item = await db.get_clip(content_id)
    else:
        item = await db.get_story(content_id)
        
    if not item:
        raise HTTPException(status_code=404, detail=f"{content_type.capitalize()} ID {content_id} not found")
        
    # Check if channel exists
    channel = await db.get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail=f"Channel {channel_id} not found")
        
    # Create a pending upload log entry
    log_id = await db.create_upload_log(
        content_id=content_id,
        content_type=content_type,
        channel_id=channel_id
    )
    
    # Trigger the upload queue in the background
    background_tasks.add_task(scheduler.job_process_upload_queue)
    
    return {
        "status": "queued",
        "message": f"Queued {content_type} ID {content_id} for immediate upload to Channel '{channel['name']}'. Processing in background.",
        "upload_log_id": log_id
    }
