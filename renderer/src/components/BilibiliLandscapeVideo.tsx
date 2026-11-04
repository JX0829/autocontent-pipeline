import React from 'react';
import { Sequence, Audio, staticFile } from 'remotion';
import { AlignedRenderPayload, AlignedSceneItem } from '../types';
import { TemplateProvider, useTemplate } from '../templates/context';
import { LandscapeTerminalCodeView } from './LandscapeTerminalCodeView';
import { LandscapeUiCardView } from './LandscapeUiCardView';
import { LandscapeStatChartView } from './LandscapeStatChartView';
import { SubtitleOverlay } from './SubtitleOverlay';

const LandscapeSceneContent: React.FC<{
  scene: AlignedSceneItem;
  totalScenes: number;
  entityKey?: string;
  resourceKeyword?: string;
  fps: number;
  footer_callout?: string;
}> = ({ scene, totalScenes, entityKey, resourceKeyword, fps, footer_callout }) => {
  const { tokens } = useTemplate();

  return (
    <div
      style={{
        width: 1920,
        height: 1080,
        position: 'relative',
        boxSizing: 'border-box',
        padding: '50px 80px',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        overflow: 'hidden',
      }}
    >
      {/* 顶部 B 站专属导航与热点标签栏 */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          width: '100%',
          zIndex: 10,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <span
            style={{
              padding: '8px 20px',
              background: 'rgba(56, 189, 248, 0.15)',
              color: '#38bdf8',
              borderRadius: 12,
              fontSize: 20,
              fontWeight: 800,
              border: '1px solid rgba(56, 189, 248, 0.3)',
            }}
          >
            🔥 B站前沿科技速递
          </span>
          <span style={{ color: tokens.textSecondary || '#94a3b8', fontSize: 22, fontWeight: 600 }}>
            {entityKey || '开源爆款'}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span
            style={{
              color: tokens.accentPrimary || '#38bdf8',
              fontSize: 20,
              fontWeight: 700,
              background: 'rgba(255, 255, 255, 0.05)',
              padding: '8px 20px',
              borderRadius: 20,
              border: '1px solid rgba(255, 255, 255, 0.1)',
            }}
          >
            {scene.highlight_text || '极客首发'}
          </span>
        </div>
      </div>

      {/* 核心双栏极客工作台容器 (16:9 横屏原生宽屏左右布局) */}
      <div
        style={{
          width: '100%',
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 60,
          margin: '10px 0',
          zIndex: 10,
        }}
      >
        {/* 左侧工作区 (占 58% 宽度，原生适配横屏高度，绝不裁切) */}
        <div style={{ flex: 1.25, height: '100%', display: 'flex', alignItems: 'center' }}>
          {scene.visual_type === 'terminal_code' && (
            <LandscapeTerminalCodeView
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
            <LandscapeUiCardView
              sceneIndex={scene.scene_index}
              totalScenes={totalScenes}
              entityKey={entityKey}
              highlightText={scene.highlight_text}
              statItems={scene.stat_items || []}
            />
          )}
          {scene.visual_type === 'stat_chart' && (
            <LandscapeStatChartView
              sceneIndex={scene.scene_index}
              totalScenes={totalScenes}
              entityKey={entityKey}
              highlightText={scene.highlight_text}
              statItems={scene.stat_items || []}
            />
          )}
          {scene.visual_type === 'media_showcase' && (
            <LandscapeUiCardView
              sceneIndex={scene.scene_index}
              totalScenes={totalScenes}
              entityKey={entityKey}
              highlightText={scene.highlight_text}
              statItems={scene.stat_items || []}
            />
          )}
        </div>

        {/* 右侧实时指标看板与技术解读 (占 42% 宽度) */}
        <div
          style={{
            flex: 0.75,
            display: 'flex',
            flexDirection: 'column',
            gap: 20,
            height: '100%',
            justifyContent: 'center',
          }}
        >
          {scene.stat_items && scene.stat_items.length > 0 ? (
            scene.stat_items.map((item, idx) => (
              <div
                key={idx}
                style={{
                  background: 'rgba(15, 23, 42, 0.85)',
                  border: '1px solid rgba(255, 255, 255, 0.1)',
                  borderRadius: 18,
                  padding: '20px 24px',
                  boxShadow: '0 10px 30px rgba(0, 0, 0, 0.4)',
                }}
              >
                <div style={{ color: '#94a3b8', fontSize: 18, fontWeight: 600, marginBottom: 4 }}>
                  {item.label}
                </div>
                <div style={{ color: idx === 0 ? '#38bdf8' : '#c084fc', fontSize: 32, fontWeight: 900 }}>
                  {item.value}
                </div>
              </div>
            ))
          ) : (
            <div
              style={{
                background: 'rgba(15, 23, 42, 0.85)',
                border: '1px solid rgba(56, 189, 248, 0.25)',
                borderRadius: 18,
                padding: '24px',
                boxShadow: '0 10px 30px rgba(0, 0, 0, 0.4)',
              }}
            >
              <div style={{ color: '#38bdf8', fontSize: 24, fontWeight: 800, marginBottom: 8 }}>
                ⚡ 极客生产力突破
              </div>
              <div style={{ color: '#cbd5e1', fontSize: 18, lineHeight: 1.5 }}>
                数据中心级模型服务下放至个人桌面，普通GPU即开即用，极大加速本地AI部署。
              </div>
            </div>
          )}

          {/* 底部置顶提示徽章 */}
          <div
            style={{
              background: 'rgba(56, 189, 248, 0.08)',
              border: '1px solid rgba(56, 189, 248, 0.2)',
              borderRadius: 16,
              padding: '16px 20px',
              display: 'flex',
              alignItems: 'center',
              gap: 12,
            }}
          >
            <span style={{ fontSize: 22 }}>📌</span>
            <span style={{ color: '#38bdf8', fontSize: 16, fontWeight: 700 }}>
              {footer_callout || '完整项目直链与启动命令见视频简介与置顶'}
            </span>
          </div>
        </div>
      </div>

      {/* 底部宽屏横贯式字幕 */}
      <div style={{ width: '100%', zIndex: 20, marginBottom: 6 }}>
        <SubtitleOverlay subtitles={scene.subtitles} />
      </div>

      {/* 音频轨道 */}
      {scene.audio_src && <Audio src={staticFile(scene.audio_src)} />}
      {scene.sfx_src && <Audio src={staticFile(scene.sfx_src)} volume={0.6} />}
    </div>
  );
};

export const BilibiliLandscapeVideo: React.FC<AlignedRenderPayload> = (props) => {
  return (
    <TemplateProvider templateId={props.template_id} theme={props.theme}>
      <BilibiliLandscapeVideoInner {...props} />
    </TemplateProvider>
  );
};

const BilibiliLandscapeVideoInner: React.FC<AlignedRenderPayload> = (props) => {
  const { tokens, layout } = useTemplate();

  return (
    <div
      style={{
        flex: 1,
        position: 'relative',
        width: 1920,
        height: 1080,
        background: tokens.canvasBg || '#090d16',
        color: tokens.textPrimary || '#f8fafc',
      }}
    >
      {/* 动态网格/纹理背景 */}
      {tokens.patternType === 'linear_grid' && (
        <div
          style={{
            position: 'absolute',
            width: '100%',
            height: '100%',
            backgroundImage: `linear-gradient(${tokens.patternColor || 'rgba(255,255,255,0.05)'} 1px, transparent 1px), linear-gradient(90deg, ${tokens.patternColor || 'rgba(255,255,255,0.05)'} 1px, transparent 1px)`,
            backgroundSize: `${tokens.patternSizePx || 48}px ${tokens.patternSizePx || 48}px`,
            opacity: 0.6,
            pointerEvents: 'none',
          }}
        />
      )}

      {/* 背景光晕环境氛围：100% 同步当前主题模版 tokens */}
      {tokens.ambientGlowPrimary && (
        <div
          style={{
            position: 'absolute',
            width: 1200,
            height: 1200,
            left: -200,
            top: -300,
            borderRadius: '50%',
            background: `radial-gradient(circle, ${tokens.ambientGlowPrimary} 0%, transparent 65%)`,
            filter: 'blur(110px)',
            pointerEvents: 'none',
          }}
        />
      )}
      {tokens.ambientGlowSecondary && (
        <div
          style={{
            position: 'absolute',
            width: 900,
            height: 900,
            right: -100,
            bottom: -200,
            borderRadius: '50%',
            background: `radial-gradient(circle, ${tokens.ambientGlowSecondary} 0%, transparent 65%)`,
            filter: 'blur(90px)',
            pointerEvents: 'none',
          }}
        />
      )}

      {/* 背景循环底噪音乐 */}
      {props.bgm_src && (
        <Audio src={staticFile(props.bgm_src)} volume={0.06} loop />
      )}

      {/* 横屏各分镜渲染 */}
      {props.scenes.map((scene) => (
        <Sequence
          key={scene.scene_index}
          from={scene.start_frame}
          durationInFrames={scene.duration_frames}
        >
          <LandscapeSceneContent
            scene={scene}
            totalScenes={props.scenes.length}
            entityKey={props.resource_keyword || 'OpenSource'}
            resourceKeyword={props.resource_keyword}
            fps={props.fps}
            footer_callout={props.footer_callout}
          />
        </Sequence>
      ))}

      {/* 左下角合规 AIGC 水印 */}
      <div
        style={{
          position: 'absolute',
          bottom: 18,
          left: 36,
          padding: '5px 12px',
          background: 'rgba(0, 0, 0, 0.5)',
          borderRadius: 8,
          color: '#64748b',
          fontSize: 14,
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
