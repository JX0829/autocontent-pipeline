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
import { TemplateProvider, useTemplate } from './templates/context';
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

const VideoSceneContent: React.FC<{
  scene: AlignedSceneItem;
  totalScenes: number;
  entityKey?: string;
  resourceKeyword?: string;
  fps: number;
}> = ({ scene, totalScenes, entityKey, resourceKeyword, fps }) => {
  return (
    <SafeAreaContainer showDebugBorder={false}>
      {/* 上方核心视觉容器 */}
      <div style={{ width: '100%', marginTop: 20 }}>
        {scene.visual_type === 'terminal_code' && (
          <TerminalCodeView
            sceneIndex={scene.scene_index}
            totalScenes={totalScenes}
            entityKey={entityKey}
            resourceKeyword={resourceKeyword}
            codeSnippet={scene.code_snippet || ''}
            highlightText={scene.highlight_text}
          />
        )}
        {scene.visual_type === 'ui_card' && (
          <UiCardView
            sceneIndex={scene.scene_index}
            totalScenes={totalScenes}
            entityKey={entityKey}
            highlightText={scene.highlight_text}
            statItems={scene.stat_items || []}
          />
        )}
        {scene.visual_type === 'stat_chart' && (
          <StatChartView
            sceneIndex={scene.scene_index}
            totalScenes={totalScenes}
            entityKey={entityKey}
            highlightText={scene.highlight_text}
            statItems={scene.stat_items || []}
          />
        )}
        {scene.visual_type === 'media_showcase' && (
          <MediaShowcaseView
            sceneIndex={scene.scene_index}
            totalScenes={totalScenes}
            entityKey={entityKey}
            resourceKeyword={resourceKeyword}
            highlightText={scene.highlight_text}
            mediaAssetUrl={
              scene.media_asset_url ? staticFile(scene.media_asset_url) : undefined
            }
          />
        )}
      </div>

      {/* 下方安全区动态字幕 */}
      <SubtitleOverlay subtitles={scene.subtitles} />

      {/* 音频与音效轨道 */}
      {scene.audio_src && <Audio src={staticFile(scene.audio_src)} />}
      {scene.sfx_src && <Audio src={staticFile(scene.sfx_src)} volume={0.6} />}
    </SafeAreaContainer>
  );
};

export const GeekShortVideo: React.FC<AlignedRenderPayload> = (props) => {
  return (
    <TemplateProvider templateId={props.template_id} theme={props.theme}>
      <div style={{ flex: 1, position: 'relative', width: '100%', height: '100%' }}>
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
            <VideoSceneContent
              scene={scene}
              totalScenes={props.scenes.length}
              entityKey={props.resource_keyword || 'OpenSource'}
              resourceKeyword={props.resource_keyword}
              fps={props.fps}
            />
          </Sequence>
        ))}

        {/* 关键帧作为封面：第 0 帧渲染高清封面海报（定格 1 帧），确保抖音和本地相册默认直接提取该图作为封面 */}
        <Sequence from={0} durationInFrames={1}>
          <CoverStillInner {...props} />
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
    </TemplateProvider>
  );
};

