import React from 'react';
import {
  Composition,
  Still,
  Sequence,
  Audio,
  staticFile,
  CalculateMetadataFunction,
} from 'remotion';
import { AlignedRenderPayload, AlignedSceneItem } from './types';
import { SafeAreaContainer } from './components/SafeAreaContainer';
import { TerminalCodeView } from './components/TerminalCodeView';
import { UiCardView } from './components/UiCardView';
import { StatChartView } from './components/StatChartView';
import { MediaShowcaseView } from './components/MediaShowcaseView';
import { SubtitleOverlay } from './components/SubtitleOverlay';

const calculateVideoMetadata: CalculateMetadataFunction<AlignedRenderPayload> = async ({
  props,
}) => {
  return {
    durationInFrames: props.total_duration_in_frames || 900,
    fps: props.fps || 30,
    width: props.width || 1080,
    height: props.height || 1920,
  };
};

const VideoScene: React.FC<{ scene: AlignedSceneItem; fps: number }> = ({
  scene,
  fps,
}) => {
  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        background: 'radial-gradient(ellipse at 50% 25%, #0f172a 0%, #020617 100%)',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* 科技背景动态网格光效 */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundImage:
            'linear-gradient(rgba(56, 189, 248, 0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(56, 189, 248, 0.04) 1px, transparent 1px)',
          backgroundSize: '40px 40px',
          opacity: 0.6,
        }}
      />

      <SafeAreaContainer showDebugBorder={false}>
        {/* 上方核心视觉容器 */}
        <div style={{ width: '100%', marginTop: 20 }}>
          {scene.visual_type === 'terminal_code' && (
            <TerminalCodeView
              codeSnippet={scene.code_snippet}
              highlightText={scene.highlight_text}
            />
          )}
          {scene.visual_type === 'ui_card' && (
            <UiCardView
              highlightText={scene.highlight_text}
              statItems={scene.stat_items}
            />
          )}
          {scene.visual_type === 'stat_chart' && (
            <StatChartView
              highlightText={scene.highlight_text}
              statItems={scene.stat_items}
            />
          )}
          {scene.visual_type === 'media_showcase' && (
            <MediaShowcaseView
              highlightText={scene.highlight_text}
              mediaAssetUrl={
                scene.media_asset_url ? staticFile(scene.media_asset_url) : undefined
              }
            />
          )}
        </div>

        {/* 下方安全区动态字幕 */}
        <div style={{ width: '100%', marginBottom: 30 }}>
          <SubtitleOverlay subtitles={scene.subtitles} fps={fps} />
        </div>
      </SafeAreaContainer>

      {/* 音频与音效轨道 */}
      {scene.audio_src && <Audio src={staticFile(scene.audio_src)} />}
      {scene.sfx_src && <Audio src={staticFile(scene.sfx_src)} volume={0.6} />}
    </div>
  );
};

export const GeekShortVideo: React.FC<AlignedRenderPayload> = (props) => {
  return (
    <div style={{ flex: 1, backgroundColor: '#020617', position: 'relative' }}>
      {/* 背景循环音乐 (-24dB 极客环境底噪) */}
      {props.bgm_src && (
        <Audio src={staticFile(props.bgm_src)} volume={0.06} loop />
      )}

      {/* 视频正文各分镜 */}
      {props.scenes.map((scene) => (
        <Sequence
          key={scene.scene_index}
          from={scene.start_frame}
          durationInFrames={scene.duration_frames}
        >
          <VideoScene scene={scene} fps={props.fps} />
        </Sequence>
      ))}

      {/* 关键帧作为封面：第 0 帧渲染高清封面海报（定格 1 帧），确保抖音和本地相册默认直接提取该图作为封面 */}
      <Sequence from={0} durationInFrames={1}>
        <CoverStill {...props} />
      </Sequence>

      {/* 左下角合规 AIGC 声明水印 */}
      <div
        style={{
          position: 'absolute',
          bottom: 40,
          left: 96,
          padding: '6px 14px',
          background: 'rgba(0, 0, 0, 0.45)',
          borderRadius: 12,
          color: '#64748b',
          fontSize: 16,
          fontWeight: 600,
          border: '1px solid rgba(255, 255, 255, 0.08)',
          zIndex: 100,
        }}
      >
        AI辅助生成
      </div>
    </div>
  );
};

