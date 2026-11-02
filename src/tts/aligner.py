import os
import re
import math
import edge_tts
from mutagen.mp3 import MP3
from ..schemas import DraftScriptPayload, AlignedRenderPayload, AlignedSceneItem, SubtitleItem
from ..templates_manager import TemplateManager
from ..platform_policy import get_footer_callout

def sanitize_narration_guardrail(text: str, is_tail: bool = False) -> str:
    """
    全链路最高风控红线拦截门禁：
    彻底清除“暗号”、“私信”、“主页置顶”、“粉丝群”、“领源码/求源码/领取论文”等任何导流诱导词
    """
    if not text:
        return ""
    cleaned = text
    # 违规导流模式拦截与清洗
    patterns = [
        r"回复暗号[^\s，。！!？?]*",
        r"私信我?[^\s，。！!？?]*",
        r"主页置顶[^\s，。！!？?]*",
        r"粉丝群[^\s，。！!？?]*",
        r"进群[^\s，。！!？?]*",
        r"领取[论文源码资料代码完整包]+[^\s，。！!？?]*",
        r"暗号",
        r"加群",
        r"求源码",
        r"领源码"
    ]
    for p in patterns:
        cleaned = re.sub(p, "", cleaned)
    
    cleaned = cleaned.replace("点赞收藏，", "").replace("点赞收藏", "")
    cleaned = re.sub(r"^[，,、\s]+", "", cleaned)
    cleaned = re.sub(r"[，,、\s]+$", "", cleaned).strip()

    if is_tail:
        # 结尾必须且只能是纯净的极客交流讨论
        if not cleaned.endswith("交流讨论～") and not cleaned.endswith("交流讨论！") and not cleaned.endswith("看法～"):
            cleaned = "欢迎在评论区交流讨论～"
    return cleaned

class TTSAligner:
    def __init__(self, voice="zh-CN-YunxiNeural", rate="+10%"):
        self.voice = voice
        self.rate = rate

    async def align_draft(
        self,
        draft: DraftScriptPayload,
        task_id: str,
        output_base_dir: str,
        template_override: str | None = None
    ) -> AlignedRenderPayload:
        task_audio_dir = os.path.join(output_base_dir, "tasks", task_id)
        os.makedirs(task_audio_dir, exist_ok=True)

        aligned_scenes = []
        current_time_sec = 0.0
        current_frame = 0
        fps = 30
        pause_tail_sec = 0.25  # 句尾留白呼吸感

        total_scene_count = len(draft.scenes)
        for scene in draft.scenes:
            idx = scene.scene_index
            mp3_rel_path = f"tasks/{task_id}/scene_{idx}.mp3"
            mp3_abs_path = os.path.join(output_base_dir, mp3_rel_path)

            # 严格经过最高风控门禁清洗
            is_tail_scene = (idx == total_scene_count - 1)
            clean_narration = sanitize_narration_guardrail(scene.segment_narration, is_tail=is_tail_scene)

            # 调用 edge-tts 生成音频文件
            communicate = edge_tts.Communicate(clean_narration, self.voice, rate=self.rate)
            await communicate.save(mp3_abs_path)

            # mutagen 获取精确时长
            audio = MP3(mp3_abs_path)
            raw_duration = audio.info.length
            total_scene_sec = raw_duration + pause_tail_sec
            scene_duration_frames = int(round(total_scene_sec * fps))

            # 纯文本切分子句生成字幕轴
            subtitles = self._split_subtitles(clean_narration, raw_duration)

            sfx_src = f"sfx/{scene.sfx_cue}.wav" if scene.sfx_cue != "none" else ""

            aligned_scene = AlignedSceneItem(
                scene_index=idx,
                visual_type=scene.visual_type,
                segment_narration=clean_narration,
                highlight_text=scene.highlight_text,
                code_snippet=scene.code_snippet,
                media_asset_url=scene.media_asset_url,
                stat_items=scene.stat_items,
                sfx_cue=scene.sfx_cue,
                sfx_src=sfx_src,
                audio_src=mp3_rel_path,
                start_sec=round(current_time_sec, 3),
                end_sec=round(current_time_sec + total_scene_sec, 3),
                start_frame=current_frame,
                duration_frames=scene_duration_frames,
                subtitles=subtitles
            )

            aligned_scenes.append(aligned_scene)
            current_time_sec += total_scene_sec
            current_frame += scene_duration_frames

        template_mgr = TemplateManager()
        resolved_template = template_mgr.resolve_template_id(
            theme=draft.theme,
            cli_template=template_override,
            payload_template=draft.template_id
        )

        return AlignedRenderPayload(
            task_id=task_id,
            template_id=resolved_template,
            fps=fps,
            width=1080,
            height=1920,
            total_duration_in_frames=current_frame,
            theme=draft.theme,
            title=draft.title,
            cover_main_text=draft.cover_main_text,
            cover_sub_text=draft.cover_sub_text,
            resource_keyword=draft.resource_keyword,
            bgm_src="sfx/bgm_loop.wav",
            footer_callout=get_footer_callout(getattr(draft, "target_account_type", "xiaohongshu")),
            scenes=aligned_scenes
        )

    def _split_subtitles(self, text: str, total_sec: float) -> list[SubtitleItem]:
        # 按照逗号、句号、感叹号、问号切分
        parts = [p.strip() for p in re.split(r'[，。！？!?,]', text) if p.strip()]
        if not parts:
            parts = [text]

        total_chars = sum(len(p) for p in parts)
        subtitles = []
        cur_ms = 0
        total_ms = int(total_sec * 1000)

        for p in parts:
            dur_ms = int((len(p) / total_chars) * total_ms) if total_chars > 0 else total_ms
            end_ms = min(total_ms, cur_ms + dur_ms)
            subtitles.append(SubtitleItem(
                text=p,
                start_ms=cur_ms,
                end_ms=end_ms
            ))
            cur_ms = end_ms

        return subtitles
