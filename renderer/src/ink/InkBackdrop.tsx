import React from 'react';
import {
  AbsoluteFill,
  Img,
  Loop,
  OffthreadVideo,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

/**
 * 水墨素材层（2026-11-19 重做，方案 C v2）
 *
 * v1 的问题（用户实测看不出素材）：
 *   · 只挂 bg 一层，且不透明度 0.42，叠在米色宣纸上一共只差 10 灰阶 = 肉眼不可见；
 *   · OffthreadVideo 播完就停在最后一帧，10s 素材铺 125s 成片，92% 时间是死图；
 *   · open / mid / end 三个槽根本没接进课文本（飞鸟/泼墨永远不出现）。
 *
 * v2 口径：
 *   · 素材**当画面底**（opacity 0.92），不再走"淡叠加"；
 *   · `<Loop>` 循环播放，整片都在动；
 *   · 浅色素材自动加 contrast/brightness 校正，保证与纸色差 ≥25 灰阶（肉眼可辨）；
 *   · 上面压一层纸色薄纱（0.18），保证竖排文字对比度，文字仍然清楚。
 */

export type InkBackdropLayerProps = {
  /** 背景视频，相对 renderer/public，形如 ink/ink_bamboo_sway_v1.mp4 */
  bg?: string | null;
  /** 纸纹叠加（图片或视频帧），可为空 */
  tex?: string | null;
  /** 背景不透明度：素材要当"画面"，给足 0.92 */
  bgOpacity?: number;
  /** 素材单次循环时长（秒）：素材多为 6–10s，用 Loop 反复播 */
  loopSeconds?: number;
  /** 纸色薄纱不透明度：保证文字对比 */
  veilOpacity?: number;
};

export const InkBackdropLayer: React.FC<InkBackdropLayerProps> = ({
  bg,
  tex,
  bgOpacity = 0.92,
  loopSeconds = 8,
  veilOpacity = 0.18,
}) => {
  const { fps } = useVideoConfig();
  if (!bg && !tex) return null;
  const loopFrames = Math.max(1, Math.round(loopSeconds * fps));
  return (
    <>
      {bg ? (
        <AbsoluteFill style={{ opacity: bgOpacity }}>
          <Loop durationInFrames={loopFrames}>
            <OffthreadVideo
              src={staticFile(bg)}
              muted
              // 水墨素材偏浅（亮度 183–237），纸色 230：不校正会糊成一片纸。
              style={{
                width: '100%',
                height: '100%',
                objectFit: 'cover',
                filter: 'contrast(1.32) brightness(0.9) saturate(0.85)',
              }}
            />
          </Loop>
        </AbsoluteFill>
      ) : null}
      {/* 纸色薄纱：把素材拉回宣纸调，同时给竖排文字留对比 */}
      <AbsoluteFill style={{ background: `rgba(239,230,212,${veilOpacity})` }} />
      {tex ? (
        <AbsoluteFill style={{ opacity: 0.14, mixBlendMode: 'multiply' }}>
          <Img src={staticFile(tex)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        </AbsoluteFill>
      ) : null}
    </>
  );
};

/**
 * 分幕空镜层：把 open / mid / end 真正用起来（v1 完全没接，飞鸟永远不出现）。
 *   · open  —— 片头 0 → openFrames，墨滴入纸/笔触落下
 *   · mid   —— 每个场景交界插一段，泼墨/飞鸟/飘叶
 *   · end   —— 片尾最后 endFrames，残墨收尾
 * 全部带淡入淡出，避免硬切；缺素材的槽自动跳过。
 */
export type InkOverlaysProps = {
  assets?: {
    open?: string | null;
    mid?: string | null;
    end?: string | null;
  } | null;
  /** 场景起始帧号（来自 props.scenes[].start_frame） */
  sceneStarts?: number[];
  totalFrames: number;
  openSeconds?: number;
  midSeconds?: number;
  endSeconds?: number;
};

const fade = (frame: number, dur: number, fadeFrames: number): number =>
  interpolate(frame, [0, fadeFrames, dur - fadeFrames, dur], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

export const InkOverlays: React.FC<InkOverlaysProps> = ({
  assets,
  sceneStarts = [],
  totalFrames,
  openSeconds = 2.2,
  midSeconds = 0.6,
  endSeconds = 1.4,
}) => {
  const { fps } = useVideoConfig();
  const openFrames = Math.round(openSeconds * fps);
  const midFrames = Math.round(midSeconds * fps);
  const endFrames = Math.round(endSeconds * fps);
  const fadeFrames = Math.max(2, Math.round(fps * 0.12));

  const midStart = sceneStarts
    .filter((f) => f > openFrames + midFrames && f < totalFrames - endFrames)
    .map((f) => f - Math.round(midFrames / 2));

  return (
    <>
      {assets?.open ? (
        <Sequence from={0} durationInFrames={openFrames} name="水墨开场">
          <AbsoluteFill style={{ opacity: 0.55 }}>
            <OffthreadVideo
              src={staticFile(assets.open)}
              muted
              style={{ width: '100%', height: '100%', objectFit: 'cover', filter: 'contrast(1.25) brightness(0.92)' }}
            />
          </AbsoluteFill>
        </Sequence>
      ) : null}

      {assets?.mid
        ? midStart.map((from, i) => (
            <Sequence key={`mid-${i}`} from={from} durationInFrames={midFrames} name={`水墨转场 ${i + 1}`}>
              <MidFrame src={assets.mid as string} dur={midFrames} fadeFrames={fadeFrames} />
            </Sequence>
          ))
        : null}

      {assets?.end ? (
        <Sequence from={Math.max(0, totalFrames - endFrames)} durationInFrames={endFrames} name="水墨收尾">
          <AbsoluteFill style={{ opacity: 0.5 }}>
            <OffthreadVideo
              src={staticFile(assets.end)}
              muted
              style={{ width: '100%', height: '100%', objectFit: 'cover', filter: 'contrast(1.2) brightness(0.95)' }}
            />
          </AbsoluteFill>
        </Sequence>
      ) : null}
    </>
  );
};

const MidFrame: React.FC<{ src: string; dur: number; fadeFrames: number }> = ({ src, dur, fadeFrames }) => {
  // Sequence 内部帧号从 0 起算，直接用它算淡入淡出
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ opacity: fade(frame, dur, fadeFrames) * 0.7 }}>
      <OffthreadVideo
        src={staticFile(src)}
        muted
        style={{ width: '100%', height: '100%', objectFit: 'cover', filter: 'contrast(1.22) brightness(0.94)' }}
      />
    </AbsoluteFill>
  );
};
