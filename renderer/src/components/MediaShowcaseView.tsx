import React from 'react';
import { Img, interpolate, staticFile, useCurrentFrame } from 'remotion';
import { useTemplate } from '../templates/context';
import { SceneHeaderBanner } from './SceneHeaderBanner';

export interface MediaShowcaseViewProps {
  sceneIndex: number;
  totalScenes: number;
  entityKey?: string;
  resourceKeyword?: string;
  highlightText: string;
  mediaAssetUrl?: string;
}

export const MediaShowcaseView: React.FC<MediaShowcaseViewProps> = ({
  sceneIndex,
  totalScenes,
  entityKey,
  resourceKeyword,
  highlightText,
  mediaAssetUrl,
}) => {
  const frame = useCurrentFrame();
  const { tokens, layout } = useTemplate();

  const zoom = interpolate(frame, [0, 120], [1.0, 1.04], { extrapolateRight: 'clamp' });
  const defaultNodes = [
    '📥 原始多模态输入',
    '⚡ 核心架构引擎处理',
    '📝 结构化高纯度输出',
    '🚀 生产级自动化闭环',
  ];

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
          padding: 44,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 32,
            paddingBottom: 20,
            borderBottom: '2px dashed rgba(148, 163, 184, 0.3)',
          }}
        >
          <span
            style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 28,
              fontWeight: 800,
              color: tokens.accentPrimary,
            }}
          >
            FIG 1.0 · ARCHITECTURE SHOWCASE
          </span>
          <span style={{ fontSize: 26, fontWeight: 700, color: tokens.textSecondary }}>
            LIVE PREVIEW
          </span>
        </div>

        {mediaAssetUrl ? (
          <div style={{ overflow: 'hidden', borderRadius: 20, transform: `scale(${zoom})` }}>
            <Img
              src={staticFile(mediaAssetUrl)}
              style={{ width: '100%', height: 520, objectFit: 'cover' }}
            />
          </div>
        ) : (
          <div
            style={{
              transform: `scale(${zoom})`,
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 28,
              padding: 12,
            }}
          >
            {defaultNodes.map((node, idx) => (
              <div
                key={idx}
                style={{
                  background: idx === 1 ? tokens.accentPrimary : tokens.codeBg,
                  color: idx === 1 ? '#FFFFFF' : tokens.codeText,
                  padding: '38px 28px',
                  borderRadius: 22,
                  border:
                    layout.cardVariant === 'pop_sticker'
                      ? '4px solid #111111'
                      : '1px solid rgba(255, 255, 255, 0.15)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 14,
                }}
              >
                <span
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 24,
                    opacity: 0.75,
                    fontWeight: 700,
                  }}
                >
                  STAGE 0{idx + 1}
                </span>
                <span style={{ fontSize: 34, fontWeight: 900, lineHeight: 1.3 }}>{node}</span>
              </div>
            ))}
          </div>
        )}

        {resourceKeyword && (
          <div
            style={{
              marginTop: 36,
              padding: '26px 32px',
              borderRadius: 18,
              background: tokens.badgeBg,
              color: tokens.badgeText,
              fontSize: 32,
              fontWeight: 800,
              textAlign: 'center',
            }}
          >
            💬 评论区回复【{resourceKeyword}】获取完整架构图与部署包
          </div>
        )}
      </div>
    </div>
  );
};
