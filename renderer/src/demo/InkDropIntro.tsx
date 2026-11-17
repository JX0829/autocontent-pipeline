import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
  Easing,
} from 'remotion';

/**
 * 水墨开场片段（demo · 只做开场）
 *
 * 分镜：
 *   0–18f   一滴墨从天而降（受重力加速，拉出尾迹）
 *   18–22f  砸在屏幕中央（压扁回弹 + 墨点飞溅）
 *   20–70f  墨晕向四周晕染（多层不规则墨边，靠 feTurbulence 位移）
 *   22–78f  水波纹涟漪（三圈同心环扩散、透明度衰减）
 *   30–112f 画面徐徐展开（纸色从中心撑开，把墨面冲成宣纸）
 *   88–150f 标题显影（逐字墨迹浮现 + 轻微放大收束）
 *
 * 技术：纯 SVG（feTurbulence / feDisplacementMap）+ framer 无关的
 * `interpolate`，不引入任何新依赖。
 */

export type InkDropIntroProps = {
  kicker: string;
  title: string;
  paperColor: string;
  inkColor: string;
  accentColor: string;
};

const SEED = 20261116;

/** 确定性伪随机（保证多次渲染同一帧完全一致） */
function rnd(i: number): number {
  const x = Math.sin(i * 12.9898 + SEED * 0.0001) * 43758.5453;
  return x - Math.floor(x);
}

