import React from 'react';
import { AbsoluteFill, Img, staticFile } from 'remotion';
import {
  COVER_KICKER_SIZE,
  COVER_MAIN_SIZE,
  COVER_OFFSET_Y,
  COVER_PAD_X,
  COVER_SUB_SIZE,
} from './coverFrame';

const ACCENT = '#5ED8FF';

/**
 * 封面卡片（SPEC-20261117 §十一 R13~R18）
 *
 * 底图 = **无文字素材/背景 loop 的一帧**（由 cover_compose.pick_clean_source 挑好、
 * 居中裁成 3:4 后放进 public/runs/<run>/），文案 = 这里渲染上去。
 *
 * 版式一律吃 ../tech/coverFrame.ts 的统一常量（居中 / 下移 32 / 边距 ≥64 / 字号 76-28-20），
 * 新模板请复用同一组常量，不要抄数字。
 */
export {
  COVER_SAFE_34_TOP,
  COVER_SAFE_34_BOTTOM,
} from './coverFrame';

const FONT = "'PingFang SC','Hiragino Sans GB','Helvetica Neue',sans-serif";

const KICKER: Record<string, string> = {
  'breaking-news': '科技快讯',
  'ai-product': 'AI 产品',
  'tech-github': '开源技术',
};

export type TechCoverProps = {
  /** 底图（相对 renderer/public 的路径；由 cover_compose 从无字素材/背景 loop 截出并裁好 3:4） */
  bg: string;
  main: string;
  sub?: string;
  kicker?: string;
  theme?: string;
  aspect?: 'portrait' | 'landscape';
};

export const TechCoverCard: React.FC<TechCoverProps> = ({ bg, main, sub, kicker, theme, aspect }) => {
  const portrait = (aspect ?? 'portrait') !== 'landscape';
  const kickerText = kicker || KICKER[theme || ''] || '科技速递';
  const insets = portrait
    ? { left: COVER_PAD_X, right: COVER_PAD_X, bottom: 96 }
    : { left: 96, right: 96, bottom: 96 };
  const mainSize = portrait ? COVER_MAIN_SIZE : 84;
  const subSize = portrait ? COVER_SUB_SIZE : 32;
  const kickerSize = portrait ? COVER_KICKER_SIZE : 22;

  return (
    <AbsoluteFill style={{ background: '#04060a' }}>
      <Img src={staticFile(bg)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />

      {/* 与正文同款压暗：底部更重，保证文案区对比度（R15 可读性前提） */}
      <AbsoluteFill
        style={{
          background: portrait
            ? 'linear-gradient(to bottom, rgba(3,5,9,0.55) 0%, rgba(3,5,9,0.18) 34%, rgba(3,5,9,0.72) 66%, rgba(3,5,9,0.94) 100%)'
            : 'linear-gradient(to bottom, rgba(3,5,9,0.50) 0%, rgba(3,5,9,0.16) 38%, rgba(3,5,9,0.82) 78%, rgba(3,5,9,0.96) 100%)',
        }}
      />

      <div
        style={portrait
          ? {
              position: 'absolute', inset: 0,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }
          : { position: 'absolute', left: insets.left, right: insets.right, bottom: insets.bottom }}
      >
        <div
          style={portrait
            ? {
                width: '100%', paddingLeft: insets.left, paddingRight: insets.right,
                textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center',
                transform: `translateY(${COVER_OFFSET_Y}px)`,
              }
            : undefined}
        >
        <div style={{ fontFamily: FONT, fontSize: kickerSize, letterSpacing: 8, color: ACCENT }}>{kickerText}</div>

        <div
          style={{
            marginTop: 22, fontFamily: FONT, fontSize: mainSize, fontWeight: 800, lineHeight: 1.2,
            letterSpacing: 1, color: '#FFFFFF',
            textShadow: '0 8px 40px rgba(0,0,0,0.92), 0 2px 10px rgba(0,0,0,0.85)',
          }}
        >
          {main}
        </div>

        <div style={{ marginTop: 22, width: 150, height: 5, background: ACCENT, borderRadius: 3 }} />

        {sub ? (
          <div
            style={{
              marginTop: 20, fontFamily: FONT, fontSize: subSize, lineHeight: 1.5, color: '#E4EEF7',
              textShadow: '0 4px 22px rgba(0,0,0,0.9)',
              maxWidth: 900, whiteSpace: 'pre-wrap',
            }}
          >
            {sub}
          </div>
        ) : null}
        </div>
      </div>
    </AbsoluteFill>
  );
};

export default TechCoverCard;
