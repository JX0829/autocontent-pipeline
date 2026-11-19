import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  OffthreadVideo,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  Easing,
} from 'remotion';

/**
 * 水墨课文片（InkStoryVideo）· 模板骨架
 *
 * 画幅：竖屏 1080×1920（30fps）
 *   S1  0–120   开场：AI 墨滴晕染素材 + 右侧竖排标题/作者
 *   S2  120–170 泼墨转场：AI 泼墨素材吃掉画面
 *   S3  170–420 正文：AI 山水背景板 + 四句竖排诗句（右→左，逐字落墨）
 *   S4  420–480 落款：宣纸 + 朱印 + 尾注
 *
 * 竖排铁律（踩过的坑）：
 *   一列 = 一个 vertical-rl 容器，字在容器内自上而下流动；
 *   逐字动效必须写在容器内部的 span 上。
 *   把每字做成独立容器再横向排列 = 视觉上变成横排单字列，且会溢出画面被裁切。
 */

import { InkBackdropLayer } from './InkBackdrop';

export type InkLine = {
  text: string;
  /** 小字释义，可空 */
  gloss?: string;
};

export type InkStoryProps = {
  title: string;
  byline: string;
  lines: InkLine[];
  colophon: string;
  seal: string;
  paper?: string;
  ink?: string;
  cinnabar?: string;
  assets?: {
    // 2026-11-19（方案 C）：素材库 6 槽位
    open?: string | null;     // 开场：墨滴入纸/笔触
    reveal?: string | null;   // 揭示：雾散/水波
    bg?: string | null;       // 正文底：山水/竹林/江面
    mid?: string | null;      // 幕间转场：泼墨/飘叶
    end?: string | null;      // 收尾底：空白纸/残墨
    tex?: string | null;      // 纸纹叠加
    // 旧三槽（Root.tsx 默认值仍在用，保留兼容）
    drop?: string | null;
    mountains?: string | null;
    splash?: string | null;
  };
};

const PAPER = '#EFE6D4';
const INK = '#1C1A17';
const CINNABAR = '#A6261F';
const KAI = "'STKaiti','Kaiti SC','Songti SC',serif";

/** 逐字落墨：按字序错开，模糊+位移 → 清晰 */
const useCharReveal = (start: number, perChar: number) => {
  const frame = useCurrentFrame();
  return (i: number): React.CSSProperties => {
    const t = interpolate(frame, [start + i * perChar, start + i * perChar + perChar * 1.6], [0, 1], {
      easing: Easing.out(Easing.cubic),
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
    return {
      opacity: t,
      transform: `translateY(${interpolate(t, [0, 1], [12, 0])}px)`,
      filter: `blur(${interpolate(t, [0, 0.5, 1], [8, 2, 0])}px)`,
    };
  };
};

/**
 * 竖排文字：一列一个纵向 flex 容器，字逐个向下堆叠。
 *
 * 为什么不用 writing-mode: vertical-rl？
 *   headless Chrome 里 vertical-rl 容器放在 flex 里时宽高会塌成 0，文字整个不显示
 *   （2026-11-16 实测：still 渲染出来正文/落款一片空白）。
 *   用 flexDirection: 'column' 逐字堆叠，效果等价且稳定，逐字动效直接写在字上。
 */
const VText: React.FC<{
  text: string;
  style?: React.CSSProperties;
  charStyle?: (i: number) => React.CSSProperties;
}> = ({ text, style, charStyle }) => (
  <div
    style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      flexShrink: 0,
      fontFamily: KAI,
      color: INK,
      lineHeight: 1.18,
      textShadow: '0 0 18px rgba(239,230,212,0.9)',
      ...style,
    }}
  >
    {Array.from(text).map((ch, i) => (
      <span key={i} style={{ display: 'block', ...(charStyle ? charStyle(i) : {}) }}>
        {ch}
      </span>
    ))}
  </div>
);

/** 纸面 + 纤维纹理（素材缺失时的兜底） */
const PaperBase: React.FC<{ paper: string }> = ({ paper }) => {
  const { width, height } = useVideoConfig();
  return (
    <AbsoluteFill style={{ background: paper }}>
      <svg width={width} height={height} style={{ position: 'absolute', inset: 0 }}>
        <defs>
          <filter id="fiberBase">
            <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="4" seed={7} result="n" />
            <feColorMatrix in="n" type="saturate" values="0" result="g" />
            <feComponentTransfer in="g">
              <feFuncA type="linear" slope="0.05" />
            </feComponentTransfer>
          </filter>
        </defs>
        <rect width={width} height={height} fill={paper} filter="url(#fiberBase)" />
      </svg>
    </AbsoluteFill>
  );
};