export const InkDropIntro: React.FC<InkDropIntroProps> = ({
  kicker,
  title,
  paperColor,
  inkColor,
  accentColor,
}) => {
  const frame = useCurrentFrame();
  const { width, height, fps } = useVideoConfig();
  const cx = width / 2;
  const cy = height * 0.46;

  // ── 1. 墨滴下落：重力加速 ─────────────────────────────────────────────
  const fall = interpolate(frame, [0, 18], [0, 1], {
    easing: Easing.in(Easing.quad),
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const dropY = interpolate(fall, [0, 1], [-height * 0.35, cy]);
  const dropStretch = interpolate(fall, [0, 1], [1, 2.15]); // 越快越长
  const dropVisible = frame < 20;

  // ── 2. 撞击：压扁 → 回弹 ────────────────────────────────────────────
  const hit = interpolate(frame, [18, 22], [0, 1], {
    easing: Easing.out(Easing.cubic),
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const splash = frame >= 18 ? interpolate(frame, [18, 34], [0, 1], {
    easing: Easing.out(Easing.quad),
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  }) : 0;

  // ── 3. 墨晕生长：四层不同节奏 ────────────────────────────────────────
  const bloom = (delay: number, dur: number, maxR: number) => {
    const grow = interpolate(frame, [18 + delay, 18 + delay + dur], [0, maxR], {
      easing: Easing.out(Easing.cubic),
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
    // 收尾后仍然极缓慢地继续晕开，避免后半段完全静止
    const creep = interpolate(frame, [18 + delay + dur, 150], [0, maxR * 0.16], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
    return grow + creep;
  };
  const layers = [
    { r: bloom(0, 52, 560), o: 0.30, blur: 26 },
    { r: bloom(6, 58, 420), o: 0.34, blur: 18 },
    { r: bloom(12, 50, 260), o: 0.42, blur: 10 },
    { r: bloom(2, 44, 120), o: 0.55, blur: 4 },
  ];

  // ── 4. 水波纹涟漪：三圈错开 ─────────────────────────────────────────
  const ripple = (delay: number) => {
    const t = interpolate(frame, [22 + delay, 74 + delay], [0, 1], {
      easing: Easing.out(Easing.quad),
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
    return { r: interpolate(t, [0, 1], [70, 760]), o: interpolate(t, [0, 0.25, 1], [0, 0.5, 0]) };
  };
  const ripples = [ripple(0), ripple(9), ripple(18)];

  // ── 5. 纸面展开：纸色从中心撑开 ─────────────────────────────────────
  const reveal = interpolate(frame, [30, 112], [0, 1], {
    easing: Easing.inOut(Easing.cubic),
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const revealR = interpolate(reveal, [0, 1], [0, Math.max(width, height) * 1.05]);

  // ── 6. 标题显影 ────────────────────────────────────────────────────
  const kickerT = interpolate(frame, [84, 104], [0, 1], {
    easing: Easing.out(Easing.cubic), extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const titleT = interpolate(frame, [92, 132], [0, 1], {
    easing: Easing.out(Easing.cubic), extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const titleScale = interpolate(frame, [92, 132, 150], [1.06, 1, 1.012], { extrapolateRight: 'clamp' });
  const titleBlur = interpolate(titleT, [0, 0.6, 1], [10, 2.4, 0]);
  const titleMask = interpolate(titleT, [0, 1], [0, 100]);

  // ── 墨点飞溅 ───────────────────────────────────────────────────────
  const droplets = Array.from({ length: 16 }, (_, i) => {
    const ang = rnd(i) * Math.PI * 2;
    const dist = 60 + rnd(i + 40) * 300 * splash;
    const size = 4 + rnd(i + 80) * 9;
    return {
      x: cx + Math.cos(ang) * dist,
      y: cy + Math.sin(ang) * dist * 0.72,
      r: size * interpolate(splash, [0, 0.4, 1], [0, 1, 0.15]),
      o: interpolate(splash, [0, 0.2, 1], [0, 0.75, 0]),
    };
  });

  return (
    <AbsoluteFill style={{ background: paperColor, overflow: 'hidden' }}>
      {/* 宣纸纹理 + 墨边扰动滤镜 */}
      <svg width={width} height={height} style={{ position: 'absolute', inset: 0 }}>
        <defs>
          <filter id="paperFiber" x="-20%" y="-20%" width="140%" height="140%">
            <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="4" seed={7} result="n" />
            <feColorMatrix in="n" type="saturate" values="0" result="g" />
            <feComponentTransfer in="g" result="t">
              <feFuncA type="linear" slope="0.055" />
            </feComponentTransfer>
            <feBlend in="SourceGraphic" in2="t" mode="multiply" />
          </filter>

          {/* 墨晕：不规则墨边（低频道扰动）+ 柔化 */}
          <filter id="inkEdge" x="-60%" y="-60%" width="220%" height="220%">
            <feTurbulence type="fractalNoise" baseFrequency="0.006 0.008" numOctaves="4" seed={SEED % 97} result="noise" />
            <feDisplacementMap in="SourceGraphic" in2="noise" scale="46" xChannelSelector="R" yChannelSelector="G" result="disp" />
            <feGaussianBlur in="disp" stdDeviation="6" />
          </filter>

          {/* 涟漪：细扰动，避免机械圆 */}
          <filter id="rippleEdge" x="-40%" y="-40%" width="180%" height="180%">
            <feTurbulence type="fractalNoise" baseFrequency="0.015" numOctaves="2" seed={11} result="n2" />
            <feDisplacementMap in="SourceGraphic" in2="n2" scale="12" xChannelSelector="R" yChannelSelector="G" />
          </filter>

          {/* 标题：纸色冲开的软边 */}
          <radialGradient id="revealGrad">
            <stop offset="88%" stopColor="#fff" stopOpacity="1" />
            <stop offset="100%" stopColor="#fff" stopOpacity="0" />
          </radialGradient>

          {/* 墨滴形状渐变 */}
          <linearGradient id="dropGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={inkColor} stopOpacity="0.15" />
            <stop offset="55%" stopColor={inkColor} stopOpacity="0.85" />
            <stop offset="100%" stopColor={inkColor} stopOpacity="1" />
          </linearGradient>
        </defs>

        {/* 宣纸底 */}
        <rect width={width} height={height} fill={paperColor} filter="url(#paperFiber)" />

        {/* 墨晕四层 */}
        <g filter="url(#inkEdge)">
          {layers.map((l, i) => (
            <circle key={i} cx={cx} cy={cy} r={Math.max(l.r, 0.1)} fill={inkColor} opacity={l.o} />
          ))}
        </g>

        {/* 墨核（撞击瞬间最浓） */}
        <circle
          cx={cx}
          cy={cy}
          r={interpolate(hit, [0, 1], [8, 34]) * interpolate(frame, [20, 46], [1, 0.55], { extrapolateRight: 'clamp' })}
          fill={inkColor}
          opacity={interpolate(frame, [18, 24, 52], [0, 0.92, 0.35], { extrapolateRight: 'clamp' })}
        />

        {/* 水波纹涟漪 */}
        <g filter="url(#rippleEdge)">
          {ripples.map((r, i) => (
            <circle
              key={i}
              cx={cx}
              cy={cy}
              r={Math.max(r.r, 0.1)}
              fill="none"
              stroke={inkColor}
              strokeWidth={interpolate(r.r, [70, 760], [5.5, 0.8])}
              opacity={r.o}
            />
          ))}
        </g>

        {/* 收尾墨尘：缓慢飘落，保持画面有生命 */}
        {Array.from({ length: 10 }, (_, i) => {
          const t = interpolate(frame, [110, 150], [0, 1], {
            extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
          });
          const x = width * (0.12 + rnd(i + 200) * 0.76);
          const y0 = height * (0.05 + rnd(i + 300) * 0.5);
          return (
            <circle
              key={`dust-${i}`}
              cx={x + Math.sin((frame / 26) + i) * 10}
              cy={y0 + t * (140 + rnd(i + 400) * 220)}
              r={2 + rnd(i + 500) * 3.2}
              fill={inkColor}
              opacity={t * (0.10 + rnd(i + 600) * 0.16)}
            />
          );
        })}

        {/* 墨点飞溅 */}
        {droplets.map((d, i) => (
          <circle key={i} cx={d.x} cy={d.y} r={Math.max(d.r, 0.1)} fill={inkColor} opacity={d.o} />
        ))}

        {/* 墨滴本体（带尾迹） */}
        {dropVisible && (
          <g transform={`translate(${cx}, ${dropY})`}>
            <ellipse
              cx={0}
              cy={-70 * dropStretch}
              rx={13}
              ry={70 * dropStretch}
              fill="url(#dropGrad)"
              opacity={0.55}
            />
            {/* 撞击压扁 */}
            <ellipse
              cx={0}
              cy={0}
              rx={interpolate(hit, [0, 0.5, 1], [17, 42, 20])}
              ry={interpolate(hit, [0, 0.5, 1], [23, 11, 16])}
              fill={inkColor}
              opacity={frame < 20 ? 1 : 0}
            />
          </g>
        )}

        {/* 纸面从中心撑开（画面徐徐展开） */}
        <circle cx={cx} cy={cy} r={Math.max(revealR, 0.1)} fill={paperColor} opacity={0.97} />
        <circle
          cx={cx}
          cy={cy}
          r={Math.max(revealR, 0.1)}
          fill="none"
          stroke={inkColor}
          strokeWidth={4}
          opacity={interpolate(reveal, [0, 0.75, 1], [0.5, 0.22, 0])}
          filter="url(#rippleEdge)"
        />
      </svg>

      {/* 标题层 */}
      <AbsoluteFill
        style={{
          alignItems: 'center',
          justifyContent: 'center',
          paddingLeft: 96,
          paddingRight: 96,
          transform: `scale(${titleScale})`,
          filter: `blur(${titleBlur}px)`,
        }}
      >
        <div style={{ textAlign: 'center', width: '100%' }}>
          <div
            style={{
              fontFamily: "'STKaiti','Kaiti SC','Songti SC',serif",
              fontSize: 34,
              letterSpacing: 10,
              color: accentColor,
              opacity: kickerT,
              marginBottom: 34,
              transform: `translateY(${interpolate(kickerT, [0, 1], [14, 0])}px)`,
            }}
          >
            {kicker}
          </div>
          <div
            style={{
              fontFamily: "'STKaiti','Kaiti SC','Songti SC',serif",
              fontSize: 96,
              lineHeight: 1.28,
              color: inkColor,
              fontWeight: 600,
              letterSpacing: 4,
              opacity: titleT,
              WebkitMaskImage: `linear-gradient(100deg, #000 ${titleMask}%, transparent ${Math.min(titleMask + 18, 100)}%)`,
              maskImage: `linear-gradient(100deg, #000 ${titleMask}%, transparent ${Math.min(titleMask + 18, 100)}%)`,
            }}
          >
            {title}
          </div>
          <div
            style={{
              margin: '42px auto 0',
              width: interpolate(frame, [98, 150], [0, 300], { extrapolateRight: 'clamp' }),
              height: 5,
              background: inkColor,
              opacity: interpolate(titleT, [0, 1], [0, 0.55]),
            }}
          />
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
