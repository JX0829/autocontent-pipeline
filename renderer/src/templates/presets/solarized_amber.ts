import { TemplatePreset } from '../types';

export const solarizedAmberPreset: TemplatePreset = {
  id: 'solarized_amber',
  name: 'Solarized Amber · 琥珀熔岩极客风',
  shortName: 'T8 · 琥珀日珥风',
  description: '极深熔岩黑底色 + 工业放射琥珀橙 + 45°警戒斜纹 + 强视觉工业高能警示感',
  recommendedThemes: ['tech-github', 'breaking-news'],
  tokens: {
    canvasBg: 'linear-gradient(180deg, #140A04 0%, #080301 100%)',
    patternType: 'linear_grid',
    patternColor: 'rgba(255, 119, 0, 0.12)',
    patternSizePx: 44,
    ambientGlowPrimary: 'rgba(255, 119, 0, 0.28)',
    ambientGlowSecondary: 'rgba(255, 204, 0, 0.18)',
    textPrimary: '#FFF7ED',
    textSecondary: '#FDBA74',
    accentPrimary: '#FF7700',
    accentSecondary: '#FFD000',
    cardBg: 'rgba(24, 12, 5, 0.94)',
    cardAltBgs: [
      'rgba(26, 13, 5, 0.96)',
      'rgba(36, 16, 6, 0.96)',
      'rgba(20, 10, 4, 0.96)'
    ],
    cardBorder: '2px solid #FF7700',
    cardRadius: 4,
    cardShadow: '0 0 35px rgba(255, 119, 0, 0.25), 0 16px 36px rgba(0, 0, 0, 0.85)',
    badgeBg: '#FF7700',
    badgeText: '#0A0502',
    badgeBorder: '2px solid #FFD000',
    codeBg: '#080301',
    codeHeaderBg: 'rgba(255, 119, 0, 0.22)',
    codeHeaderText: '#FFB366',
    codeText: '#FFE8D6',
    subBg: 'rgba(10, 5, 2, 0.96)',
    subText: '#FFF7ED',
    subHighlight: '#FF7700',
    subBorder: '2px solid #FF7700',
    subRadius: 4,
    subShadow: '0 0 28px rgba(255, 119, 0, 0.4)'
  },
  layout: {
    terminalVariant: 'raycast_obsidian',
    cardVariant: 'linear_matrix',
    useSerifHeadline: false,
    useHighlighterUnderline: false,
    cardTiltDegrees: 0,
    barRadius: 4,
    headerBannerPrefix: '🔥 HAZARD LAB // CRITICAL ALERT'
  }
};
