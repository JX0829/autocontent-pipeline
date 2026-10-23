export interface SubtitleItem {
  text: string;
  start_ms: number;
  end_ms: number;
}

export interface AlignedSceneItem {
  scene_index: number;
  visual_type: 'terminal_code' | 'ui_card' | 'stat_chart' | 'media_showcase';
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

export interface AlignedRenderPayload {
  task_id: string;
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
  scenes: AlignedSceneItem[];
}
