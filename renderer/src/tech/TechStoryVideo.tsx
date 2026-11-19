import React from 'react';
import {
  AbsoluteFill,
  Audio,
  interpolate,
  OffthreadVideo,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  Easing,
} from 'remotion';
import TL from './tech_scene_timeline.json';

/**
 * 科技类视频模板（TechStoryVideo）· 全屏 AI 视频素材版
 *
 * 数据来源：流水线 props（aligned_render.json）
 *   scenes[]        segment_narration / highlight_text / creator_take / audio_src / start_frame / duration_frames
 *   assets.open     开场素材（可缺）
 *   assets.bg       正文素材（必需）
 *   bgm_src         背景音（素材库选出来的）
 *   bgm_volume      基准音量（口播在场时乘 duck）
 *
 * 没有 props 时（本地 demo）回落到 src/tech/tech_scene_timeline.json。
 *
 * 画面分区（1080×1920）：
 *   y 0–148      状态条：AI 声明 / 栏目标签 / 进度
 *   y 190–760    标题(首镜) 或 编号+关键词(其余)
 *   y 760–1500   素材裸露区（无文字覆盖）
 *   y 1500–1780  字幕（锐评条在字幕上方）
 */

const ACCENT = '#5ED8FF';
const FONT = "'PingFang SC','Hiragino Sans GB','Helvetica Neue',sans-serif";
const PAD_FRAMES = 14; // 段落尾部留白（与 build 侧 PAD 一致，用于口播避让判断）

/**
 * 左右安全边距（2026-11-18）
 *
 * 竖版 1080 宽：抖音信息流会把左右各裁掉约 9%，原来的 88/70 被裁完就贴边了
 * （用户原话「视频号有边距、抖音没有」——同一条视频，抖音把留白裁没了）。
 * 折中定 100（9.3%）：裁 9% 后还剩约 3px 缓冲，视觉上比 88 明显宽松，又不至于
 * 把正文压得太窄。抖音和视频号都吃竖版，一处改两平台生效。
 * 横版 1920 宽维持 88 不变，避免无谓改动影响 B站。
 */
const SAFE_X_VERTICAL = 100;
const SAFE_X_LANDSCAPE = 88;
const useSafeX = (): number =>
  (useVideoConfig().width >= 1600 ? SAFE_X_LANDSCAPE : SAFE_X_VERTICAL);

type SceneLike = {
  index: number;
  narration: string;
  label: string;
  take?: string | null;
  audio?: string;
  start: number;
  frames: number;
};

export type TechProps = {
  fps?: number;
  total_duration_in_frames?: number;
  cover_main_text?: string;
  cover_sub_text?: string;
  theme?: string;
  title?: string;
  bgm_src?: string;
  bgm_volume?: number;
  bgm_duck_volume?: number;
  assets?: { open?: string | null; bg?: string; open_start_from?: number;
    /** asset=用素材帧做封面（默认）；typographic=纯排版卡片封面（无实拍画面，R5 回退） */
    cover_mode?: 'none' | 'asset' | 'typographic';
    /** 幕间转场片段（每个场景交界插 0.5s 全屏覆盖，不改时间轴，2026-11-18） */
    mid?: string | null;
    /** 片尾收尾片段（最后 1.2s 全屏覆盖，不改时间轴，2026-11-18） */
    end?: string | null };
  scenes?: Array<{
    scene_index: number;
    segment_narration: string;
    highlight_text?: string;
    creator_take?: string | null;
    audio_src?: string;
    start_frame: number;
    duration_frames: number;
  }>;
};

const KICKER: Record<string, string> = {
  'breaking-news': '科技快讯',
  'ai-product': 'AI 产品',
  'tech-github': '开源技术',
};

const DEFAULTS = {
  open: 'assets/tech/video/open/open_wafer_v1.mp4',
  bg: 'assets/tech/video/bg/bg_circuit_v1_loop.mp4',
  bgm: 'assets/tech/bgm/bgm_calm_v1.mp3',
};

