from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Literal, Optional

# ---------------------------------------------------------------- 品类与分镜预算
# F-2026-11-12-TEXTBOOK-STORY §6：分镜上限 8 → 25，但科技类必须保持 8 不变。
# 上限放宽必须在**品类维度**上区分，否则科技速报会被 LLM 拖成 20 镜的注水片。
CONTENT_TYPE_TECH = "tech_news"          # 缺省：现有科技速报物料不带 content_type
CONTENT_TYPE_TEXTBOOK = "textbook_story" # 课文故事：90~150 秒叙事片
MAX_SCENES_TECH = 8
MAX_SCENES_TEXTBOOK = 25

# 科技风视觉（GeekShortVideo）与古风视觉（TextbookStoryVideo，M2）并列新增、互不混用。
TECH_VISUAL_TYPES = ("terminal_code", "ui_card", "stat_chart", "media_showcase")
TEXTBOOK_VISUAL_TYPES = ("ink_title", "vertical_poem", "ancient_map", "archive_card", "scroll_transition")


class SceneItemDraft(BaseModel):
    scene_index: int
    segment_narration: str = Field(..., max_length=100)
    visual_type: Literal[
        "terminal_code", "ui_card", "stat_chart", "media_showcase",
        "ink_title", "vertical_poem", "ancient_map", "archive_card", "scroll_transition",
    ]
    code_snippet: Optional[str] = None
    media_asset_url: Optional[str] = None
    stat_items: Optional[List[dict]] = None
    highlight_text: str = Field(..., max_length=40)
    sfx_cue: Literal["whoosh", "typing", "ding", "glitch", "none"]

class DraftScriptPayload(BaseModel):
    theme: Literal["tech-github", "ai-product", "breaking-news", "textbook-story"]
    entity_key: str
    content_type: Literal["tech_news", "textbook_story"] = Field(
        default=CONTENT_TYPE_TECH,
        description="物料品类：tech_news（科技速报，缺省）| textbook_story（课文故事）"
    )
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
    scenes: List[SceneItemDraft] = Field(..., min_length=3, max_length=MAX_SCENES_TEXTBOOK)
    tags: List[str]
    target_account_type: Literal["tech", "news", "edu"]

    @model_validator(mode="after")
    def check_scene_budget_by_content_type(self):
        """分镜预算按品类分流（F-2026-11-12 §6 / spec §11.3 缺口 2）。

        · textbook_story：9~25 镜（90s÷10s 为下限，150s÷6s 留余量到上限）
        · 其它（含缺省 tech_news）：仍限 3~8 镜 —— 科技链路行为一字不变
        """
        n = len(self.scenes)
        if self.content_type == CONTENT_TYPE_TEXTBOOK:
            # M2 判据：课文片画面里不得再出现科技风视觉（终端/图表/UI 卡）
            bad = sorted({s.visual_type for s in self.scenes if s.visual_type not in TEXTBOOK_VISUAL_TYPES})
            if bad:
                raise ValueError(
                    f"课文故事分镜含科技风视觉 {bad}；课文片只允许 {list(TEXTBOOK_VISUAL_TYPES)}"
                    f"（spec：古诗配代码终端/数据图表属于事故）")
            # 镜数下限不在这里卡：它必须由「目标时长 ÷ 8~10 秒」推出来，
            # 而 target_duration_sec 在物料里，schema 看不到 —— 由
            # hotspot_extractor._generate_textbook_draft 的镜数预算断言负责（30s 样片 3 镜也合法）。
            return self
        bad_tech = sorted({s.visual_type for s in self.scenes if s.visual_type not in TECH_VISUAL_TYPES})
        if bad_tech:
            raise ValueError(
                f"科技速报分镜含古风视觉 {bad_tech}；科技片只允许 {list(TECH_VISUAL_TYPES)}"
                f"（若确为课文故事物料，请设 content_type={CONTENT_TYPE_TEXTBOOK}）")
        if n > MAX_SCENES_TECH:
            raise ValueError(
                f"科技速报分镜 {n} 镜 > {MAX_SCENES_TECH} 镜上限"
                f"（若确为课文故事物料，请设 content_type={CONTENT_TYPE_TEXTBOOK}）")
        return self

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
