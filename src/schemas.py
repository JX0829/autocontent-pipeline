from pydantic import BaseModel, Field
from typing import List, Literal, Optional

class SceneItemDraft(BaseModel):
    scene_index: int
    segment_narration: str = Field(..., max_length=100)
    visual_type: Literal["terminal_code", "ui_card", "stat_chart", "media_showcase"]
    code_snippet: Optional[str] = None
    media_asset_url: Optional[str] = None
    stat_items: Optional[List[dict]] = None
    highlight_text: str = Field(..., max_length=40)
    sfx_cue: Literal["whoosh", "typing", "ding", "glitch", "none"]

class DraftScriptPayload(BaseModel):
    theme: Literal["tech-github", "ai-product", "breaking-news"]
    entity_key: str
    title: str = Field(..., max_length=50)
    cover_main_text: str = Field(..., max_length=20)
    cover_sub_text: str = Field(..., max_length=30)
    hook_narration: str = Field(..., max_length=50)
    fact_summary_150: str = Field(..., max_length=200)
    resource_keyword: str = Field(..., max_length=20)
    template_id: Optional[str] = Field(
        default="auto",
        description="视觉模板 ID：auto | editorial_news | cupertino_glass | neo_brutalism | notion_craft | linear_obsidian 或自定义扩展模板 ID"
    )
    scenes: List[SceneItemDraft] = Field(..., min_length=3, max_length=8)
    tags: List[str]
    target_account_type: Literal["tech", "news"]

class SubtitleItem(BaseModel):
    text: str
    start_ms: int
    end_ms: int

class AlignedSceneItem(BaseModel):
    scene_index: int
    visual_type: str
    segment_narration: str
    highlight_text: str
    code_snippet: Optional[str] = None
    media_asset_url: Optional[str] = None
    stat_items: Optional[List[dict]] = None
    sfx_cue: str
    sfx_src: str
    audio_src: str
    start_sec: float
    end_sec: float
    start_frame: int
    duration_frames: int
    subtitles: List[SubtitleItem]

class AlignedRenderPayload(BaseModel):
    task_id: str
    fps: int = 30
    width: int = 1080
    height: int = 1920
    total_duration_in_frames: int
    template_id: str = Field(
        default="linear_obsidian",
        description="经 TemplateManager 解析后最终注入 Remotion 渲染器的确定模板 ID"
    )
    theme: str
    title: str
    cover_main_text: str
    cover_sub_text: str
    resource_keyword: str
    bgm_src: str
    scenes: List[AlignedSceneItem]
