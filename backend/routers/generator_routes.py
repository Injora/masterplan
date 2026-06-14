from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
import story_engine

router = APIRouter(prefix="/generator", tags=["Generator"])

class GeneratorRequest(BaseModel):
    theme: str
    custom_prompt: Optional[str] = None

@router.post("/run")
async def run_generator(req: GeneratorRequest, background_tasks: BackgroundTasks):
    """Start the AI story generator in the background."""
    if req.theme not in story_engine.AVAILABLE_THEMES:
        raise HTTPException(status_code=400, detail=f"Invalid theme. Must be one of: {story_engine.AVAILABLE_THEMES}")
        
    background_tasks.add_task(
        story_engine.build_story_short,
        theme=req.theme,
        custom_prompt=req.custom_prompt
    )
    return {"status": "accepted", "message": f"Story generation started for theme: {req.theme}"}

@router.get("/themes")
async def get_themes():
    """Get available story themes and configs."""
    return {"themes": story_engine._THEME_CONFIG}
