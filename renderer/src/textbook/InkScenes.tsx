/**
 * 古风场景组件（F-2026-11-12-TEXTBOOK-STORY · M2）
 *
 * 五类 visual_type：ink_title / vertical_poem / ancient_map / archive_card / scroll_transition
 * 全部只读既有 AlignedSceneItem 字段（highlight_text / segment_narration / stat_items），
 * 不引入新的渲染契约；诗句原文来自 props.textbook.full_text（由 _prepare_props 注入）。
 */
import React from 'react';
import { interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { ink, splitPoemColumns, splitSentences, toInkVisualType } from './theme';

/* ------------------------------------------------------------------ 底纹 */

/** 宣纸底：纤维纹理 + 淡墨晕 + 四角压暗。全幅铺开，横屏两侧不留纯色带。 */
export const InkBackdrop: React.FC<{ children?: React.ReactNode }> = ({ children }) => {
  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        background: `linear-gradient(160deg, ${ink.paper} 0%, ${ink.paperDeep} 55%, ${ink.paperShade} 100%)`,
      }}
    >
      {/* 纸纤维：细密横竖纹理，让每一列像素都有灰度差（横屏护栏按列灰度判"有没有内容"） */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          backgroundImage: `repeating-linear-gradient(0deg, ${ink.inkWash} 0 1px, transparent 1px 7px),
            repeating-linear-gradient(90deg, rgba(28,26,23,0.045) 0 1px, transparent 1px 11px)`,
        }}
      />
      {/* 淡墨晕 */}
      <div
        style={{
          position: 'absolute',
          top: '-12%',
          left: '-18%',
          width: '70%',
          height: '55%',
          background: `radial-gradient(circle, rgba(28,26,23,0.10) 0%, transparent 68%)`,
          filter: 'blur(24px)',
        }}
      />
      <div
        style={{
          position: 'absolute',
          bottom: '-18%',
          right: '-14%',
          width: '75%',
          height: '60%',
          background: `radial-gradient(circle, rgba(28,26,23,0.07) 0%, transparent 70%)`,
          filter: 'blur(28px)',
        }}
      />
      {/* 四角压暗 */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background:
            'radial-gradient(ellipse at 50% 45%, transparent 55%, rgba(28,26,23,0.16) 100%)',
        }}
      />
      {children}
    </div>
  );
};

/* ------------------------------------------------------------------ 印章 */

export const Seal: React.FC<{ text?: string; size?: number; style?: React.CSSProperties }> = ({
  text = '课文背后',
  size = 96,
  style,
}) => {
  const chars = Array.from(text).slice(0, 4);
  return (
    <div
      style={{
        width: size,
        height: size,
        border: `3px solid ${ink.cinnabar}`,
        borderRadius: 6,
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        alignItems: 'center',
        justifyItems: 'center',
        color: ink.cinnabar,
        fontFamily: ink.fontKai,
        fontWeight: 700,
        fontSize: size * 0.36,
        lineHeight: 1,
        opacity: 0.92,
        transform: 'rotate(-2deg)',
        background: 'rgba(166, 38, 31, 0.06)',
        ...style,
      }}
    >
      {chars.map((c, i) => (
        <span key={i}>{c}</span>
      ))}
    </div>
  );
};

/* ------------------------------------------------------------------ 笔画渐显 */

const useStrokeIn = (delay = 4, duration = 26) => {
  const frame = useCurrentFrame();
  return interpolate(frame - delay, [0, duration], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
};

/* ------------------------------------------------------------------ 1. 水墨标题 */

const InkTitleScene: React.FC<{
  title: string;
  subtitle?: string;
  chapter?: string;
}> = ({ title, subtitle, chapter }) => {
  const { height } = useVideoConfig();
  const scale = height / 1920;
  const t = useStrokeIn(2, 30);
  const chars = Array.from(title || '课文背后');

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 70 * scale,
      }}
    >
      <div
        style={{
          writingMode: 'vertical-rl',
          fontFamily: ink.fontKai,
          color: ink.ink,
          fontSize: Math.min(132, 1400 / Math.max(6, chars.length)) * scale,
          fontWeight: 700,
          letterSpacing: 14 * scale,
          lineHeight: 1.18,
          clipPath: `inset(${(1 - t) * 100}% 0 0 0)`,
          textShadow: '0 2px 18px rgba(28,26,23,0.10)',
        }}
      >
        {chars.join('')}
      </div>
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 28 * scale,
          opacity: t,
        }}
      >
        <Seal size={104 * scale} />
        {subtitle ? (
          <div
            style={{
              writingMode: 'vertical-rl',
              fontFamily: ink.fontSong,
              fontSize: 34 * scale,
              color: ink.inkSoft,
              letterSpacing: 6 * scale,
            }}
          >
            {subtitle}
          </div>
        ) : null}
        {chapter ? (
          <div style={{ fontFamily: ink.fontSong, fontSize: 26 * scale, color: ink.jade }}>
            {chapter}
          </div>
        ) : null}
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ 2. 竖排原文 */

