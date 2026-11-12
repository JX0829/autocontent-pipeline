import { BilibiliLandscapeVideo } from './components/BilibiliLandscapeVideo';
import { BilibiliLandscapeCover } from './components/BilibiliLandscapeCover';
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
import {
  TextbookStoryVideo,
  TextbookLandscapeVideo,
  TextbookCoverStill,
  TextbookLandscapeCover,
} from './textbook/TextbookVideo';


const calculateLandscapeMetadata: CalculateMetadataFunction<AlignedRenderPayload> = async ({
  props,
}) => {
  return {
    durationInFrames: props.total_duration_in_frames || 900,
    fps: props.fps || 30,
    width: 1920,
    height: 1080,
  };
};

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
  footer_callout?: string;
}> = ({ scene, totalScenes, entityKey, resourceKeyword, fps, footer_callout }) => {
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
            footer_callout={footer_callout}
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
            footer_callout={footer_callout}
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
              footer_callout={props.footer_callout}
            />
          </Sequence>
        ))}

        {/* 关键帧作为封面：前 0.5 秒 (15 帧) 渲染高清封面海报，确保各大平台和本地相册默认稳妥抓取大标题封面 */}
        <Sequence from={0} durationInFrames={15}>
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

// 封面的 `$ pip install xxx` 只在 resource_keyword 真的像包名时才渲染。
// 否则（中文短语、整句、空值）会渲染成 `$ pip install tool` 这种假命令——所有非开源类选题都是错的。
const PACKAGE_NAME_RE = /^[A-Za-z][A-Za-z0-9._-]{1,39}$/;
const TOPIC_LABEL: Record<string, string> = {
  'tech-github': '开源技术速递',
  'ai-product': 'AI 产品速递',
  'breaking-news': '科技快讯',
};
const topicLabel = (theme?: string) => TOPIC_LABEL[theme || ''] || '科技速递';
const asPackageName = (kw?: string) => {
  const v = (kw || '').trim();
  return PACKAGE_NAME_RE.test(v) ? v.toLowerCase() : '';
};

const CoverStillInner: React.FC<AlignedRenderPayload> = (props) => {
  const pkg = asPackageName(props.resource_keyword);
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
          {props.footer_callout || '💡 欢迎评论区交流'}
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
          {pkg ? `$ pip install ${pkg}` : (props.cover_sub_text || topicLabel(props.theme))}
        </span>
        <span style={{ color: tokens.textSecondary, fontSize: 20 }}>{topicLabel(props.theme)}</span>
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
  footer_callout: '💡 欢迎评论区交流',
  scenes: [],
};


const BilibiliCoverWrapper: React.FC<AlignedRenderPayload> = (props) => {
  return (
    <TemplateProvider templateId={props.template_id} theme={props.theme}>
      <BilibiliLandscapeCover {...props} />
    </TemplateProvider>
  );
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
            <Composition
        id="BilibiliLandscapeVideo"
        component={BilibiliLandscapeVideo}
        durationInFrames={900}
        fps={30}
        width={1920}
        height={1080}
        calculateMetadata={calculateLandscapeMetadata}
        defaultProps={defaultMockProps}
      />
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
      <Composition
        id="GeekHorizontalVideo"
        component={GeekShortVideo}
        durationInFrames={300}
        fps={30}
        width={1920}
        height={1080}
        calculateMetadata={calculateVideoMetadata}
        defaultProps={{
          ...defaultMockProps,
          width: 1920,
          height: 1080,
        }}
      />
            <Still
        id="BilibiliCoverStill"
        component={BilibiliCoverWrapper}
        width={1920}
        height={1080}
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
        id="CoverHorizontalStill"
        component={CoverStill}
        width={1920}
        height={1080}
        defaultProps={{
          ...defaultMockProps,
          width: 1920,
          height: 1080,
        }}
      />

      {/* ---------------------------------------------------------------- 课文故事（古风，F-2026-11-12 M2）
          与上面四个科技合成并列新增：科技链路仍渲染 GeekShortVideo / BilibiliLandscapeVideo，
          本组合只有 content_type=textbook_story 的物料才会被 executors 选中。 */}
      <Composition
        id="TextbookStoryVideo"
        component={TextbookStoryVideo}
        durationInFrames={2700}
        fps={30}
        width={1080}
        height={1920}
        calculateMetadata={calculateVideoMetadata}
        defaultProps={defaultMockProps}
      />
      <Composition
        id="TextbookLandscapeVideo"
        component={TextbookLandscapeVideo}
        durationInFrames={2700}
        fps={30}
        width={1920}
        height={1080}
        calculateMetadata={calculateLandscapeMetadata}
        defaultProps={defaultMockProps}
      />
      <Still
        id="TextbookCoverStill"
        component={TextbookCoverStill}
        width={1080}
        height={1920}
        defaultProps={defaultMockProps}
      />
      <Still
        id="TextbookLandscapeCoverStill"
        component={TextbookLandscapeCover}
        width={1920}
        height={1080}
        defaultProps={defaultMockProps}
      />
    </>
  );
};