/** props → 内部场景结构；无 props 时用本地时间轴 demo */
const normalize = (props: TechProps) => {
  if (props.scenes && props.scenes.length) {
    return {
      total: props.total_duration_in_frames || 900,
      kicker: KICKER[props.theme || ''] || '科技速递',
      title: props.cover_main_text || props.title || '',
      subtitle: props.cover_sub_text || '',
      open: props.assets?.open ?? null,
      openStart: props.assets?.open_start_from ?? 40,
      coverMode: props.assets?.cover_mode ?? 'none',
      bg: props.assets?.bg || DEFAULTS.bg,
      // 2026-11-19 回滚：mid/end 不再参与渲染（见下方注释），字段保留只为兼容旧 props
      mid: null as string | null,
      end: null as string | null,
      bgm: props.bgm_src || '',
      bgmVolume: props.bgm_volume ?? 0.22,
      bgmDuck: props.bgm_duck_volume ?? 0.12,
      scenes: props.scenes.map<SceneLike>((s) => ({
        index: s.scene_index,
        narration: s.segment_narration,
        label: s.highlight_text || '',
        take: s.creator_take || null,
        audio: s.audio_src,
        start: s.start_frame,
        frames: s.duration_frames,
      })),
    };
  }
  return {
    total: TL.total,
    kicker: TL.kicker,
    title: TL.title,
    subtitle: TL.subtitle,
    open: DEFAULTS.open,
    openStart: 40,
    coverMode: 'none' as 'none' | 'asset' | 'typographic',
    bg: DEFAULTS.bg,
    bgm: DEFAULTS.bgm,
    bgmVolume: 0.22,
    bgmDuck: 0.12,
    scenes: TL.scenes.map<SceneLike>((s) => ({
      index: s.index,
      narration: s.narration,
      label: s.label,
      take: null,
      audio: s.audio,
      start: s.start,
      frames: s.frames,
    })),
  };
};

