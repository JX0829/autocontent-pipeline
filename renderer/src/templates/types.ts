export type BuiltinTemplateId =
  | 'editorial_news'
  | 'cupertino_glass'
  | 'neo_brutalism'
  | 'notion_craft'
  | 'linear_obsidian';

export type TemplateId = BuiltinTemplateId | (string & {});

export type BackgroundPatternType = 'none' | 'paper_grid' | 'dot_matrix' | 'pop_halftone' | 'linear_grid';

export type TerminalChromeVariant =
  | 'telegraph_dispatch'
  | 'macos_glass'
  | 'brutalist_window'
  | 'notion_callout'
  | 'raycast_obsidian';

export type CardVariant =
  | 'editorial_column'
  | 'bento_glass'
  | 'pop_sticker'
  | 'notion_steps'
  | 'linear_matrix';

export interface TemplateTokens {
  canvasBg: string;
  patternType: BackgroundPatternType;
  patternColor: string;
  patternSizePx: number;
  ambientGlowPrimary?: string;
  ambientGlowSecondary?: string;
  textPrimary: string;
  textSecondary: string;
  accentPrimary: string;
  accentSecondary: string;
  cardBg: string;
  cardAltBgs?: string[];
  cardBorder: string;
  cardRadius: number;
  cardShadow: string;
  badgeBg: string;
  badgeText: string;
  badgeBorder?: string;
  codeBg: string;
  codeHeaderBg: string;
  codeHeaderText: string;
  codeText: string;
  subBg: string;
  subText: string;
  subHighlight: string;
  subBorder: string;
  subRadius: number;
  subShadow: string;
}

export interface TemplateLayoutConfig {
  terminalVariant: TerminalChromeVariant;
  cardVariant: CardVariant;
  useSerifHeadline: boolean;
  useHighlighterUnderline: boolean;
  cardTiltDegrees: number;
  barRadius: number;
  headerBannerPrefix: string;
}

export interface TemplatePreset {
  id: TemplateId;
  name: string;
  shortName: string;
  description: string;
  recommendedThemes: Array<'tech-github' | 'ai-product' | 'breaking-news'>;
  tokens: TemplateTokens;
  layout: TemplateLayoutConfig;
}
