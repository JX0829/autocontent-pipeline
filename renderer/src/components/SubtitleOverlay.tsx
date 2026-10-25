import React from 'react';
import { useCurrentFrame, useVideoConfig } from 'remotion';
import { useTemplate } from '../templates/context';

export interface SubtitleItem {
  text: string;
  start_ms: number;
  end_ms: number;
}

export interface SubtitleOverlayProps {
  subtitles: SubtitleItem[];
}

export const SubtitleOverlay: React.FC<SubtitleOverlayProps> = ({ subtitles }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { tokens } = useTemplate();

  const currentMs = (frame / fps) * 1000;
  const activeSub =
    subtitles.find((s) => currentMs >= s.start_ms && currentMs <= s.end_ms) ||
    subtitles[subtitles.length - 1];

  if (!activeSub || !activeSub.text) {
    return null;
  }

  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        width: '100%',
        bottom: 40,
        zIndex: 25,
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        boxSizing: 'border-box',
        padding: '0 20px',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          background: tokens.subBg,
          color: tokens.subText,
          border: tokens.subBorder,
          boxShadow: tokens.subShadow,
          borderRadius: tokens.subRadius,
          padding: '20px 36px',
          fontSize: 38,
          fontWeight: 900,
          lineHeight: 1.35,
          textAlign: 'center',
          maxWidth: '100%',
          boxSizing: 'border-box',
        }}
      >
        <span style={{ color: tokens.subHighlight, marginRight: 10 }}>▸</span>
        {activeSub.text}
      </div>
    </div>
  );
};