const CoverStillInner: React.FC<AlignedRenderPayload> = (props) => {
  const { id, tokens, layout } = useTemplate();

  return (
    <div
      style={{
        width: 1080,
        height: 1920,
        background: tokens.canvasBg,
        color: tokens.textPrimary,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        position: 'relative',
        boxSizing: 'border-box',
        padding: '0 80px',
        overflow: 'hidden',
      }}
    >
      {/* 环境光晕 */}
      {tokens.ambientGlowPrimary && (
        <div
          style={{
            position: 'absolute',
            width: 700,
            height: 700,
            borderRadius: '50%',
            background: `radial-gradient(circle, ${tokens.ambientGlowPrimary} 0%, transparent 70%)`,
            filter: 'blur(70px)',
            pointerEvents: 'none',
          }}
        />
      )}

      {/* 顶部标签 */}
      <div
        style={{
          padding: '14px 36px',
          background: tokens.badgeBg,
          color: tokens.badgeText,
          border: tokens.badgeBorder || 'none',
          borderRadius: layout.useSerifHeadline ? 8 : 50,
          fontSize: 34,
          fontWeight: 800,
          letterSpacing: 2,
          marginBottom: 40,
          textTransform: 'uppercase',
          boxShadow: tokens.cardShadow,
        }}
      >
        {props.cover_sub_text || 'GitHub 趋势'}
      </div>

      {/* 封面正中央超大主标题（九宫格居中防裁切） */}
      <div
        style={{
          fontSize: 82,
          fontWeight: 900,
          color: tokens.textPrimary,
          textAlign: 'center',
          lineHeight: 1.25,
          letterSpacing: 2,
          maxWidth: 920,
          marginBottom: 36,
          fontFamily: layout.useSerifHeadline ? 'Georgia, serif' : 'inherit',
        }}
      >
        {props.cover_main_text || props.title}
      </div>

      {/* 封面正中央技术交流提示徽章 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '14px 32px',
          background: tokens.cardBg,
          border: tokens.cardBorder,
          borderRadius: 30,
          marginBottom: 44,
          boxShadow: tokens.cardShadow,
        }}
      >
        <span style={{ fontSize: 24 }}>💡</span>
        <span
          style={{
            color: tokens.accentPrimary,
            fontSize: 24,
            fontWeight: 800,
            letterSpacing: 1.5,
          }}
        >
          欢迎在评论区一起交流探讨
        </span>
      </div>

      {/* 底部代码卡片缩略装饰 */}
      <div
        style={{
          width: '100%',
          borderRadius: tokens.cardRadius,
          background: tokens.cardBg,
          border: tokens.cardBorder,
          padding: '24px 30px',
          boxShadow: tokens.cardShadow,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <span style={{ color: tokens.accentPrimary, fontSize: 26, fontFamily: 'monospace', fontWeight: 700 }}>
          $ pip install {props.resource_keyword ? props.resource_keyword.toLowerCase() : 'tool'}
        </span>
        <span style={{ color: tokens.textSecondary, fontSize: 20 }}>开源技术速递</span>
      </div>
    </div>
  );
};

export const CoverStill: React.FC<AlignedRenderPayload> = (props) => {
  return (
    <TemplateProvider templateId={props.template_id} theme={props.theme}>
      <CoverStillInner {...props} />
    </TemplateProvider>
  );
};

export const XhsCardStill: React.FC<AlignedRenderPayload & { frameIndex?: number }> = (
  props
) => {
  return (
    <TemplateProvider templateId={props.template_id} theme={props.theme}>
      <XhsCardStillInner {...props} />
    </TemplateProvider>
  );
};

const XhsCardStillInner: React.FC<AlignedRenderPayload & { frameIndex?: number }> = (
  props
) => {
  const { tokens, layout } = useTemplate();

  return (
    <div
      style={{
        width: 1242,
        height: 1660,
        background: tokens.canvasBg,
        color: tokens.textPrimary,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        padding: '80px 70px',
        boxSizing: 'border-box',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* 顶部标题栏 */}
      <div>
        <div
          style={{
            display: 'inline-block',
            padding: '10px 24px',
            background: tokens.badgeBg,
            border: tokens.badgeBorder || 'none',
            borderRadius: 30,
            color: tokens.badgeText,
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
            color: tokens.textPrimary,
            lineHeight: 1.3,
            letterSpacing: 1,
            textShadow: tokens.cardShadow,
            fontFamily: layout.useSerifHeadline ? 'Georgia, serif' : 'inherit',
          }}
        >
          {props.title}
        </div>
      </div>

      {/* 中央核心代码/卡片展示 */}
      <div
        style={{
          width: '100%',
          borderRadius: tokens.cardRadius,
          background: tokens.cardBg,
          border: tokens.cardBorder,
          padding: 40,
          boxShadow: tokens.cardShadow,
        }}
      >
        <div
          style={{
            fontSize: 28,
            color: tokens.codeText,
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
          background: tokens.cardBg,
          borderRadius: 20,
          border: tokens.cardBorder,
        }}
      >
        <span style={{ fontSize: 24, color: tokens.accentPrimary, fontWeight: 800 }}>
          💡 欢迎在评论区一起交流探讨
        </span>
        <span style={{ fontSize: 20, color: tokens.textSecondary }}>科技极客速递</span>
      </div>
    </div>
  );
};

const defaultMockProps: AlignedRenderPayload = {
  task_id: 'default_demo',
  template_id: 'linear_obsidian',
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
        durationInFrames={300}
        fps={30}
        width={1080}
        height={1920}
        calculateMetadata={calculateVideoMetadata}
        defaultProps={defaultMockProps}
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
