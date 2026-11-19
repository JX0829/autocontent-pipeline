import React from 'react';
import { AbsoluteFill, OffthreadVideo, staticFile, useVideoConfig, Img } from 'remotion';

/**
 * 水墨背景层（2026-11-19，SPEC 方案 C）
 *
 * 课文本（TextbookVideo）与水墨本（InkStoryVideo）共用：
 *   · bg  —— 全屏水墨视频底（循环铺底），压在正文文字之下
 *   · tex —— 纸纹/肌理叠加层（在最上面，极低不透明度，营造宣纸质感）
 *
 * 缺素材时**不画任何东西**，由外层保留原本的纸色纯色底 —— 不炸、不黑屏。
 * 所有素材来自素材库选材（ink_story），命名 `<slot>_<family>_v<N>.mp4`。
 */

export type InkBackdropLayerProps = {
  /** 背景视频（水墨山水/竹林/江面等），路径相对 renderer/public */
  bg?: string | null;
  /** 纸纹叠加，路径相对 renderer/public */
  tex?: string | null;
  /** 背景视频不透明度，水墨底色浅，太高会吃掉文字对比度 */
  bgOpacity?: number;
  /** 纸纹不透明度 */
  texOpacity?: number;
};

export const InkBackdropLayer: React.FC<InkBackdropLayerProps> = ({
  bg,
  tex,
  bgOpacity = 0.42,
  texOpacity = 0.16,
}) => {
  const { durationInFrames, fps } = useVideoConfig();
  if (!bg && !tex) return null;
  return (
    <>
      {bg ? (
        <AbsoluteFill style={{ opacity: bgOpacity }}>
          <OffthreadVideo
            src={staticFile(bg)}
            muted
            // 素材多为 4–10s，成片更长：靠 remotion 的 loop 语义不合适，
            // 这里按「播完停在最后一帧」处理，避免回跳闪断。
            startFrom={0}
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          />
          {/* 播完后用最后一帧撑住的兜底层：纯纸色，视觉上等于静止水墨 */}
          {durationInFrames / fps > 8 ? (
            <AbsoluteFill style={{ background: 'rgba(239,230,212,0.06)' }} />
          ) : null}
        </AbsoluteFill>
      ) : null}
      {tex ? (
        <AbsoluteFill style={{ opacity: texOpacity, mixBlendMode: 'multiply' }}>
          <Img
            src={staticFile(tex)}
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          />
        </AbsoluteFill>
      ) : null}
    </>
  );
};
