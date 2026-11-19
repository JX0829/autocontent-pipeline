import React from 'react';
import { AbsoluteFill, Img, staticFile } from 'remotion';

/**
 * 封面卡片（SPEC-20261117 §十一 R13~R18）
 *
 * 底图 = 从**成片里截的一帧真实画面**（由构建侧 ffmpeg 截好后放进 public/runs/<run>/），
 * 文案 = 由我们渲染上去（同 tech_story 的字体族 / 强调色 / 左下版位 / 渐变压暗）。
 *
 * 为什么不用 CoverStill / BilibiliLandscapeCover：那是「海报」版式，与 tech_story 正文
 * 完全不搭（用户 2026-11-18 明确否决）。这里只复刻 HookOverlay 的文字语汇。
 */

const ACCENT = '#5ED8FF';
const FONT = "'PingFang SC','Hiragino Sans GB','Helvetica Neue',sans-serif";

const KICKER: Record<string, string> = {
  'breaking-news': '科技快讯',
  'ai-product': 'AI 产品',
  'tech-github': '开源技术',
};

export type TechCoverProps = {
  /** 底图（相对 renderer/public 的路径，由构建侧截帧后写入） */
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
    ? { left: 120, right: 120, bottom: 210 }
    : { left: 96, right: 96, bottom: 96 };
  const mainSize = portrait ? 88 : 84;

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

      <div style={{ position: 'absolute', left: insets.left, right: insets.right, bottom: insets.bottom }}>
        <div style={{ fontFamily: FONT, fontSize: 22, letterSpacing: 8, color: ACCENT }}>{kickerText}</div>

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
              marginTop: 20, fontFamily: FONT, fontSize: 32, lineHeight: 1.5, color: '#E4EEF7',
              textShadow: '0 4px 22px rgba(0,0,0,0.9)',
            }}
          >
            {sub}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

export default TechCoverCard;
