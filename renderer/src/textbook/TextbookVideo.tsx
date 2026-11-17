/**
 * 课文故事片（F-2026-11-12-TEXTBOOK-STORY · M2）
 *
 * 独立的 9:16 / 16:9 合成，与科技链路（GeekShortVideo / BilibiliLandscapeVideo）并列：
 *   · 不改 GeekShortVideo 的任何分支与默认值
 *   · 五类古风 visual_type 只在本合成里渲染，科技片永远走原组件
 *
 * 前 15 帧固定渲染封面静帧（与封面导出同一组件），
 * 这样 landscape_guard 的「首帧 vs 封面」一致性判据天然成立。
 */
import React from 'react';
import { AbsoluteFill, Audio, Sequence, staticFile, useVideoConfig, useCurrentFrame } from 'remotion';
import { AlignedRenderPayload } from '../types';
import { InkScene, InkSubtitle, InkBackdrop, Seal } from './InkScenes';
import { ink, splitPoemColumns } from './theme';

const COVER_FRAMES = 15;

interface TextbookMeta {
  poemTitle?: string;
  author?: string;
  dynasty?: string;
  fullText?: string;
  edition?: string;
  attribution?: string;
  aiLabel?: boolean;
}

function readMeta(props: AlignedRenderPayload): TextbookMeta {
  const tb = (props as any).textbook || {};
  const cp = (props as any).compliance || {};
  return {
    poemTitle: tb.poem_title,
    author: tb.author,
    dynasty: tb.dynasty,
    fullText: tb.full_text,
    edition: tb.edition
      ? `${tb.edition}${tb.grade || ''}${tb.volume || ''}`
      : cp.textbook_attribution,
    attribution: cp.textbook_attribution,
    aiLabel: cp.ai_generated_label !== false,
  };
}

/* ------------------------------------------------------------------ 封面（竖版） */

const CoverVertical: React.FC<{ props: AlignedRenderPayload }> = ({ props }) => {
  const meta = readMeta(props);
  const title = props.cover_main_text || props.title || '';
  const cols = splitPoemColumns(meta.fullText || '', 7).slice(0, 6);

  return (
    <InkBackdrop>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '120px 80px',
          boxSizing: 'border-box',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 40 }}>
          <div
            style={{
              writingMode: 'vertical-rl',
              fontFamily: ink.fontKai,
              fontSize: Math.min(150, 1200 / Math.max(4, Array.from(title).length)),
              fontWeight: 700,
              color: ink.ink,
              letterSpacing: 12,
              lineHeight: 1.16,
              textShadow: '0 3px 20px rgba(28,26,23,0.12)',
            }}
          >
            {title}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 26, alignItems: 'center' }}>
            <Seal size={112} />
            {cols.length ? (
              <div
                style={{
                  writingMode: 'vertical-rl',
                  fontFamily: ink.fontKai,
                  fontSize: 34,
                  color: ink.inkSoft,
                  opacity: 0.8,
                  maxHeight: 620,
                  letterSpacing: 6,
                }}
              >
                {cols.join('')}
              </div>
            ) : null}
          </div>
        </div>

        <div
          style={{
            marginTop: 70,
            padding: '12px 34px',
            border: `1px solid ${ink.inkFaint}`,
            borderRadius: 6,
            fontFamily: ink.fontSong,
            fontSize: 34,
            color: ink.inkSoft,
            letterSpacing: 6,
          }}
        >
          {props.cover_sub_text || '课文背后'}
        </div>

        <div
          style={{
            position: 'absolute',
            bottom: 96,
            fontFamily: ink.fontSong,
            fontSize: 22,
            color: ink.inkSoft,
            opacity: 0.62,
            letterSpacing: 2,
          }}
        >
          {[meta.attribution || meta.edition, meta.aiLabel ? 'AI 辅助生成' : ''].filter(Boolean).join(' · ')}
        </div>
      </div>
    </InkBackdrop>
  );
};

/* ------------------------------------------------------------------ 封面（横版，需过 landscape_guard） */

