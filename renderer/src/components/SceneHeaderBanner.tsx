import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';
import { useTemplate } from '../templates/context';

export interface SceneHeaderBannerProps {
  entityKey?: string;
  sceneIndex: number;
  totalScenes: number;
  highlightText: string;
}

export const SceneHeaderBanner: React.FC<SceneHeaderBannerProps> = ({
  entityKey,
  sceneIndex,
  totalScenes,
  highlightText,
}) => {
  const frame = useCurrentFrame();
  const { tokens, layout } = useTemplate();

  const translateY = interpolate(frame, [0, 16], [28, 0], { extrapolateRight: 'clamp' });
  const opacity = interpolate(frame, [0, 12], [0, 1], { extrapolateRight: 'clamp' });

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 20,
        marginBottom: 36,
        transform: `translateY(${translateY}px)`,
        opacity,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span
          style={{
            background: tokens.badgeBg,
            color: tokens.badgeText,
            border: tokens.badgeBorder || 'none',
            padding: '12px 30px',
            borderRadius: layout.useSerifHeadline ? 6 : 999,
            fontSize: 28,
            fontWeight: 800,
          }}
        >
          {entityKey ? `${layout.headerBannerPrefix} · ${entityKey}` : layout.headerBannerPrefix}
        </span>
        <span
          style={{
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 26,
            fontWeight: 700,
            color: tokens.textSecondary,
          }}
        >
          SCENE 0{sceneIndex + 1} / 0{totalScenes}
        </span>
      </div>

      <div
        style={{
          fontFamily: layout.useSerifHeadline
            ? "'Noto Serif SC', Georgia, serif"
            : "'Inter', 'Noto Sans SC', sans-serif",
          fontSize: 62,
          fontWeight: 900,
          lineHeight: 1.2,
          color: tokens.textPrimary,
          letterSpacing: -1,
        }}
      >
        {layout.useHighlighterUnderline ? (
          <span
            style={{
              background: 'linear-gradient(transparent 58%, #FDE68A 58%)',
              padding: '0 8px',
            }}
          >
            {highlightText}
          </span>
        ) : (
          highlightText
        )}
      </div>
    </div>
  );
};
