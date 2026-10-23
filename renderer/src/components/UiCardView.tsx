import React from 'react';
import { useCurrentFrame, interpolate } from 'remotion';

interface UiCardViewProps {
  highlightText: string;
  statItems?: Array<{ label: string; value: string }>;
}

export const UiCardView: React.FC<UiCardViewProps> = ({
  highlightText,
  statItems = [],
}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 10], [0, 1], {
    extrapolateRight: 'clamp',
  });

  return (
    <div
      style={{
        width: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 30,
        opacity,
      }}
    >
      {/* 顶部高亮大字 */}
      <div
        style={{
          padding: '12px 30px',
          background: 'linear-gradient(135deg, rgba(16, 185, 129, 0.25), rgba(56, 189, 248, 0.3))',
          border: '2px solid rgba(16, 185, 129, 0.7)',
          borderRadius: 40,
          color: '#34d399',
          fontSize: 32,
          fontWeight: 800,
          letterSpacing: 2,
          boxShadow: '0 0 30px rgba(16, 185, 129, 0.3)',
          textTransform: 'uppercase',
        }}
      >
        {highlightText}
      </div>

      {/* 科技感毛玻璃特性列表卡片 */}
      <div
        style={{
          width: '100%',
          borderRadius: 24,
          background: 'rgba(15, 23, 42, 0.85)',
          border: '2px solid rgba(16, 185, 129, 0.35)',
          boxShadow: '0 25px 60px rgba(0, 0, 0, 0.8), 0 0 40px rgba(16, 185, 129, 0.15)',
          backdropFilter: 'blur(25px)',
          padding: '36px 30px',
          display: 'flex',
          flexDirection: 'column',
          gap: 24,
          boxSizing: 'border-box',
        }}
      >
        {statItems.map((item, idx) => (
          <div
            key={idx}
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
              padding: '16px 20px',
              background: 'rgba(30, 41, 59, 0.65)',
              borderRadius: 16,
              border: '1px solid rgba(255, 255, 255, 0.08)',
            }}
          >
            <span style={{ fontSize: 18, color: '#94a3b8', fontWeight: 600 }}>
              {item.label}
            </span>
            <span
              style={{
                fontSize: 26,
                color: '#f8fafc',
                fontWeight: 750,
                letterSpacing: 0.5,
              }}
            >
              {item.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};
