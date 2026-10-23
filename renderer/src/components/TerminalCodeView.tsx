import React from 'react';
import { useCurrentFrame, interpolate } from 'remotion';

interface TerminalCodeViewProps {
  codeSnippet?: string;
  highlightText: string;
}

export const TerminalCodeView: React.FC<TerminalCodeViewProps> = ({
  codeSnippet = '',
  highlightText,
}) => {
  const frame = useCurrentFrame();
  const charsShown = Math.floor(interpolate(frame, [0, 45], [0, codeSnippet.length], {
    extrapolateRight: 'clamp',
  }));
  const displayedCode = codeSnippet.slice(0, charsShown);

  return (
    <div
      style={{
        width: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 30,
      }}
    >
      {/* 顶部醒目大标题徽章 */}
      <div
        style={{
          padding: '12px 28px',
          background: 'linear-gradient(135deg, rgba(56, 189, 248, 0.25), rgba(99, 102, 241, 0.3))',
          border: '2px solid rgba(56, 189, 248, 0.7)',
          borderRadius: 40,
          color: '#38bdf8',
          fontSize: 32,
          fontWeight: 800,
          letterSpacing: 2,
          boxShadow: '0 0 30px rgba(56, 189, 248, 0.3)',
          textTransform: 'uppercase',
        }}
      >
        {highlightText}
      </div>

      {/* 极客终端黑客风代码卡片 */}
      <div
        style={{
          width: '100%',
          borderRadius: 24,
          background: 'rgba(15, 23, 42, 0.88)',
          border: '2px solid rgba(56, 189, 248, 0.35)',
          boxShadow: '0 25px 60px rgba(0, 0, 0, 0.8), 0 0 40px rgba(56, 189, 248, 0.15)',
          backdropFilter: 'blur(20px)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* 终端红黄绿按钮栏 */}
        <div
          style={{
            height: 48,
            background: 'rgba(30, 41, 59, 0.95)',
            display: 'flex',
            alignItems: 'center',
            padding: '0 20px',
            gap: 12,
            borderBottom: '1px solid rgba(255, 255, 255, 0.1)',
          }}
        >
          <div style={{ width: 14, height: 14, borderRadius: '50%', background: '#ef4444' }} />
          <div style={{ width: 14, height: 14, borderRadius: '50%', background: '#f59e0b' }} />
          <div style={{ width: 14, height: 14, borderRadius: '50%', background: '#10b981' }} />
          <span style={{ marginLeft: 'auto', color: '#94a3b8', fontSize: 16, fontFamily: 'monospace' }}>
            bash - python3
          </span>
        </div>

        {/* 终端代码正文 */}
        <div
          style={{
            padding: 30,
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
            fontSize: 26,
            lineHeight: 1.6,
            color: '#f8fafc',
            whiteSpace: 'pre-wrap',
            minHeight: 280,
            maxHeight: 520,
            overflow: 'hidden',
            textShadow: '0 0 10px rgba(255,255,255,0.2)',
          }}
        >
          {displayedCode}
          <span
            style={{
              display: 'inline-block',
              width: 14,
              height: 28,
              background: '#38bdf8',
              marginLeft: 4,
              verticalAlign: 'middle',
              opacity: Math.sin(frame / 4) > 0 ? 1 : 0,
            }}
          />
        </div>
      </div>
    </div>
  );
};
