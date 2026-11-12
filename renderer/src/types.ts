export interface SubtitleItem {
  text: string;
  start_ms: number;
  end_ms: number;
}

/** 科技风视觉（GeekShortVideo 使用） */
export type TechVisualType = 'terminal_code' | 'ui_card' | 'stat_chart' | 'media_showcase';

/** 古风视觉（TextbookStoryVideo 使用，F-2026-11-12 M2；与科技风并列新增，不替换） */
export type InkVisualType =
  | 'ink_title'
  | 'vertical_poem'
  | 'ancient_map'
  | 'archive_card'
  | 'scroll_transition';

export interface AlignedSceneItem {
  scene_index: number;
  visual_type: TechVisualType | InkVisualType | string;
  segment_narration: string;
  highlight_text: string;
  code_snippet?: string;
  media_asset_url?: string;
  stat_items?: Array<{ label: string; value: string }>;
  sfx_cue: string;
  sfx_src: string;
  audio_src: string;
  start_sec: number;
  end_sec: number;
  start_frame: number;
  duration_frames: number;
  subtitles: SubtitleItem[];
}

export interface AlignedRenderPayload extends Record<string, unknown> {
  task_id: string;
  template_id?: string;
  fps: number;
  width: number;
  height: number;
  total_duration_in_frames: number;
  theme: string;
  title: string;
  cover_main_text: string;
  cover_sub_text: string;
  resource_keyword: string;
  bgm_src: string;
  footer_callout?: string;
  /** T4.4：封面右侧三卡取哪几屏，由 pipeline.yaml 的 cover.card_source 声明（默认 [0,1,3]） */
  cover_card_source?: number[];
  /** F-2026-11-12 M2：课文物料透传（诗题/朝代/作者/原文/版本），古风模板渲染竖排原文与片尾版本标注 */
  textbook?: {
    poem_title?: string;
    dynasty?: string;
    author?: string;
    grade?: string;
    volume?: string;
    edition?: string;
    full_text?: string;
  };
  /** 合规标注：AI 生成 + 教材版本（spec F7 / §8.3） */
  compliance?: { ai_generated_label?: boolean; textbook_attribution?: string };
  scenes: AlignedSceneItem[];
}
