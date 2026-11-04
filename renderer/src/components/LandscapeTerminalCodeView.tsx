import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';
import { useTemplate } from '../templates/context';

export interface LandscapeTerminalCodeViewProps {
  sceneIndex: number;
  totalScenes: number;
  entityKey?: string;
  resourceKeyword?: string;
  highlightText: string;
  codeSnippet: string;
  footer_callout?: string;
}

export const LandscapeTerminalCodeView: React.FC<LandscapeTerminalCodeViewProps> = ({
  sceneIndex,
  totalScenes,
  entityKey,
  resourceKeyword,
  highlightText,
  codeSnippet,
  footer_callout,
}) => {
  const frame = useCurrentFrame();
  const { tokens, layout } = useTemplate();

  const entryY = interpolate(frame, [0, 15], [30, 0], { extrapolateRight: 'clamp' });
  const opacity = interpolate(frame, [0, 12], [0, 1], { extrapolateRight: 'clamp' });
  const visibleChars = Math.floor(
    interpolate(frame, [4, 70], [8, codeSnippet.length], { extrapolateRight: 'clamp' })
  );

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
      {/* 顶部标题区（紧凑无裁切） */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
          <span
            style={{
              background: tokens.badgeBg || 'rgba(56, 189, 248, 0.15)',
              color: tokens.badgeText || '#38bdf8',
              padding: '6px 16px',
              borderRadius: 8,
              fontSize: 16,
              fontWeight: 800,
            }}
          >
            SCENE 0{sceneIndex + 1}
          </span>
          <span style={{ fontSize: 28, fontWeight: 900, color: tokens.textPrimary || '#ffffff' }}>
            {highlightText}
          </span>
        </div>
      </div>

      {/* 终端卡片容器（高度固定在 440px 左右，严禁溢出裁切） */}
      <div
        style={{
          background: tokens.codeBg || '#0f172a',
          color: tokens.codeText || '#f8fafc',
          border: '1px solid rgba(255, 255, 255, 0.12)',
          borderRadius: 16,
          boxShadow: '0 16px 36px rgba(0, 0, 0, 0.45)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* macOS 风格窗口栏 */}
        <div
          style={{
            background: 'rgba(255, 255, 255, 0.05)',
            padding: '12px 20px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
          }}
        >
          <div style={{ display: 'flex', gap: 8 }}>
            <span style={{ width: 14, height: 14, borderRadius: '50%', background: '#FF5F56' }} />
            <span style={{ width: 14, height: 14, borderRadius: '50%', background: '#FFBD2E' }} />
            <span style={{ width: 14, height: 14, borderRadius: '50%', background: '#27C93F' }} />
          </div>
          <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 16, fontWeight: 700, color: '#94a3b8' }}>
            quickstart.sh
          </span>
        </div>

        {/* 终端代码打字机区域 */}
        <div
          style={{
            fontFamily: "'JetBrains Mono', monospace",
            padding: '24px 28px',
            fontSize: 22,
            lineHeight: 1.6,
            whiteSpace: 'pre-wrap',
            minHeight: 280,
            maxHeight: 340,
            overflow: 'hidden',
          }}
        >
          {codeSnippet.slice(0, visibleChars)}
          <span
            style={{
              display: 'inline-block',
              width: 12,
              height: 24,
              background: tokens.accentPrimary || '#38bdf8',
              verticalAlign: 'middle',
              marginLeft: 4,
            }}
          />
        </div>
      </div>
    </div>
  );
};
