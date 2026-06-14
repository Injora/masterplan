from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
import clipper

router = APIRouter(prefix="/clipper", tags=["Clipper"])

class ClipperRequest(BaseModel):
    niche: str = ""
    url: Optional[str] = None
    search_count: int = 5
    max_clips_per_video: int = 3
    min_virality_score: int = 6

@router.post("/run")
async def run_clipper(req: ClipperRequest, background_tasks: BackgroundTasks):
    """Start the viral clipper pipeline in the background."""
    background_tasks.add_task(
        clipper.run_clipper_pipeline,
        niche=req.niche,
        url=req.url,
        search_count=req.search_count,
        max_clips_per_video=req.max_clips_per_video,
        min_virality_score=req.min_virality_score
    )
    target = req.url if req.url else req.niche
    return {"status": "accepted", "message": f"Clipper pipeline started for: {target}"}

import os

@router.get("/clips")
async def get_clips(status: Optional[str] = None, limit: int = 50, offset: int = 0):
    """Retrieve all generated clips from the database."""
    try:
        clips = await clipper.db.list_clips(status=status, limit=limit, offset=offset)
        for clip in clips:
            if clip.get("output_path"):
                filename = os.path.basename(clip["output_path"])
                clip["play_url"] = f"/outputs/{filename}"
        return {"clips": clips}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch clips: {e}")

@router.get("/niches")
async def get_niches():
    """Get available standard niches."""
    return {"niches": list(clipper._NICHE_QUERIES.keys())}

