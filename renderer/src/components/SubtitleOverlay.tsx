import React from 'react';
import { useCurrentFrame } from 'remotion';
import { SubtitleItem } from '../types';

interface SubtitleOverlayProps {
  subtitles: SubtitleItem[];
  fps: number;
}

export const SubtitleOverlay: React.FC<SubtitleOverlayProps> = ({
  subtitles = [],
  fps,
}) => {
  const frame = useCurrentFrame();
  const currentMs = (frame / fps) * 1000;

  // 匹配当前时间戳命中的字幕分句
  const currentSub = subtitles.find(
    (s) => currentMs >= s.start_ms && currentMs <= s.end_ms
  );

  if (!currentSub) {
    return null;
  }

  return (
    <div
      style={{
        width: '100%',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        padding: 0,
        boxSizing: 'border-box',
        zIndex: 50,
      }}
    >
      <div
        style={{
          width: '100%',
          background: 'rgba(15, 23, 42, 0.88)',
          border: '1.5px solid rgba(56, 189, 248, 0.5)',
          borderRadius: 20,
          padding: '18px 24px',
          boxShadow: '0 10px 30px rgba(0, 0, 0, 0.7), 0 0 20px rgba(56, 189, 248, 0.25)',
          backdropFilter: 'blur(16px)',
          textAlign: 'center',
          boxSizing: 'border-box',
        }}
      >
        <span
          style={{
            fontSize: 34,
            fontWeight: 800,
            color: '#ffffff',
            lineHeight: 1.35,
            letterSpacing: 1.5,
            textShadow: '0 2px 10px rgba(0, 0, 0, 0.9), 0 0 15px rgba(56, 189, 248, 0.6)',
            display: 'inline-block',
          }}
        >
          {currentSub.text}
        </span>
      </div>
    </div>
  );
};
