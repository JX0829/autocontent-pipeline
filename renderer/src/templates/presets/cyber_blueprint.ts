import { TemplatePreset } from '../types';

export const cyberBlueprintPreset: TemplatePreset = {
  id: 'cyber_blueprint',
  name: 'Cyber Blueprint · 赛博蓝图工程风',
  shortName: 'T6 · 赛博蓝图工程风',
  description: '深海普鲁士工程蓝图 + 赛博青荧光刻线 + CAD 几何标尺 + 纯正硬核技术雷达',
  recommendedThemes: ['tech-github', 'ai-product', 'breaking-news'],
  tokens: {
    canvasBg: 'radial-gradient(ellipse at 50% 10%, #102A43 0%, #06121E 60%, #030911 100%)',
    patternType: 'linear_grid',
    patternColor: 'rgba(56, 189, 248, 0.08)',
    patternSizePx: 48,
    ambientGlowPrimary: 'rgba(0, 255, 204, 0.18)',
    ambientGlowSecondary: 'rgba(56, 189, 248, 0.15)',
    textPrimary: '#F0F9FF',
    textSecondary: '#7DD3FC',
    accentPrimary: '#00FFCC',
    accentSecondary: '#FF9F1C',
    cardBg: 'rgba(10, 25, 47, 0.88)',
    cardAltBgs: [
      'rgba(10, 25, 47, 0.92)',
      'rgba(12, 35, 64, 0.92)',
      'rgba(14, 42, 71, 0.92)'
    ],
    cardBorder: '1.5px solid rgba(0, 255, 204, 0.45)',
    cardRadius: 8,
    cardShadow: '0 0 30px rgba(0, 255, 204, 0.15), 0 20px 40px rgba(0, 0, 0, 0.7)',
    badgeBg: '#00FFCC',
    badgeText: '#030911',
    badgeBorder: '1px solid #00FFCC',
    codeBg: '#030911',
    codeHeaderBg: 'rgba(0, 255, 204, 0.12)',
    codeHeaderText: '#00FFCC',
    codeText: '#E0F2FE',
    subBg: 'rgba(3, 9, 17, 0.95)',
    subText: '#F0F9FF',
    subHighlight: '#00FFCC',
    subBorder: '1px solid rgba(0, 255, 204, 0.5)',
    subRadius: 8,
    subShadow: '0 10px 30px rgba(0, 0, 0, 0.8)'
  },
  layout: {
    terminalVariant: 'raycast_obsidian',
    cardVariant: 'linear_matrix',
    useSerifHeadline: false,
    useHighlighterUnderline: false,
    cardTiltDegrees: 0,
    barRadius: 4,
    headerBannerPrefix: '⚡ CAD // BLUEPRINT SPEC'
  }
};