const CoverLandscape: React.FC<{ props: AlignedRenderPayload }> = ({ props }) => {
  const meta = readMeta(props);
  const title = props.cover_main_text || props.title || '';
  const cols = splitPoemColumns(meta.fullText || '', 7).slice(0, 4);

  return (
    <InkBackdrop>
      {/* 横向墨痕：贯穿整幅，保证横屏两侧不留纯色带（护栏按列灰度判内容） */}
      {[
        { top: '18%', h: '16%', o: 0.16 },
        { top: '46%', h: '12%', o: 0.1 },
        { top: '72%', h: '18%', o: 0.14 },
      ].map((b, i) => (
        <div
          key={i}
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            top: b.top,
            height: b.h,
            background: `linear-gradient(90deg, rgba(28,26,23,0) 0%, rgba(28,26,23,${b.o}) 22%, rgba(28,26,23,${b.o * 0.7}) 55%, rgba(28,26,23,0) 100%)`,
            filter: 'blur(14px)',
          }}
        />
      ))}

      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '70px 90px',
          boxSizing: 'border-box',
        }}
      >
        <div
          style={{
            writingMode: 'vertical-rl',
            fontFamily: ink.fontKai,
            fontSize: 36,
            color: ink.inkSoft,
            letterSpacing: 8,
            maxHeight: 760,
            opacity: 0.85,
          }}
        >
          {cols.join('') || meta.poemTitle || ''}
        </div>

        <div
          style={{
            flex: 1,
            padding: '0 50px',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 30,
          }}
        >
          <div
            style={{
              fontFamily: ink.fontKai,
              fontWeight: 700,
              fontSize: Math.min(120, 1500 / Math.max(6, Array.from(title).length)),
              color: ink.ink,
              letterSpacing: 10,
              textAlign: 'center',
              lineHeight: 1.22,
              textShadow: '0 3px 22px rgba(28,26,23,0.14)',
            }}
          >
            {title}
          </div>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 26,
            }}
          >
            <span
              style={{
                fontFamily: ink.fontSong,
                fontSize: 36,
                color: ink.inkSoft,
                letterSpacing: 6,
                borderTop: `1px solid ${ink.inkFaint}`,
                borderBottom: `1px solid ${ink.inkFaint}`,
                padding: '10px 0',
              }}
            >
              {props.cover_sub_text || '课文背后'}
            </span>
            <Seal size={92} />
          </div>
        </div>

        <div
          style={{
            writingMode: 'vertical-rl',
            fontFamily: ink.fontSong,
            fontSize: 26,
            color: ink.inkSoft,
            opacity: 0.7,
            letterSpacing: 4,
            maxHeight: 700,
          }}
        >
          {[meta.attribution || meta.edition, meta.aiLabel ? 'AI 辅助生成' : ''].filter(Boolean).join(' · ')}
        </div>
      </div>
    </InkBackdrop>
  );
};

/* ------------------------------------------------------------------ 视频合成（两版共用） */

const SceneBody: React.FC<{ props: AlignedRenderPayload; landscape: boolean }> = ({ props, landscape }) => {
  const meta = readMeta(props);
  const poetry = {
    poemTitle: meta.poemTitle,
    author: meta.author,
    dynasty: meta.dynasty,
    fullText: meta.fullText,
    edition: meta.attribution || meta.edition,
  };

  return (
    <InkBackdrop>
      {props.scenes.map((scene) => (
        <Sequence key={scene.scene_index} from={scene.start_frame} durationInFrames={scene.duration_frames}>
          <InkScene scene={scene} poetry={poetry} />
          {scene.audio_src ? <Audio src={staticFile(scene.audio_src)} /> : null}
          {scene.sfx_src ? <Audio src={staticFile(scene.sfx_src)} volume={0.5} /> : null}
        </Sequence>
      ))}

      {!landscape ? <InkSubtitle subtitles={props.scenes.flatMap((s) => s.subtitles || [])} /> : null}

      {/* 片尾合规标注：AI 生成 + 教材版本（spec F7） */}
      <div
        style={{
          position: 'absolute',
          bottom: 40,
          left: 96,
          fontFamily: ink.fontSong,
          fontSize: 20,
          color: ink.inkSoft,
          opacity: 0.72,
          letterSpacing: 1.5,
          zIndex: 90,
        }}
      >
        {[meta.aiLabel ? 'AI 辅助生成' : '', meta.attribution || meta.edition].filter(Boolean).join(' · ')}
      </div>
    </InkBackdrop>
  );
};

const Overlay = (props: AlignedRenderPayload) => (
  <Sequence from={0} durationInFrames={COVER_FRAMES}>
    <CoverVertical props={props} />
  </Sequence>
);

const LandscapeOverlay = (props: AlignedRenderPayload) => (
  <Sequence from={0} durationInFrames={COVER_FRAMES}>
    <CoverLandscape props={props} />
  </Sequence>
);

export const TextbookStoryVideo: React.FC<AlignedRenderPayload> = (props) => {
  const { width, height } = useVideoConfig();
  return (
    <AbsoluteFill style={{ background: ink.paper }}>
      {props.bgm_src ? <Audio src={staticFile(props.bgm_src)} volume={0.05} loop /> : null}
      <SceneBody props={props} landscape={false} />
      <Sequence from={0} durationInFrames={COVER_FRAMES}>
        <AbsoluteFill style={{ width, height }}>
          <CoverVertical props={props} />
        </AbsoluteFill>
      </Sequence>
    </AbsoluteFill>
  );
};

export const TextbookLandscapeVideo: React.FC<AlignedRenderPayload> = (props) => {
  const { width, height } = useVideoConfig();
  return (
    <AbsoluteFill style={{ background: ink.paper }}>
      {props.bgm_src ? <Audio src={staticFile(props.bgm_src)} volume={0.05} loop /> : null}
      <SceneBody props={props} landscape />
      <Sequence from={0} durationInFrames={COVER_FRAMES}>
        <AbsoluteFill style={{ width, height }}>
          <CoverLandscape props={props} />
        </AbsoluteFill>
      </Sequence>
    </AbsoluteFill>
  );
};

/* ------------------------------------------------------------------ 封面静帧 */

export const TextbookCoverStill: React.FC<AlignedRenderPayload> = (props) => (
  <AbsoluteFill>
    <CoverVertical props={props} />
  </AbsoluteFill>
);

export const TextbookLandscapeCover: React.FC<AlignedRenderPayload> = (props) => (
  <AbsoluteFill>
    <CoverLandscape props={props} />
  </AbsoluteFill>
);
