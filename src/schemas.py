from pydantic import BaseModel, Field, field_validator
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
    title: str = Field(..., max_length=20, description="抖音/视频号双列瀑布流极客标题，严格限制在20字以内")
    cover_main_text: str = Field(..., max_length=20)
    cover_sub_text: str = Field(..., max_length=30)
    hook_narration: str = Field(..., max_length=50)
    fact_summary_150: str = Field(..., max_length=200)
    video_desc: Optional[str] = Field(
        default=None,
        max_length=240,
        description="发布用简介（人话，100~170字）：禁斜杠命令、文件名、emoji、话题标签，供三平台统一取用",
    )
    resource_keyword: str = Field(..., max_length=20)
    template_id: Optional[str] = Field(
        default="auto",
        description="视觉模板 ID：auto | editorial_news | cupertino_glass | neo_brutalism | notion_craft | linear_obsidian 或自定义扩展模板 ID"
    )
    scenes: List[SceneItemDraft] = Field(..., min_length=3, max_length=8)
    tags: List[str]
    target_account_type: Literal["tech", "news"]

    @field_validator("title", mode="before")
    @classmethod
    def sanitize_title_length(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
            if len(v) > 20:
                sub = v[:20]
                delimiters = ["！", "!", "，", ",", "：", ":", " ", "、", "-", "—", "|", "？", "?"]
                best_cut = -1
                for d in delimiters:
                    idx = sub.rfind(d)
                    if idx >= 8:
                        best_cut = max(best_cut, idx)
                if best_cut != -1:
                    v = sub[:best_cut].rstrip("！!，,:：、-—|?？ ")
                else:
                    v = sub.rstrip("！!，,:：、-—|?？ ")
        return v

    @field_validator("fact_summary_150", mode="before")
    @classmethod
    def sanitize_fact_summary(cls, v: str) -> str:
        """名字里的 150 就是真上限：超长按标点截断，别让整条草稿崩在 string_too_long。

        200 的 max_length 保留作兜底（截断逻辑失效时仍然会拦），但正常路径不该走到 200。
        为什么不是靠 on_schema_error 重试：同样的输入第二次还是超长，白烧一次 LLM。
        """
        if not isinstance(v, str):
            return v
        v = v.strip()
        if len(v) <= 150:
            return v
        sub = v[:150]
        delimiters = ("。", "！", "？", "；", "，", "、", "…", ".", "!", "?", ";", ",")
        best_cut = -1
        for d in delimiters:
            idx = sub.rfind(d)
            if idx >= 100:          # 太靠前会把摘要截成半句，宁可硬切
                best_cut = max(best_cut, idx)
        if best_cut != -1:
            cut = sub[:best_cut].rstrip("。！？；，、… .!?;,")
            return cut + ("。" if not cut.endswith(("。", "！", "？", "…")) else "")
        return sub.rstrip("。！？；，、… .!?;,")

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
    footer_callout: str = Field(
        default="💡 欢迎评论区交流",
        description="视频画面底部及卡片引导语，由平台策略中心动态注入"
    )
    scenes: List[AlignedSceneItem]
