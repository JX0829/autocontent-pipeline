import React from 'react';
import { AbsoluteFill } from 'remotion';
import { useTemplate } from '../templates/context';

export interface SafeAreaContainerProps {
  children: React.ReactNode;
  showDebugBorder?: boolean;
}

/**
 * 严格遵循仓库校准后的抖音竖屏安全区坐标:
 * left: 96px, top: 214px, width: 888px, height: 1326px (1080x1920 画布)
 */
export const SafeAreaContainer: React.FC<SafeAreaContainerProps> = ({
  children,
  showDebugBorder = false,
}) => {
  const { id, tokens } = useTemplate();

  const getPatternStyle = (): React.CSSProperties => {
    if (tokens.patternType === 'paper_grid' || tokens.patternType === 'linear_grid') {
      return {
        backgroundImage: `linear-gradient(to right, ${tokens.patternColor} 1px, transparent 1px), linear-gradient(to bottom, ${tokens.patternColor} 1px, transparent 1px)`,
        backgroundSize: `${tokens.patternSizePx}px ${tokens.patternSizePx}px`,
      };
    }
    if (tokens.patternType === 'dot_matrix' || tokens.patternType === 'pop_halftone') {
      return {
        backgroundImage: `radial-gradient(${tokens.patternColor} 2.2px, transparent 2.2px)`,
        backgroundSize: `${tokens.patternSizePx}px ${tokens.patternSizePx}px`,
      };
    }
    return {};
  };

  return (
    <AbsoluteFill
      style={{
        background: tokens.canvasBg,
        color: tokens.textPrimary,
        fontFamily: "'Inter', 'Noto Sans SC', 'PingFang SC', sans-serif",
        overflow: 'hidden',
        ...getPatternStyle(),
      }}
    >
      {/* 模板专属环境光装饰 */}
      {tokens.ambientGlowPrimary && (
        <div
          style={{
            position: 'absolute',
            top: 80,
            left: -120,
            width: 640,
            height: 640,
            borderRadius: '50%',
            background: `radial-gradient(circle, ${tokens.ambientGlowPrimary} 0%, transparent 70%)`,
            filter: 'blur(40px)',
            pointerEvents: 'none',
          }}
        />
      )}
      {tokens.ambientGlowSecondary && (
        <div
          style={{
            position: 'absolute',
            bottom: 240,
            right: -100,
            width: 680,
            height: 680,
            borderRadius: '50%',
            background: `radial-gradient(circle, ${tokens.ambientGlowSecondary} 0%, transparent 70%)`,
            filter: 'blur(48px)',
            pointerEvents: 'none',
          }}
        />
      )}

      {/* 瑞士报刊风顶部双线社论分割线 */}
      {id === 'editorial_news' && (
        <>
          <div
            style={{
              position: 'absolute',
              top: 168,
              left: 96,
              width: 888,
              height: 4,
              background: '#141413',
            }}
          />
          <div
            style={{
              position: 'absolute',
              top: 178,
              left: 96,
              width: 888,
              height: 1,
              background: '#141413',
            }}
          />
        </>
      )}

      {/* 核心安全区容器 */}
      <div
        style={{
          position: 'absolute',
          left: 96,
          top: 214,
          width: 888,
          height: 1326,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'flex-start',
          boxSizing: 'border-box',
          border: showDebugBorder ? '3px dashed #F43F5E' : 'none',
        }}
      >
        {children}
      </div>
    </AbsoluteFill>
  );
};