/* ── 首镜标题 ── */
const HookOverlay: React.FC<{ title: string; subtitle: string; kicker: string; take?: string | null }> = ({
  title, subtitle, kicker, take,
}) => {
  const frame = useCurrentFrame();
  const safeX = useSafeX();
  const t = (a: number, b: number) =>
    interpolate(frame, [a, b], [0, 1], { easing: Easing.out(Easing.cubic), extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const lineW = interpolate(frame, [40, 74], [0, 150], {
    easing: Easing.inOut(Easing.quad), extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });

  return (
    <div style={{ position: 'absolute', left: safeX, right: safeX, top: 190 }}>
      <div style={{ fontFamily: FONT, fontSize: 24, letterSpacing: 8, color: ACCENT, opacity: t(8, 30) }}>
        {kicker}
      </div>
      <div
        style={{
          marginTop: 34, fontFamily: FONT, fontSize: 92, fontWeight: 800, lineHeight: 1.22,
          letterSpacing: 1, color: '#FFFFFF', opacity: t(18, 48),
          transform: `translateY(${interpolate(t(18, 48), [0, 1], [24, 0])}px)`,
          textShadow: '0 8px 40px rgba(0,0,0,0.85), 0 2px 10px rgba(0,0,0,0.7)',
        }}
      >
        {title}
      </div>
      <div style={{ marginTop: 30, width: lineW, height: 5, background: ACCENT, borderRadius: 3 }} />
      {subtitle ? (
        <div
          style={{
            marginTop: 26, fontFamily: FONT, fontSize: 34, lineHeight: 1.5,
            color: '#D7E3EE', opacity: t(54, 80), textShadow: '0 4px 22px rgba(0,0,0,0.8)',
          }}
        >
          {subtitle}
        </div>
      ) : null}
      {take ? (
        <div
          style={{
            marginTop: 30, paddingLeft: 18, borderLeft: `4px solid ${ACCENT}`,
            fontFamily: FONT, fontSize: 30, lineHeight: 1.45, color: '#CFE6F5',
            opacity: t(66, 92), textShadow: '0 4px 20px rgba(0,0,0,0.85)',
          }}
        >
          <span style={{ color: ACCENT, fontWeight: 800 }}>锐评 </span>
          {take}
        </div>
      ) : null}
    </div>
  );
};

/* ── 正文：编号 + 关键词 + 锐评 ── */
const SceneTag: React.FC<{ scene: SceneLike }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const t = interpolate(frame, [0, 16], [0, 1], {
    easing: Easing.out(Easing.cubic), extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const barH = interpolate(frame, [0, 18], [0, 92], {
    easing: Easing.out(Easing.cubic), extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const no = String(scene.index).padStart(2, '0');
  const safeX = useSafeX();

  return (
    <div
      style={{
        position: 'absolute', left: safeX, right: safeX, top: 200,
        display: 'flex', alignItems: 'flex-start', gap: 26, opacity: t,
        transform: `translateY(${interpolate(t, [0, 1], [18, 0])}px)`,
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 4 }}>
        <div style={{ fontFamily: FONT, fontSize: 22, fontWeight: 700, color: ACCENT, letterSpacing: 2 }}>{no}</div>
        <div style={{ marginTop: 10, width: 4, height: barH, background: ACCENT, borderRadius: 2 }} />
      </div>
      <div style={{ flex: 1 }}>
        <div
          style={{
            fontFamily: FONT, fontSize: 56, fontWeight: 800, lineHeight: 1.3, color: '#FFFFFF',
            textShadow: '0 8px 34px rgba(0,0,0,0.9), 0 2px 8px rgba(0,0,0,0.7)',
          }}
        >
          {scene.label || `第 ${scene.index + 1} 段`}
        </div>
        {scene.take ? (
          <div
            style={{
              marginTop: 22, paddingLeft: 18, borderLeft: `4px solid ${ACCENT}`,
              fontFamily: FONT, fontSize: 30, lineHeight: 1.5, color: '#CFE6F5',
              textShadow: '0 4px 20px rgba(0,0,0,0.85)',
            }}
          >
            <span style={{ color: ACCENT, fontWeight: 800 }}>锐评 </span>
            {scene.take}
          </div>
        ) : null}
      </div>
    </div>
  );
};

/* ── 底部字幕 ── */
const Subtitles: React.FC<{ scene: SceneLike }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const safeX = useSafeX();
  const fadeIn = interpolate(frame, [0, 8], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const fadeOut = interpolate(frame, [scene.frames - 10, scene.frames], [1, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  return (
    <div
      style={{
        position: 'absolute', left: safeX, right: safeX, bottom: 168,
        display: 'flex', justifyContent: 'center', opacity: fadeIn * fadeOut,
      }}
    >
      <div
        style={{
          padding: '18px 30px', borderRadius: 18, maxWidth: 940,
          background: 'rgba(3,6,10,0.55)', border: '1px solid rgba(94,216,255,0.22)',
        }}
      >
        <div
          style={{
            fontFamily: FONT, fontSize: 38, fontWeight: 600, lineHeight: 1.5,
            color: '#FFFFFF', textAlign: 'center', letterSpacing: 0.5,
          }}
        >
          {scene.narration}
        </div>
      </div>
    </div>
  );
};

const Progress: React.FC<{ total: number }> = ({ total }) => {
  const frame = useCurrentFrame();
  return (
    <div style={{ position: 'absolute', left: 0, right: 0, top: 148, height: 3, background: 'rgba(255,255,255,0.10)' }}>
      <div style={{ width: `${Math.min(100, (frame / total) * 100)}%`, height: '100%', background: ACCENT, opacity: 0.85 }} />
    </div>
  );
};

export const TechStoryVideo: React.FC<TechProps> = (props) => {
  const safeX2 = useSafeX();
  const cfg = normalize(props);
  const first = cfg.scenes[0];
  const bodyStart = first ? first.frames : 0;

  // 口播在场帧区间：每段前 (frames - PAD) 视为有人声 → BGM 避让
  const voiceRanges = cfg.scenes.map((s) => [s.start, s.start + Math.max(1, s.frames - PAD_FRAMES)] as const);
  const bgmVolume = (f: number) => {
    const speaking = voiceRanges.some(([a, b]) => f >= a && f < b);
    return speaking ? cfg.bgmDuck : cfg.bgmVolume;
  };

  // SPEC-20261117 §八：cover_mode=none → 不插封面海报帧，open 片段也不参与，
  // 正片从第 0 帧开始（平台封面图由构建侧 ffmpeg 抽 video 第 0 帧，见 executors）。
  if (cfg.coverMode === 'none') {
    cfg.open = null;
  }

  return (
    <AbsoluteFill style={{ background: '#04060a' }}>
      {cfg.bgm ? <Audio src={staticFile(cfg.bgm)} loop volume={bgmVolume} /> : null}

      {cfg.open ? (
        <Sequence from={0} durationInFrames={bodyStart}>
          <OffthreadVideo
            src={staticFile(cfg.open)}
            startFrom={cfg.openStart}
            muted
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          />
        </Sequence>
      ) : null}

      <Sequence from={cfg.open ? bodyStart : 0} durationInFrames={Math.max(1, cfg.total - (cfg.open ? bodyStart : 0))}>
        <OffthreadVideo
          src={staticFile(cfg.bg)}
          muted
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
        />
      </Sequence>

      {/* 只压上下两端，中部裸露 */}
      <AbsoluteFill
        style={{
          background:
            'linear-gradient(to bottom, rgba(3,5,9,0.34) 0%, rgba(3,5,9,0.14) 42%, rgba(3,5,9,0.52) 100%)',
        }}
      />

      {/* R5：无匹配档位素材 → 封面帧回退纯排版卡片（不拿别的档位画面凑）
          只覆盖前 15 帧（0.5s @30fps），正文背景不受影响 */}
      {cfg.coverMode === 'typographic' && (
        <Sequence from={0} durationInFrames={Math.min(15, cfg.total)}>
          <AbsoluteFill style={{ background: 'linear-gradient(135deg, #04060a 0%, #0b1622 52%, #12304f 100%)' }} />
          <AbsoluteFill style={{ background: 'radial-gradient(circle at 22% 18%, rgba(93,169,255,0.20), transparent 58%)' }} />
          <AbsoluteFill style={{ background: 'radial-gradient(circle at 82% 88%, rgba(255,196,86,0.10), transparent 52%)' }} />
        </Sequence>
      )}

      {cfg.scenes.map((s) => (
        <Sequence key={`${s.index}-${s.start}`} from={s.start} durationInFrames={s.frames}>
          {s.index === 0 ? (
            <HookOverlay title={cfg.title} subtitle={cfg.subtitle} kicker={cfg.kicker} take={s.take} />
          ) : (
            <SceneTag scene={s} />
          )}
          <Subtitles scene={s} />
          {s.audio ? <Audio src={staticFile(s.audio)} volume={1} /> : null}
          <Audio src={staticFile(s.index === 0 ? 'sfx/ding.wav' : 'sfx/whoosh.wav')} volume={0.18} />
        </Sequence>
      ))}

      {/* 幕间转场 / 片尾收尾：2026-11-19 已回滚 —— mid 素材开头是黑场，
          压在场景交界会造成 0.3~0.5s 全黑闪断（实测 7.0~7.2s 亮度 0.0）。
          要恢复必须先在素材侧裁掉黑场头，再加回来。 */}

      <Progress total={cfg.total} />

      <div
        style={{
          position: 'absolute', left: safeX2, top: 104, fontFamily: FONT, fontSize: 18,
          letterSpacing: 3, color: 'rgba(255,255,255,0.42)',
        }}
      >
        AI 辅助生成
      </div>
      <div
        style={{
          position: 'absolute', right: 88, top: 104, fontFamily: FONT, fontSize: 18,
          letterSpacing: 3, color: 'rgba(255,255,255,0.42)',
        }}
      >
        {String(cfg.scenes.length).padStart(2, '0')} 段
      </div>
    </AbsoluteFill>
  );
};

export const TECH_TOTAL = TL.total;