const VerticalPoemScene: React.FC<{
  poemTitle?: string;
  author?: string;
  dynasty?: string;
  fullText?: string;
  highlight?: string;
}> = ({ poemTitle, author, dynasty, fullText, highlight }) => {
  const { width, height } = useVideoConfig();
  const scale = height / 1920;
  const frame = useCurrentFrame();

  const columns = splitPoemColumns(fullText || highlight || '', 7);
  const shown = Math.max(1, Math.min(columns.length, Math.ceil((frame - 4) / 9)));

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: `${120 * scale}px ${90 * scale}px`,
        boxSizing: 'border-box',
      }}
    >
      {poemTitle ? (
        <div
          style={{
            fontFamily: ink.fontKai,
            fontSize: 46 * scale,
            color: ink.inkSoft,
            letterSpacing: 10 * scale,
            marginBottom: 34 * scale,
          }}
        >
          《{poemTitle}》
          <span style={{ fontSize: 30 * scale, marginLeft: 18 * scale, color: ink.jade }}>
            {dynasty ? `${dynasty} · ` : ''}
            {author || ''}
          </span>
        </div>
      ) : null}

      <div
        style={{
          display: 'flex',
          flexDirection: 'row-reverse',
          justifyContent: 'center',
          alignItems: 'flex-start',
          gap: 22 * scale,
          maxWidth: width * 0.82,
        }}
      >
        {columns.map((col, i) => (
          <div
            key={i}
            style={{
              writingMode: 'vertical-rl',
              fontFamily: ink.fontKai,
              fontSize: 74 * scale,
              color: ink.ink,
              letterSpacing: 10 * scale,
              lineHeight: 1.16,
              opacity: i < shown ? 1 : 0.12,
              transform: `translateY(${i < shown ? 0 : 12 * scale}px)`,
              textShadow: '0 1px 12px rgba(28,26,23,0.08)',
            }}
          >
            {col}
          </div>
        ))}
      </div>

      <div
        style={{
          marginTop: 44 * scale,
          width: `${Math.min(88, 14 + columns.length * 8)}%`,
          height: 2 * scale,
          background: `linear-gradient(90deg, transparent, ${ink.inkFaint}, transparent)`,
        }}
      />
      <div
        style={{
          marginTop: 26 * scale,
          fontFamily: ink.fontSong,
          fontSize: 30 * scale,
          color: ink.inkSoft,
          letterSpacing: 4 * scale,
        }}
      >
        {highlight || ''}
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ 3. 古地图 */

