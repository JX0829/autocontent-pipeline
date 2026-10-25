import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';
import { useTemplate } from '../templates/context';
import { SceneHeaderBanner } from './SceneHeaderBanner';
import { StatItem } from './UiCardView';

export interface StatChartViewProps {
  sceneIndex: number;
  totalScenes: number;
  entityKey?: string;
  highlightText: string;
  statItems: StatItem[];
}

export const StatChartView: React.FC<StatChartViewProps> = ({
  sceneIndex,
  totalScenes,
  entityKey,
  highlightText,
  statItems,
}) => {
  const frame = useCurrentFrame();
  const { tokens, layout } = useTemplate();

  const parsePercentage = (val: string, idx: number): number => {
    const match = val.match(/(\d+(\.\d+)?)/);
    if (match) {
      const num = parseFloat(match[1]);
      return Math.min(100, Math.max(35, num > 100 ? 92 : num));
    }
    return 88 - idx * 6;
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', width: '100%' }}>
      <SceneHeaderBanner
        entityKey={entityKey}
        sceneIndex={sceneIndex}
        totalScenes={totalScenes}
        highlightText={highlightText}
      />

      <div
        style={{
          background: tokens.cardBg,
          border: tokens.cardBorder,
          borderRadius: tokens.cardRadius,
          boxShadow: tokens.cardShadow,
          padding: '48px 44px',
          display: 'flex',
          flexDirection: 'column',
          gap: 38,
        }}
      >
        {statItems.map((item, idx) => {
          const targetPct = parsePercentage(item.value, idx);
          const barWidth = interpolate(frame, [idx * 6, idx * 6 + 32], [0, targetPct], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          });

          return (
            <div key={idx}>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'baseline',
                  marginBottom: 14,
                }}
              >
                <span style={{ fontSize: 34, fontWeight: 800, color: tokens.textPrimary }}>
                  {item.label}
                </span>
                <span
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 42,
                    fontWeight: 900,
                    color: tokens.accentPrimary,
                  }}
                >
                  {item.value}
                </span>
              </div>
              <div
                style={{
                  width: '100%',
                  height: 38,
                  background: 'rgba(148, 163, 184, 0.16)',
                  borderRadius: layout.barRadius,
                  overflow: 'hidden',
                  border: layout.cardVariant === 'pop_sticker' ? '3px solid #111111' : 'none',
                }}
              >
                <div
                  style={{
                    width: `${barWidth}%`,
                    height: '100%',
                    background: idx % 2 === 0 ? tokens.accentPrimary : tokens.accentSecondary,
                    borderRadius: layout.barRadius,
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
