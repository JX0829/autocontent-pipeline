import { TemplatePreset } from '../types';

export const swissVanguardPreset: TemplatePreset = {
  id: 'swiss_vanguard',
  name: 'Swiss Vanguard · 瑞士红黑先锋大字报风',
  shortName: 'T9 · 瑞士红黑先锋风',
  description: '纯亮白底色 + 国际先锋大红 + 浓郁极黑粗黑体 + 报刊社论级强撞色与高密度排版',
  recommendedThemes: ['breaking-news', 'ai-product', 'tech-github'],
  tokens: {
    canvasBg: '#F8F8F8',
    patternType: 'paper_grid',
    patternColor: 'rgba(10, 10, 10, 0.06)',
    patternSizePx: 36,
    ambientGlowPrimary: 'rgba(217, 38, 38, 0.15)',
    textPrimary: '#0A0A0A',
    textSecondary: '#2E2E2E',
    accentPrimary: '#D92626',
    accentSecondary: '#0A0A0A',
    cardBg: '#FFFFFF',
    cardAltBgs: [
      '#FFFFFF',
      '#FFF5F5',
      '#F5F5F5'
    ],
    cardBorder: '3px solid #0A0A0A',
    cardRadius: 0,
    cardShadow: '12px 12px 0px #D92626',
    badgeBg: '#D92626',
    badgeText: '#FFFFFF',
    badgeBorder: '2px solid #0A0A0A',
    codeBg: '#0A0A0A',
    codeHeaderBg: '#D92626',
    codeHeaderText: '#FFFFFF',
    codeText: '#F8F8F8',
    subBg: '#0A0A0A',
    subText: '#FFFFFF',
    subHighlight: '#FF4D4D',
    subBorder: '2px solid #D92626',
    subRadius: 0,
    subShadow: '6px 6px 0px #D92626'
  },
  layout: {
    terminalVariant: 'telegraph_dispatch',
    cardVariant: 'editorial_column',
    useSerifHeadline: true,
    useHighlighterUnderline: true,
    cardTiltDegrees: 0,
    barRadius: 0,
    headerBannerPrefix: '🛑 SPECIAL ISSUE // 极客特刊'
  }
};
