/**
 * 古风水墨主题 tokens（F-2026-11-12-TEXTBOOK-STORY · M2）
 *
 * 与科技风模板完全隔离：本文件只被 src/textbook/* 引用，
 * 不改 templates/presets/*、不改 GeekShortVideo 的任何默认值。
 *
 * 配色依据（小品「古风视觉规格」的口述要求）：
 *   宣纸底 #EFE6D4 / 墨 #1C1A17 / 朱印 #A6261F / 淡墨 #4A443B / 石青 #5B6E5A / 泥金 #B08A4B
 * 字体：楷体（标题、诗句）优先，宋体承接正文；缺字回落到 Noto Serif CJK。
 */

export const ink = {
  paper: '#EFE6D4',
  paperDeep: '#E2D6BE',
  paperShade: '#D8C9AC',
  ink: '#1C1A17',
  inkSoft: '#4A443B',
  inkFaint: 'rgba(28, 26, 23, 0.14)',
  inkWash: 'rgba(28, 26, 23, 0.06)',
  cinnabar: '#A6261F',
  cinnabarSoft: 'rgba(166, 38, 31, 0.85)',
  jade: '#5B6E5A',
  gold: '#B08A4B',
  fontKai: "'STKaiti', 'Kaiti SC', 'Kaiti TC', 'Songti SC', 'Noto Serif CJK SC', serif",
  fontSong: "'Songti SC', 'STSong', 'Noto Serif CJK SC', serif",
} as const;

/** 五类课文 visual_type —— 与科技风四类（terminal_code/ui_card/stat_chart/media_showcase）并列，不替换 */
export const INK_VISUAL_TYPES = [
  'ink_title',
  'vertical_poem',
  'ancient_map',
  'archive_card',
  'scroll_transition',
] as const;

export type InkVisualType = (typeof INK_VISUAL_TYPES)[number];

/** 兼容映射：历史 draft 里残留的科技风类型，在课文片里一律落到古风画面，绝不出现终端/图表/UI 卡 */
export const LEGACY_TO_INK: Record<string, InkVisualType> = {
  terminal_code: 'ink_title',
  ui_card: 'archive_card',
  stat_chart: 'ancient_map',
  media_showcase: 'archive_card',
};

export function toInkVisualType(visualType: string): InkVisualType {
  if ((INK_VISUAL_TYPES as readonly string[]).includes(visualType)) {
    return visualType as InkVisualType;
  }
  return LEGACY_TO_INK[visualType] || 'ink_title';
}

/** 把整首诗按标点断成竖排列（每列默认 7 字，符合五言/七言古诗的视觉节奏） */
export function splitPoemColumns(text: string, perColumn = 7): string[] {
  const clean = (text || '').replace(/[\r\n]+/g, '').trim();
  if (!clean) return [];
  const chunks = clean
    .split(/[，。！？；、,.!?;]/)
    .map((s) => s.trim())
    .filter(Boolean);
  const columns: string[] = [];
  for (const chunk of chunks) {
    if (chunk.length <= perColumn) {
      columns.push(chunk);
      continue;
    }
    for (let i = 0; i < chunk.length; i += perColumn) {
      columns.push(chunk.slice(i, i + perColumn));
    }
  }
  return columns;
}

/** 把旁白按标点切成短句，供史料卡/地图注脚分行 */
export function splitSentences(text: string, max = 3): string[] {
  return (text || '')
    .split(/[。！？；\n]/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, max);
}
