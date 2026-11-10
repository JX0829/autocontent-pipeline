import React from 'react';
import { AlignedRenderPayload } from '../types';
import { useTemplate } from '../templates/context';

export const BilibiliLandscapeCover: React.FC<AlignedRenderPayload> = (props) => {
  const { tokens, layout } = useTemplate();

  return (
    <div
      style={{
        width: 1920,
        height: 1080,
        background: tokens.canvasBg || '#090d16',
        color: tokens.textPrimary || '#f8fafc',
        display: 'flex',
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '80px 100px',
        boxSizing: 'border-box',
        position: 'relative',
        overflow: 'hidden',
        fontFamily: layout.useSerifHeadline ? 'Georgia, serif' : 'system-ui, sans-serif',
      }}
    >
      {/* 极客环境光晕 */}
      <div
        style={{
          position: 'absolute',
          width: 900,
          height: 900,
          left: -150,
          top: -200,
          borderRadius: '50%',
          background: `radial-gradient(circle, ${tokens.ambientGlowPrimary || 'rgba(56, 189, 248, 0.25)'} 0%, transparent 65%)`,
          filter: 'blur(90px)',
          pointerEvents: 'none',
        }}
      />
      <div
        style={{
          position: 'absolute',
          width: 700,
          height: 700,
          right: -100,
          bottom: -150,
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(168, 85, 247, 0.2) 0%, transparent 65%)',
          filter: 'blur(80px)',
          pointerEvents: 'none',
        }}
      />

      {/* ──────────────── 左侧核心文案区（占 58% 宽度） ──────────────── */}
      <div
        style={{
          width: '58%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          alignItems: 'flex-start',
          zIndex: 10,
        }}
      >
        {/* 顶部热门爆款标签徽章 */}
        <div
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 12,
            padding: '12px 28px',
            background: tokens.badgeBg || 'rgba(56, 189, 248, 0.15)',
            color: tokens.badgeText || '#38bdf8',
            border: tokens.badgeBorder || '1px solid rgba(56, 189, 248, 0.3)',
            borderRadius: layout.useSerifHeadline ? 8 : 16,
            fontSize: 26,
            fontWeight: 800,
            letterSpacing: 2,
            marginBottom: 36,
            boxShadow: tokens.cardShadow,
          }}
        >
          <span>🔥</span>
          <span>{props.cover_sub_text || props.resource_keyword || ''}</span>
        </div>

        {/* 封面超大痛点主标题（B站吸睛排版，左对齐，单行不拥挤） */}
        <div
          style={{
            fontSize: 78,
            fontWeight: 950,
            color: tokens.textPrimary || '#ffffff',
            lineHeight: 1.2,
            letterSpacing: 1.5,
            marginBottom: 36,
            textShadow: '0 8px 30px rgba(0, 0, 0, 0.7)',
          }}
        >
          {props.cover_main_text || props.title}
        </div>

        {/* 悬念补充次标题 */}
        <div
          style={{
            fontSize: 34,
            fontWeight: 600,
            color: tokens.textSecondary || '#94a3b8',
            lineHeight: 1.4,
            marginBottom: 44,
            maxWidth: 900,
          }}
        >
          {props.resource_keyword || ''}
        </div>

        {/* 底部醒目引导：置顶与源码提示 */}
        <div
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 14,
            padding: '16px 36px',
            background: 'rgba(255, 255, 255, 0.05)',
            border: '1px solid rgba(255, 255, 255, 0.12)',
            borderRadius: 30,
            backdropFilter: 'blur(10px)',
          }}
        >
          <span style={{ fontSize: 26 }}>📌</span>
          <span
            style={{
              color: tokens.accentPrimary || '#38bdf8',
              fontSize: 24,
              fontWeight: 800,
              letterSpacing: 1,
            }}
          >
            {props.footer_callout || '💡 源码直链与提示词见下方简介与置顶评论'}
          </span>
        </div>
      </div>

      {/* ──────────────── 右侧分镜看点看板（占 38% 宽度，全部取自真实分镜，禁止硬编码） ──────────────── */}
      <div
        style={{
          width: '38%',
          display: 'flex',
          flexDirection: 'column',
          gap: 24,
          zIndex: 10,
        }}
      >
        {[
          { label: '◈ 核心看点', color: '#38bdf8', border: 'rgba(56, 189, 248, 0.25)' },
          { label: '◈ 关键机制', color: '#c084fc', border: 'rgba(168, 85, 247, 0.25)' },
          { label: '◈ 实操提醒', color: '#f8fafc', border: 'rgba(255, 255, 255, 0.15)' },
        ].map((card, i) => {
          // 取哪几屏由 pipeline.yaml 的 cover.card_source 声明（默认 [0,1,3]），组件不写死魔数
          const source = props.cover_card_source ?? [0, 1, 3];
          const pick = source[i] ?? i;
          const scene = props.scenes?.[pick] ?? props.scenes?.[i];
          const text = scene?.highlight_text ?? '';
          if (!text) {
            // 不静默跳过：渲染占位卡并告警，"分镜缺 highlight_text"必须在成片上看得见
            console.warn(`[cover] scene_${pick} highlight_text 缺失，渲染占位卡`);
          }
          return (
            <div
              key={i}
              style={{
                background: 'rgba(15, 23, 42, 0.85)',
                border: `1px solid ${card.border}`,
                borderRadius: 24,
                padding: '26px 32px',
                boxShadow: '0 20px 40px rgba(0, 0, 0, 0.5)',
                backdropFilter: 'blur(16px)',
                opacity: text ? 1 : 0.35,
              }}
            >
              <div style={{ color: '#94a3b8', fontSize: 22, fontWeight: 600, marginBottom: 8 }}>
                {card.label}
              </div>
              <div style={{ color: card.color, fontSize: 36, fontWeight: 900, lineHeight: 1.25 }}>
                {text || '—'}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
