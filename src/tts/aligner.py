import os
import asyncio
import re
import edge_tts
from mutagen.mp3 import MP3
from ..schemas import DraftScriptPayload, AlignedRenderPayload, AlignedSceneItem, SubtitleItem
from ..templates_manager import TemplateManager
from ..platform_policy import get_footer_callout

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

        for scene in draft.scenes:
            idx = scene.scene_index
            mp3_rel_path = f"tasks/{task_id}/scene_{idx}.mp3"
            mp3_abs_path = os.path.join(output_base_dir, mp3_rel_path)

            # 调用 edge-tts 生成音频文件
            communicate = edge_tts.Communicate(scene.segment_narration, self.voice, rate=self.rate)
            await communicate.save(mp3_abs_path)

            # mutagen 获取精确时长
            audio = MP3(mp3_abs_path)
            raw_duration = audio.info.length
            total_scene_sec = raw_duration + pause_tail_sec
            scene_duration_frames = int(round(total_scene_sec * fps))

            # 纯文本切分子句生成字幕轴
            subtitles = self._split_subtitles(scene.segment_narration, raw_duration)

            sfx_src = f"sfx/{scene.sfx_cue}.wav" if scene.sfx_cue != "none" else ""

            aligned_scene = AlignedSceneItem(
                scene_index=idx,
                visual_type=scene.visual_type,
                segment_narration=scene.segment_narration,
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
