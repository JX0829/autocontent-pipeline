import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';
import { useTemplate } from '../templates/context';

export interface LandscapeStatChartViewProps {
  sceneIndex: number;
  totalScenes: number;
  entityKey?: string;
  highlightText: string;
  statItems: Array<{ label: string; value: string }>;
}

export const LandscapeStatChartView: React.FC<LandscapeStatChartViewProps> = ({
  sceneIndex,
  totalScenes,
  entityKey,
  highlightText,
  statItems,
}) => {
  const frame = useCurrentFrame();
  const { tokens } = useTemplate();

  const entryY = interpolate(frame, [0, 15], [30, 0], { extrapolateRight: 'clamp' });
  const opacity = interpolate(frame, [0, 12], [0, 1], { extrapolateRight: 'clamp' });

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        width: '100%',
        height: '100%',
        justifyContent: 'center',
        transform: `translateY(${entryY}px)`,
        opacity,
      }}
    >
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
          <span
            style={{
              background: 'rgba(168, 85, 247, 0.15)',
              color: '#c084fc',
              padding: '6px 16px',
              borderRadius: 8,
              fontSize: 16,
              fontWeight: 800,
            }}
          >
            SCENE 0{sceneIndex + 1} · 核心对比
          </span>
          <span style={{ fontSize: 28, fontWeight: 900, color: tokens.textPrimary || '#ffffff' }}>
            {highlightText}
          </span>
        </div>
      </div>

      <div
        style={{
          background: 'rgba(15, 23, 42, 0.9)',
          border: '1px solid rgba(168, 85, 247, 0.25)',
          borderRadius: 20,
          padding: '30px 36px',
          boxShadow: '0 16px 36px rgba(0, 0, 0, 0.45)',
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
        }}
      >
        {statItems.map((item, idx) => (
          <div
            key={idx}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '16px 22px',
              background: 'rgba(255, 255, 255, 0.04)',
              borderRadius: 12,
              border: '1px solid rgba(255, 255, 255, 0.06)',
            }}
          >
            <span style={{ color: '#94a3b8', fontSize: 20, fontWeight: 600 }}>{item.label}</span>
            <span style={{ color: idx === 0 ? '#38bdf8' : '#c084fc', fontSize: 26, fontWeight: 900 }}>
              {item.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};