export const CoverStill: React.FC<AlignedRenderPayload> = (props) => {
  return (
    <div
      style={{
        width: 1080,
        height: 1920,
        background: 'radial-gradient(circle at 50% 35%, #0f172a 0%, #020617 100%)',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        position: 'relative',
        boxSizing: 'border-box',
        padding: '0 80px',
      }}
    >
      {/* 科技背景光 */}
      <div
        style={{
          position: 'absolute',
          width: 600,
          height: 600,
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(56, 189, 248, 0.25) 0%, transparent 70%)',
          filter: 'blur(60px)',
        }}
      />

      {/* 顶部标签 */}
      <div
        style={{
          padding: '14px 36px',
          background: 'rgba(56, 189, 248, 0.15)',
          border: '2px solid rgba(56, 189, 248, 0.7)',
          borderRadius: 50,
          color: '#38bdf8',
          fontSize: 34,
          fontWeight: 800,
          letterSpacing: 2,
          marginBottom: 40,
          textTransform: 'uppercase',
          boxShadow: '0 0 40px rgba(56, 189, 248, 0.3)',
        }}
      >
        {props.cover_sub_text || 'GitHub 趋势'}
      </div>

      {/* 封面正中央超大主标题（九宫格居中防裁切） */}
      <div
        style={{
          fontSize: 82,
          fontWeight: 900,
          color: '#ffffff',
          textAlign: 'center',
          lineHeight: 1.25,
          letterSpacing: 2,
          textShadow: '0 6px 30px rgba(0, 0, 0, 0.9), 0 0 50px rgba(56, 189, 248, 0.5)',
          maxWidth: 920,
          marginBottom: 36,
        }}
      >
        {props.cover_main_text || props.title}
      </div>

      {/* 封面正中央 AI 互动引导提示徽章 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '12px 28px',
          background: 'rgba(16, 185, 129, 0.15)',
          border: '1.5px solid rgba(52, 211, 153, 0.6)',
          borderRadius: 30,
          marginBottom: 44,
          boxShadow: '0 0 25px rgba(16, 185, 129, 0.25)',
          backdropFilter: 'blur(10px)',
        }}
      >
        <span style={{ fontSize: 24 }}>💡</span>
        <span
          style={{
            color: '#34d399',
            fontSize: 24,
            fontWeight: 800,
            letterSpacing: 1.5,
          }}
        >
          此视频评论区已接入智能回复，欢迎体验
        </span>
      </div>

      {/* 底部极客终端卡片缩略装饰 */}
      <div
        style={{
          width: '100%',
          borderRadius: 20,
          background: 'rgba(15, 23, 42, 0.85)',
          border: '1.5px solid rgba(255, 255, 255, 0.15)',
          padding: '24px 30px',
          boxShadow: '0 20px 50px rgba(0,0,0,0.8)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <span style={{ color: '#38bdf8', fontSize: 26, fontFamily: 'monospace', fontWeight: 700 }}>
          $ pip install {props.resource_keyword.toLowerCase()}
        </span>
        <span style={{ color: '#94a3b8', fontSize: 20 }}>开源技术速递</span>
      </div>
    </div>
  );
};

export const XhsCardStill: React.FC<AlignedRenderPayload & { frameIndex?: number }> = (
  props
) => {
  return (
    <div
      style={{
        width: 1242,
        height: 1660,
        background: 'radial-gradient(circle at 50% 30%, #0f172a 0%, #020617 100%)',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        padding: '80px 70px',
        boxSizing: 'border-box',
        position: 'relative',
      }}
    >
      {/* 顶部标题栏 */}
      <div>
        <div
          style={{
            display: 'inline-block',
            padding: '10px 24px',
            background: 'rgba(56, 189, 248, 0.15)',
            border: '1.5px solid rgba(56, 189, 248, 0.6)',
            borderRadius: 30,
            color: '#38bdf8',
            fontSize: 24,
            fontWeight: 800,
            marginBottom: 24,
          }}
        >
          {props.cover_sub_text}
        </div>
        <div
          style={{
            fontSize: 54,
            fontWeight: 900,
            color: '#ffffff',
            lineHeight: 1.3,
            letterSpacing: 1,
            textShadow: '0 4px 20px rgba(0,0,0,0.8)',
          }}
        >
          {props.title}
        </div>
      </div>

      {/* 中央核心代码/卡片展示 */}
      <div
        style={{
          width: '100%',
          borderRadius: 24,
          background: 'rgba(15, 23, 42, 0.9)',
          border: '2px solid rgba(56, 189, 248, 0.3)',
          padding: 40,
          boxShadow: '0 25px 60px rgba(0,0,0,0.8)',
        }}
      >
        <div
          style={{
            fontSize: 28,
            color: '#f8fafc',
            fontFamily: 'monospace',
            lineHeight: 1.6,
            whiteSpace: 'pre-wrap',
          }}
        >
          {props.scenes[0]?.code_snippet ||
            '# 一键转换各种复杂文档\nmarkitdown report.xlsx -o output.md'}
        </div>
      </div>

      {/* 底部互动栏：纯技术交流 */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '20px 30px',
          background: 'rgba(30, 41, 59, 0.7)',
          borderRadius: 20,
          border: '1px solid rgba(255, 255, 255, 0.1)',
        }}
      >
        <span style={{ fontSize: 24, color: '#38bdf8', fontWeight: 800 }}>
          💡 欢迎在评论区一起交流探讨
        </span>
        <span style={{ fontSize: 20, color: '#94a3b8' }}>科技极客速递</span>
      </div>
    </div>
  );
};

const defaultMockProps: AlignedRenderPayload = {
  task_id: 'default_demo',
  fps: 30,
  width: 1080,
  height: 1920,
  total_duration_in_frames: 300,
  theme: 'tech-github',
  title: '微软开源万物转Markdown工具',
  cover_main_text: '万物转MD神器',
  cover_sub_text: 'GitHub 趋势',
  resource_keyword: 'MarkItDown',
  bgm_src: 'sfx/bgm_loop.wav',
  scenes: [],
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="GeekShortVideo"
        component={GeekShortVideo}
        durationInFrames={900}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={defaultMockProps}
        calculateMetadata={calculateVideoMetadata}
      />
      <Still
        id="CoverStill"
        component={CoverStill}
        width={1080}
        height={1920}
        defaultProps={defaultMockProps}
      />
      <Still
        id="XhsCardStill"
        component={XhsCardStill}
        width={1242}
        height={1660}
        defaultProps={defaultMockProps}
      />
    </>
  );
};
