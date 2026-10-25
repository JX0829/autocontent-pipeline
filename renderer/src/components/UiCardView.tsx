import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';
import { useTemplate } from '../templates/context';
import { SceneHeaderBanner } from './SceneHeaderBanner';

export interface StatItem {
  label: string;
  value: string;
}

export interface UiCardViewProps {
  sceneIndex: number;
  totalScenes: number;
  entityKey?: string;
  highlightText: string;
  statItems: StatItem[];
}

export const UiCardView: React.FC<UiCardViewProps> = ({
  sceneIndex,
  totalScenes,
  entityKey,
  highlightText,
  statItems,
}) => {
  const frame = useCurrentFrame();
  const { tokens, layout } = useTemplate();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', width: '100%' }}>
      <SceneHeaderBanner
        entityKey={entityKey}
        sceneIndex={sceneIndex}
        totalScenes={totalScenes}
        highlightText={highlightText}
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 30 }}>
        {statItems.map((item, idx) => {
          const delay = idx * 8;
          const cardY = interpolate(frame, [delay, delay + 20], [48, 0], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          });
          const opacity = interpolate(frame, [delay, delay + 14], [0, 1], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          });
          const bg =
            tokens.cardAltBgs && tokens.cardAltBgs.length > 0
              ? tokens.cardAltBgs[idx % tokens.cardAltBgs.length]
              : tokens.cardBg;
          const tilt =
            layout.cardTiltDegrees > 0
              ? `rotate(${idx % 2 === 0 ? -layout.cardTiltDegrees : layout.cardTiltDegrees}deg)`
              : '';

          return (
            <div
              key={idx}
              style={{
                transform: `translateY(${cardY}px) ${tilt}`,
                opacity,
                background: bg,
                border: tokens.cardBorder,
                borderRadius: tokens.cardRadius,
                boxShadow: tokens.cardShadow,
                padding: '40px 42px',
                display: 'flex',
                alignItems: 'center',
                gap: 32,
              }}
            >
              <div
                style={{
                  width: 92,
                  height: 92,
                  borderRadius: layout.useSerifHeadline ? 8 : 24,
                  background: tokens.accentPrimary,
                  color: '#FFFFFF',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 44,
                  fontWeight: 900,
                  flexShrink: 0,
                  border: layout.cardVariant === 'pop_sticker' ? '4px solid #111111' : 'none',
                }}
              >
                0{idx + 1}
              </div>
              <div style={{ flex: 1 }}>
                <div
                  style={{
                    fontSize: 30,
                    fontWeight: 700,
                    color: tokens.textSecondary,
                    marginBottom: 8,
                  }}
                >
                  {item.label}
                </div>
                <div
                  style={{
                    fontSize: 42,
                    fontWeight: 900,
                    color: tokens.textPrimary,
                    lineHeight: 1.25,
                  }}
                >
                  {item.value}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
