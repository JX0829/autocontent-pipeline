import React from 'react';

interface SafeAreaContainerProps {
  children: React.ReactNode;
  showDebugBorder?: boolean;
}

export const SafeAreaContainer: React.FC<SafeAreaContainerProps> = ({
  children,
  showDebugBorder = false,
}) => {
  return (
    <div
      style={{
        position: 'absolute',
        left: 96,
        top: 214, // 原始 150 + 下移 64px = 214px，进一步避让顶部系统与刘海组件
        width: 888, // 1080 - 96 * 2 = 888，左右边距严格固化为 96px
        height: 1326, // 同步下调高度 1390 - 64 = 1326px，保证底部安全避让
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        alignItems: 'center',
        border: showDebugBorder ? '2px dashed rgba(255, 0, 0, 0.6)' : 'none',
        boxSizing: 'border-box',
        overflow: 'hidden',
        zIndex: 10,
      }}
    >
      {children}
    </div>
  );
};