/** S1 · 开场 */
const Opening: React.FC<{ props: InkStoryProps }> = ({ props }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const reveal = useCharReveal(46, 9);
  const bylineT = interpolate(frame, [92, 116], [0, 1], {
    easing: Easing.out(Easing.cubic), extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const kickerT = interpolate(frame, [34, 62], [0, 0.55], {
    easing: Easing.out(Easing.cubic), extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  // 结尾淡回纸面，为下一幕的墨晕揭示让路（硬切会显得突兀）
  const washT = interpolate(frame, [durationInFrames - 40, durationInFrames], [0, 1], {
    easing: Easing.inOut(Easing.quad), extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill>
      {props.assets?.drop ? (
        <OffthreadVideo
          src={staticFile(props.assets?.open ?? props.assets?.drop)}
          muted
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
        />
      ) : (
        <PaperBase paper={props.paper || PAPER} />
      )}

      {/* 右侧竖排：标题 + 作者（标题在右，作者在其左） */}
      <div
        style={{
          position: 'absolute',
          right: 104,
          top: 300,
          display: 'flex',
          flexDirection: 'row-reverse',
          alignItems: 'flex-start',
          gap: 20,
        }}
      >
        <VText
          text={props.title}
          style={{ fontSize: 112, fontWeight: 600, letterSpacing: 12 }}
          charStyle={reveal}
        />
        <VText
          text={props.byline}
          style={{ fontSize: 32, letterSpacing: 8, color: props.cinnabar || CINNABAR, opacity: bylineT, marginTop: 16 }}
        />
      </div>

      {/* 结尾纸面回洗：盖掉墨色，接下一幕的柔和揭示 */}
      <AbsoluteFill style={{ background: props.paper || PAPER, opacity: washT }} />

      {/* 栏目名（底部，横排小字） */}
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'flex-end', paddingBottom: 168 }}>
        <div style={{ fontFamily: KAI, fontSize: 30, letterSpacing: 14, color: INK, opacity: kickerT * (1 - washT) }}>
          课 文 背 后 的 故 事
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/** S2 · 泼墨转场 */
const Splash: React.FC<{ props: InkStoryProps }> = ({ props }) => (
  <AbsoluteFill style={{ background: '#000' }}>
    {props.assets?.splash ? (
      <OffthreadVideo
        src={staticFile(props.assets?.mid ?? props.assets?.splash)}
        startFrom={240}
        muted
        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
      />
    ) : (
      <AbsoluteFill style={{ background: '#0b0a09' }} />
    )}
  </AbsoluteFill>
);

/** 单列诗句（独立组件：hook 不能写在 map 里） */
const PoemLine: React.FC<{ line: InkLine; index: number }> = ({ line, index }) => {
  const frame = useCurrentFrame();
  const start = 54 + index * 44;
  const reveal = useCharReveal(start, 6);
  const colT = interpolate(frame, [start, start + 18], [0, 1], {
    easing: Easing.out(Easing.cubic), extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'row-reverse', alignItems: 'flex-start', opacity: colT }}>
      <VText text={line.text} style={{ fontSize: 62, letterSpacing: 6 }} charStyle={reveal} />
      {line.gloss ? (
        <VText
          text={line.gloss}
          style={{ fontSize: 22, letterSpacing: 2, opacity: 0.45 * colT, marginRight: 10, marginTop: 88 }}
        />
      ) : null}
    </div>
  );
};

/** S3 · 正文：山水背景 + 竖排诗句 */
const Body: React.FC<{ props: InkStoryProps }> = ({ props }) => {
  const frame = useCurrentFrame();
  // 从画面中心像墨晕一样柔和漫开（软边遮罩，代替泼墨硬转场）
  const radius = interpolate(frame, [0, 84], [0, 1500], {
    easing: Easing.out(Easing.cubic), extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const revealMask = `radial-gradient(circle at 50% 54%, #000 ${radius * 0.72}px, transparent ${radius}px)`;
  const enter = interpolate(frame, [0, 20], [0, 1], {
    easing: Easing.out(Easing.cubic), extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill style={{ background: props.paper || PAPER }}>
      <AbsoluteFill style={{ maskImage: revealMask, WebkitMaskImage: revealMask }}>
        {props.assets?.mountains ? (
          <OffthreadVideo
            src={staticFile(props.assets?.bg ?? props.assets?.mountains)}
            muted
            style={{ width: '100%', height: '100%', objectFit: 'cover', opacity: enter }}
          />
        ) : (
          <PaperBase paper={props.paper || PAPER} />
        )}
      </AbsoluteFill>

      {/* 诗句：四列竖排，右 → 左 */}
      <div
        style={{
          position: 'absolute',
          left: 70,
          right: 70,
          top: 130,
          bottom: 240,
          display: 'flex',
          flexDirection: 'row-reverse',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 40,
        }}
      >
        {props.lines.map((line, li) => (
          <PoemLine key={li} line={line} index={li} />
        ))}
      </div>

      {/* 结尾回洗到纸面，接落款页 */}
      <AbsoluteFill
        style={{
          background: props.paper || PAPER,
          opacity: interpolate(frame, [216, 258], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
        }}
      />

      {/* 右下角篇名 */}
      <div
        style={{
          position: 'absolute',
          bottom: 104,
          right: 92,
          fontFamily: KAI,
          fontSize: 26,
          letterSpacing: 6,
          color: INK,
          opacity: 0.45,
        }}
      >
        《{props.title}》· {props.byline}
      </div>
    </AbsoluteFill>
  );
};

/** S4 · 落款 */
const Colophon: React.FC<{ props: InkStoryProps }> = ({ props }) => {
  const frame = useCurrentFrame();
  const t = interpolate(frame, [0, 26], [0, 1], {
    easing: Easing.out(Easing.cubic), extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const sealT = interpolate(frame, [22, 46], [0, 1], {
    easing: Easing.out(Easing.back(2)), extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill>
      <PaperBase paper={props.paper || PAPER} />

      {/* 墨尘飘落：落款页也要有缓慢运动 */}
      <svg width="1080" height="1920" style={{ position: 'absolute', inset: 0 }}>
        {Array.from({ length: 12 }, (_, i) => {
          const tt = interpolate(frame, [0, 60], [0, 1], { extrapolateRight: 'clamp' });
          const x = 1080 * (0.1 + (((i * 37) % 100) / 100) * 0.8);
          const y0 = 1920 * (0.02 + (((i * 53) % 100) / 100) * 0.4);
          return (
            <circle
              key={i}
              cx={x + Math.sin(frame / 22 + i) * 12}
              cy={y0 + tt * (150 + (i % 5) * 80)}
              r={3 + (i % 4) * 1.8}
              fill={props.ink || INK}
              opacity={tt * (0.22 + (i % 3) * 0.09)}
            />
          );
        })}
      </svg>

      <AbsoluteFill
        style={{
          alignItems: 'center',
          justifyContent: 'center',
          transform: `scale(${interpolate(frame, [0, 60], [1.0, 1.085], { extrapolateRight: 'clamp' })})`,
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'row-reverse', alignItems: 'flex-start', gap: 44, opacity: t }}>
          <VText text={props.title} style={{ fontSize: 92, letterSpacing: 10 }} />
          <div
            style={{
              width: 108,
              height: 108,
              border: `4px solid ${props.cinnabar || CINNABAR}`,
              borderRadius: 8,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontFamily: KAI,
              fontSize: 42,
              color: props.cinnabar || CINNABAR,
              letterSpacing: 2,
              opacity: sealT,
              transform: `translateY(${interpolate(sealT, [0, 1], [18, 0])}px) rotate(-3deg) scale(${1 + Math.sin(frame / 30) * 0.012})`,
              marginTop: 18,
            }}
          >
            {props.seal}
          </div>
        </div>
        <div style={{ fontFamily: KAI, fontSize: 30, letterSpacing: 6, color: INK, opacity: 0.6 * t, marginTop: 56 }}>
          {props.colophon}
        </div>
        <div
          style={{
            marginTop: 26,
            height: 3,
            width: interpolate(frame, [8, 60], [0, 260], { extrapolateRight: 'clamp' }),
            background: INK,
            opacity: 0.5,
          }}
        />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

export const InkStoryVideo: React.FC<InkStoryProps> = (props) => (
  <AbsoluteFill style={{ background: props.paper || PAPER }}>
    {/* 方案 C：素材库水墨底（bg）+ 纸纹（tex）；缺素材时什么都不画，保留纯纸色 */}
    <InkBackdropLayer bg={props.assets?.bg ?? props.assets?.mountains} tex={props.assets?.tex} />
    <Sequence from={0} durationInFrames={120} name="S1 开场">
      <Opening props={props} />
    </Sequence>
    <Sequence from={120} durationInFrames={300} name="S2 正文（墨晕揭示）">
      <Body props={props} />
    </Sequence>
    <Sequence from={420} durationInFrames={60} name="S4 落款">
      <Colophon props={props} />
    </Sequence>
  </AbsoluteFill>
);