const AncientMapScene: React.FC<{
  caption: string;
  lines: string[];
  stats?: Array<{ label: string; value: string }>;
}> = ({ caption, lines, stats }) => {
  const { height } = useVideoConfig();
  const scale = height / 1920;
  const frame = useCurrentFrame();
  const draw = interpolate(frame, [0, 34], [0, 1], { extrapolateRight: 'clamp' });

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-around',
        padding: `${110 * scale}px ${86 * scale}px`,
        boxSizing: 'border-box',
        gap: 34 * scale,
      }}
    >
      <svg width={560 * scale} height={860 * scale} viewBox="0 0 560 860">
        {/* 山河轮廓：几笔写意线条，绝不是数据图表 */}
        <path
          d="M40 700 C 150 620, 210 660, 300 560 S 470 470, 520 380"
          fill="none"
          stroke={ink.inkSoft}
          strokeWidth={5}
          strokeLinecap="round"
          strokeDasharray={1200}
          strokeDashoffset={(1 - draw) * 1200}
          opacity={0.75}
        />
        <path
          d="M60 300 C 170 260, 250 340, 330 300 S 460 250, 512 280"
          fill="none"
          stroke={ink.jade}
          strokeWidth={3}
          strokeDasharray={9}
          strokeDashoffset={(1 - draw) * 900}
          opacity={0.6}
        />
        <path
          d="M40 180 C 160 210, 240 150, 350 190 S 470 160, 520 200"
          fill="none"
          stroke={ink.inkSoft}
          strokeWidth={4}
          strokeLinecap="round"
          strokeDasharray={900}
          strokeDashoffset={(1 - draw) * 900}
          opacity={0.5}
        />
        {/* 城池点：三个节点，最后一个用朱砂 */}
        {[
          { x: 150, y: 640, r: 9 },
          { x: 300, y: 500, r: 9 },
          { x: 452, y: 366, r: 12 },
        ].map((p, i) => (
          <g key={i} opacity={draw}>
            <circle cx={p.x} cy={p.y} r={p.r + 10} fill={i === 2 ? 'rgba(166,38,31,0.18)' : ink.inkFaint} />
            <circle cx={p.x} cy={p.y} r={p.r} fill={i === 2 ? ink.cinnabar : ink.inkSoft} />
          </g>
        ))}
        <text
          x={150}
          y={600}
          textAnchor="middle"
          fontFamily={ink.fontKai}
          fontSize={26}
          fill={ink.inkSoft}
        >
          山阴
        </text>
        <text
          x={300}
          y={462}
          textAnchor="middle"
          fontFamily={ink.fontKai}
          fontSize={26}
          fill={ink.inkSoft}
        >
          临安
        </text>
        <text
          x={452}
          y={322}
          textAnchor="middle"
          fontFamily={ink.fontKai}
          fontSize={30}
          fill={ink.cinnabar}
        >
          中原
        </text>
      </svg>

      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 26 * scale,
          maxWidth: 520 * scale,
        }}
      >
        <div
          style={{
            writingMode: 'vertical-rl',
            fontFamily: ink.fontKai,
            fontSize: 62 * scale,
            color: ink.ink,
            letterSpacing: 8 * scale,
            alignSelf: 'flex-start',
            maxHeight: 620 * scale,
          }}
        >
          {caption}
        </div>
        {(lines.length ? lines : []).map((line, i) => (
          <div
            key={i}
            style={{
              fontFamily: ink.fontSong,
              fontSize: 30 * scale,
              lineHeight: 1.7,
              color: ink.inkSoft,
              borderLeft: `3px solid ${i === 0 ? ink.cinnabar : ink.inkFaint}`,
              paddingLeft: 18 * scale,
            }}
          >
            {line}
          </div>
        ))}
        {stats && stats.length ? (
          <div style={{ display: 'flex', gap: 20 * scale, flexWrap: 'wrap' }}>
            {stats.slice(0, 3).map((s, i) => (
              <div
                key={i}
                style={{
                  border: `1px solid ${ink.inkFaint}`,
                  borderRadius: 10,
                  padding: `${12 * scale}px ${18 * scale}px`,
                  fontFamily: ink.fontSong,
                  color: ink.inkSoft,
                  fontSize: 26 * scale,
                }}
              >
                <span style={{ color: ink.cinnabar }}>{s.value}</span>
                <span style={{ marginLeft: 10 * scale, opacity: 0.75 }}>{s.label}</span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ 4. 史料卡 */

const ArchiveCardScene: React.FC<{
  heading: string;
  lines: string[];
  attribution?: string;
}> = ({ heading, lines, attribution }) => {
  const { height } = useVideoConfig();
  const scale = height / 1920;
  const t = useStrokeIn(3, 22);

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: `${120 * scale}px ${96 * scale}px`,
        boxSizing: 'border-box',
      }}
    >
      {/* 卷轴上下轴杆 */}
      {[0, 1].map((i) => (
        <div
          key={i}
          style={{
            position: 'absolute',
            [i === 0 ? 'top' : 'bottom']: 150 * scale,
            left: 88 * scale,
            right: 88 * scale,
            height: 16 * scale,
            borderRadius: 8,
            background: `linear-gradient(180deg, ${ink.gold} 0%, #8a6b36 100%)`,
            boxShadow: '0 6px 18px rgba(28,26,23,0.22)',
          }}
        />
      ))}

      <div
        style={{
          width: '100%',
          background: 'linear-gradient(180deg, rgba(255,252,244,0.72), rgba(239,230,212,0.88))',
          border: `1px solid ${ink.inkFaint}`,
          boxShadow: '0 18px 46px rgba(28,26,23,0.16)',
          padding: `${52 * scale}px ${56 * scale}px`,
          boxSizing: 'border-box',
          opacity: t,
          transform: `translateY(${(1 - t) * 18 * scale}px)`,
          display: 'flex',
          flexDirection: 'column',
          gap: 28 * scale,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: `2px solid ${ink.inkFaint}`,
            paddingBottom: 18 * scale,
          }}
        >
          <span
            style={{
              fontFamily: ink.fontKai,
              fontSize: 52 * scale,
              color: ink.ink,
              letterSpacing: 6 * scale,
            }}
          >
            {heading}
          </span>
          <Seal text="史料" size={72 * scale} />
        </div>

        {lines.map((line, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              gap: 16 * scale,
              fontFamily: ink.fontSong,
              fontSize: 36 * scale,
              lineHeight: 1.62,
              color: ink.inkSoft,
            }}
          >
            <span style={{ color: ink.cinnabar, opacity: 0.85 }}>·</span>
            <span>{line}</span>
          </div>
        ))}

        {attribution ? (
          <div
            style={{
              alignSelf: 'flex-end',
              fontFamily: ink.fontSong,
              fontSize: 24 * scale,
              color: ink.inkSoft,
              opacity: 0.7,
              borderTop: `1px dashed ${ink.inkFaint}`,
              paddingTop: 14 * scale,
            }}
          >
            出处：{attribution}
          </div>
        ) : null}
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ 5. 卷轴转场 */

const ScrollTransitionScene: React.FC<{ caption: string }> = ({ caption }) => {
  const { width, height } = useVideoConfig();
  const scale = height / 1920;
  const frame = useCurrentFrame();
  const open = interpolate(frame, [0, 26], [0, 1], { extrapolateRight: 'clamp' });
  const rodGap = (width / 2) * (1 - open) + 40 * scale;
  const chars = Array.from(caption || '');

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      {/* 展开的画卷 */}
      <div
        style={{
          position: 'absolute',
          left: rodGap,
          right: rodGap,
          top: height * 0.32,
          bottom: height * 0.32,
          background:
            'linear-gradient(180deg, rgba(255,252,244,0.9) 0%, rgba(236,226,205,0.95) 100%)',
          borderTop: `2px solid ${ink.gold}`,
          borderBottom: `2px solid ${ink.gold}`,
          boxShadow: '0 16px 40px rgba(28,26,23,0.18)',
        }}
      />
      {/* 左右轴杆 */}
      {[rodGap, width - rodGap].map((x, i) => (
        <div
          key={i}
          style={{
            position: 'absolute',
            left: x - 10 * scale,
            top: height * 0.26,
            width: 20 * scale,
            height: height * 0.48,
            borderRadius: 10,
            background: `linear-gradient(90deg, ${ink.gold} 0%, #8a6b36 60%, ${ink.gold} 100%)`,
            boxShadow: '0 10px 26px rgba(28,26,23,0.28)',
          }}
        />
      ))}
      <div
        style={{
          writingMode: 'vertical-rl',
          fontFamily: ink.fontKai,
          fontSize: 76 * scale,
          color: ink.ink,
          letterSpacing: 12 * scale,
          opacity: open,
          maxHeight: height * 0.34,
        }}
      >
        {chars.join('')}
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ 分发 */

export interface InkSceneProps {
  scene: any;
  poetry: {
    poemTitle?: string;
    author?: string;
    dynasty?: string;
    fullText?: string;
    edition?: string;
  };
}

export const InkScene: React.FC<InkSceneProps> = ({ scene, poetry }) => {
  const kind = toInkVisualType(String(scene.visual_type || ''));
  const highlight = String(scene.highlight_text || '');
  const narration = String(scene.segment_narration || '');
  const stats = Array.isArray(scene.stat_items) ? scene.stat_items : [];
  const lines = splitSentences(narration, kind === 'archive_card' ? 3 : 2);

  switch (kind) {
    case 'vertical_poem':
      return (
        <VerticalPoemScene
          poemTitle={poetry.poemTitle}
          author={poetry.author}
          dynasty={poetry.dynasty}
          fullText={poetry.fullText}
          highlight={highlight}
        />
      );
    case 'ancient_map':
      return <AncientMapScene caption={highlight} lines={lines} stats={stats} />;
    case 'archive_card':
      return (
        <ArchiveCardScene
          heading={highlight || poetry.poemTitle || '史料'}
          lines={lines}
          attribution={poetry.edition}
        />
      );
    case 'scroll_transition':
      return <ScrollTransitionScene caption={highlight} />;
    case 'ink_title':
    default:
      return (
        <InkTitleScene
          title={highlight || poetry.poemTitle || '课文背后'}
          subtitle={poetry.poemTitle ? `${poetry.dynasty ? poetry.dynasty + ' · ' : ''}${poetry.author || ''}` : undefined}
          chapter={poetry.edition}
        />
      );
  }
};

/* ------------------------------------------------------------------ 水墨字幕 */

export const InkSubtitle: React.FC<{ subtitles: Array<{ text: string; start_ms: number; end_ms: number }> }> = ({
  subtitles,
}) => {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();
  const scale = height / 1920;
  const ms = (frame / fps) * 1000;
  const active = subtitles.find((s) => ms >= s.start_ms && ms <= s.end_ms);
  if (!active || !active.text) return null;

  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        bottom: 96 * scale,
        display: 'flex',
        justifyContent: 'center',
        padding: `0 ${60 * scale}px`,
        boxSizing: 'border-box',
      }}
    >
      <div
        style={{
          fontFamily: ink.fontSong,
          fontSize: 38 * scale,
          lineHeight: 1.5,
          color: '#FBF7EC',
          background: 'rgba(28, 26, 23, 0.78)',
          border: `1px solid rgba(176, 138, 75, 0.55)`,
          borderRadius: 8,
          padding: `${16 * scale}px ${30 * scale}px`,
          textAlign: 'center',
          letterSpacing: 2 * scale,
        }}
      >
        {active.text}
      </div>
    </div>
  );
};
