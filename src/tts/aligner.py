import os
import re
import math
import asyncio
import logging
import edge_tts
from mutagen.mp3 import MP3
from ..schemas import DraftScriptPayload, AlignedRenderPayload, AlignedSceneItem, SubtitleItem
from ..templates_manager import TemplateManager
from ..platform_policy import get_footer_callout

logger = logging.getLogger(__name__)

def sanitize_narration_guardrail(text: str, is_tail: bool = False) -> str:
    """
    全链路最高风控红线拦截门禁：
    彻底清除“暗号”、“私信”、“主页置顶”、“粉丝群”、“领源码/求源码/领取论文”等任何导流诱导词
    """
    if not text:
        return ""
    cleaned = text
    # 违规导流模式拦截与清洗
    # 2. 彻底清洗与置换推特/X/外网等国内敏感词汇
    #    2026-11-12：这张表已上收到 src/safety/rewrites.py —— 敏感词门禁
    #    （scripts/hooks/sensitive_scan.py）必须用同一份，否则「流水线会改写的东西
    #    先被门禁毙掉」（缺陷①）。
    from ..safety.rewrites import apply_rewrites
    cleaned, _ = apply_rewrites(cleaned)

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
        r"领源码",
        r"后台[私踢]我[^\s，。！!？?]*",
        r"领[配置工具福利资料源码]+[^\s，。！!？?]*",
        r"留邮箱[^\s，。！!？?]*"
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
    def __init__(self, voice: str, rate: str):
        # T4.5：音色/语速必须显式传入（来自 pipeline.yaml 槽位），此处不再有默认值
        if not voice or not rate:
            raise ValueError("TTSAligner 需要显式 voice/rate（禁止默认音色兜底）")
        self.voice = voice
        self.rate = rate

    @classmethod
    def from_slot(cls, slot: str = "tts.tech") -> "TTSAligner":
        """按 pipeline.yaml 的 providers.<slot>.env 解析音色与语速。

        槽位缺 voice/rate，或对应环境变量未配置 → MissingConfig（拒绝静默降级）。
        """
        from src.pipeline.config import MissingConfig, resolve_env
        from src.pipeline.contracts import load_pipeline

        providers = (load_pipeline().get("providers") or {})
        spec = providers.get(slot) or {}
        env_map = spec.get("env") or {}
        keys = {"voice": env_map.get("voice"), "rate": env_map.get("rate")}
        missing = [k for k, v in keys.items() if not v]
        if missing:
            raise MissingConfig([f"providers.{slot}.env.{k}" for k in missing])
        vals = {k: resolve_env(v) for k, v in keys.items()}
        absent = [keys[k] for k, v in vals.items() if not v]
        if absent:
            raise MissingConfig(absent)
        return cls(voice=vals["voice"], rate=vals["rate"])

    async def align_draft(
        self,
        draft: DraftScriptPayload,
        task_id: str,
        output_base_dir: str,
        template_override: str | None = None,
        props_dir: str | None = None,
        public_prefix: str | None = None,
    ) -> AlignedRenderPayload:
        """T4.1：props 与分段音频可落到 run 隔离目录。

        :param props_dir:    音频/JSON 的物理落盘目录（默认 {output_base_dir}/tasks/{task_id}）
        :param public_prefix: Remotion `staticFile()` 的相对前缀（默认 tasks/{task_id}）。
                              props 在 run_root 时，由调用方把 public/<prefix> 软链到 props_dir。
        """
        task_audio_dir = props_dir or os.path.join(output_base_dir, "tasks", task_id)
        os.makedirs(task_audio_dir, exist_ok=True)
        public_prefix = public_prefix or f"tasks/{task_id}"

        aligned_scenes = []
        # 前置 0.5 秒 (15 帧) 专属封面定格时间，确保各大平台及相册首帧稳妥抓取大标题
        cover_head_sec = 0.5
        fps = 30
        cover_head_frames = int(round(cover_head_sec * fps))

        current_time_sec = cover_head_sec
        current_frame = cover_head_frames
        pause_tail_sec = 0.25  # 句尾留白呼吸感

        total_scene_count = len(draft.scenes)
        for scene in draft.scenes:
            idx = scene.scene_index
            mp3_rel_path = f"{public_prefix}/scene_{idx}.mp3"
            mp3_abs_path = os.path.join(task_audio_dir, f"scene_{idx}.mp3")

            # 严格经过最高风控门禁清洗
            is_tail_scene = (idx == total_scene_count - 1)
            clean_narration = sanitize_narration_guardrail(scene.segment_narration, is_tail=is_tail_scene)

            # 调用 edge-tts 生成音频文件，增加重试机制应对微软服务偶发 503 握手失败
            saved = False
            for retry in range(4):
                try:
                    communicate = edge_tts.Communicate(clean_narration, self.voice, rate=self.rate)
                    await communicate.save(mp3_abs_path)
                    saved = True
                    break
                except Exception as e:
                    logger.warning(f"[TTSAligner] edge_tts retry {retry+1}/4 failed: {e}")
                    await asyncio.sleep(2 * (retry + 1))
            if not saved:
                raise RuntimeError(f"Failed to generate TTS after 4 retries for scene {idx}")

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
            footer_callout=get_footer_callout(getattr(draft, "target_account_type", "douyin")),
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
