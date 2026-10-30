import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';
import { useTemplate } from '../templates/context';
import { SceneHeaderBanner } from './SceneHeaderBanner';

export interface TerminalCodeViewProps {
  sceneIndex: number;
  totalScenes: number;
  entityKey?: string;
  resourceKeyword?: string;
  highlightText: string;
  codeSnippet: string;
  footer_callout?: string;
}

export const TerminalCodeView: React.FC<TerminalCodeViewProps> = ({
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

  const entryY = interpolate(frame, [0, 18], [45, 0], { extrapolateRight: 'clamp' });
  const opacity = interpolate(frame, [0, 14], [0, 1], { extrapolateRight: 'clamp' });
  const visibleChars = Math.floor(
    interpolate(frame, [6, 85], [10, codeSnippet.length], { extrapolateRight: 'clamp' })
  );

  const renderWindowHeader = () => {
    if (layout.terminalVariant === 'telegraph_dispatch') {
      return (
        <div
          style={{
            background: tokens.codeHeaderBg,
            color: tokens.codeHeaderText,
            padding: '18px 32px',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            fontSize: 26,
            fontWeight: 800,
            letterSpacing: 2,
          }}
        >
          <span>TELEGRAPH CODE DISPATCH</span>
          <span>VERIFIED SOURCE</span>
        </div>
      );
    }

    if (layout.terminalVariant === 'notion_callout') {
      return (
        <div
          style={{
            background: tokens.codeHeaderBg,
            color: tokens.codeHeaderText,
            padding: '20px 32px',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            fontSize: 26,
            fontWeight: 700,
          }}
        >
          <span>💡 核心命令与代码笔记</span>
          <span style={{ fontFamily: 'monospace', opacity: 0.8 }}>COPY SNIPPET</span>
        </div>
      );
    }

    return (
      <div
        style={{
          background: tokens.codeHeaderBg,
          color: tokens.codeHeaderText,
          padding: '22px 32px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom:
            layout.terminalVariant === 'brutalist_window'
              ? '4px solid #111111'
              : '1px solid rgba(255,255,255,0.1)',
        }}
      >
        <div style={{ display: 'flex', gap: 14 }}>
          <span style={{ width: 22, height: 22, borderRadius: '50%', background: '#FF5F56' }} />
          <span style={{ width: 22, height: 22, borderRadius: '50%', background: '#FFBD2E' }} />
          <span style={{ width: 22, height: 22, borderRadius: '50%', background: '#27C93F' }} />
        </div>
        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 26, fontWeight: 700 }}>
          quickstart.py
        </span>
      </div>
    );
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
          transform: `translateY(${entryY}px)`,
          opacity,
          background: tokens.codeBg,
          color: tokens.codeText,
          border: tokens.cardBorder,
          borderRadius: tokens.cardRadius,
          boxShadow: tokens.cardShadow,
          overflow: 'hidden',
        }}
      >
        {renderWindowHeader()}
        <div
          style={{
            fontFamily: "'JetBrains Mono', monospace",
            padding: '44px 40px',
            fontSize: 33,
            lineHeight: 1.65,
            whiteSpace: 'pre-wrap',
            minHeight: 480,
          }}
        >
          {codeSnippet.slice(0, visibleChars)}
          <span
            style={{
              display: 'inline-block',
              width: 18,
              height: 36,
              background: tokens.accentPrimary,
              verticalAlign: 'middle',
              marginLeft: 6,
            }}
          />
        </div>
      </div>

      {resourceKeyword && (
        <div
          style={{
            marginTop: 36,
            background: tokens.cardBg,
            border: tokens.cardBorder,
            borderRadius: tokens.cardRadius,
            boxShadow: tokens.cardShadow,
            padding: '30px 38px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div>
            <div style={{ fontSize: 24, fontWeight: 700, color: tokens.textSecondary }}>
              开源检索标识 / ENTITY
            </div>
            <div style={{ fontSize: 32, fontWeight: 900, color: tokens.textPrimary, marginTop: 4 }}>
              {entityKey || 'GitHub Trending'}
            </div>
          </div>
          <div
            style={{
              background: tokens.accentPrimary,
              color: '#FFFFFF',
              padding: '16px 32px',
              borderRadius: 16,
              fontSize: 26,
              fontWeight: 800,
            }}
          >
            {footer_callout || '💡 欢迎评论区交流'}
          </div>
        </div>
      )}
    </div>
  );
};
