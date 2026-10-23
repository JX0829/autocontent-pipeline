import React from 'react';
import { useCurrentFrame, interpolate } from 'remotion';

interface MediaShowcaseViewProps {
  highlightText: string;
  mediaAssetUrl?: string;
  caption?: string;
}

export const MediaShowcaseView: React.FC<MediaShowcaseViewProps> = ({
  highlightText,
  mediaAssetUrl,
  caption = '实测效果演示',
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
          background: 'linear-gradient(135deg, rgba(168, 85, 247, 0.25), rgba(236, 72, 153, 0.3))',
          border: '2px solid rgba(168, 85, 247, 0.7)',
          borderRadius: 40,
          color: '#c084fc',
          fontSize: 32,
          fontWeight: 800,
          letterSpacing: 2,
          boxShadow: '0 0 30px rgba(168, 85, 247, 0.3)',
          textTransform: 'uppercase',
        }}
      >
        {highlightText}
      </div>

      {/* 浏览器外框媒体展示容器 */}
      <div
        style={{
          width: '100%',
          borderRadius: 24,
          background: 'rgba(15, 23, 42, 0.9)',
          border: '2px solid rgba(168, 85, 247, 0.35)',
          boxShadow: '0 25px 60px rgba(0, 0, 0, 0.8), 0 0 40px rgba(168, 85, 247, 0.15)',
          backdropFilter: 'blur(20px)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* 浏览器标签栏 */}
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
          <div
            style={{
              marginLeft: 20,
              flex: 1,
              height: 28,
              background: 'rgba(15, 23, 42, 0.6)',
              borderRadius: 6,
              display: 'flex',
              alignItems: 'center',
              padding: '0 12px',
              color: '#94a3b8',
              fontSize: 14,
              fontFamily: 'monospace',
            }}
          >
            https://github.com/microsoft/markitdown
          </div>
        </div>

        {/* 媒体演示区域 */}
        <div
          style={{
            minHeight: 320,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            alignItems: 'center',
            padding: 30,
            background: 'radial-gradient(circle at center, rgba(30, 41, 59, 0.8), rgba(15, 23, 42, 0.95))',
          }}
        >
          {mediaAssetUrl ? (
            <img
              src={mediaAssetUrl}
              alt="demo"
              style={{
                maxWidth: '100%',
                maxHeight: 340,
                borderRadius: 12,
                boxShadow: '0 10px 30px rgba(0,0,0,0.5)',
              }}
            />
          ) : (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: 16,
                color: '#e2e8f0',
              }}
            >
              <div
                style={{
                  fontSize: 64,
                  filter: 'drop-shadow(0 0 20px rgba(168, 85, 247, 0.5))',
                }}
              >
                ⚡
              </div>
              <span style={{ fontSize: 24, fontWeight: 700, color: '#f8fafc' }}>
                {caption}
              </span>
              <span style={{ fontSize: 16, color: '#94a3b8' }}>
                自动提取与结构化解析中...
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
