import React, { createContext, useContext } from 'react';
import { TemplatePreset } from './types';
import { resolveTemplatePreset } from './registry';
import { linearObsidianPreset } from './presets/linear_obsidian';

const TemplateContext = createContext<TemplatePreset>(linearObsidianPreset);

export interface TemplateProviderProps {
  templateId?: string;
  theme?: string;
  children: React.ReactNode;
}

export const TemplateProvider: React.FC<TemplateProviderProps> = ({
  templateId,
  theme,
  children,
}) => {
  const preset = resolveTemplatePreset(templateId, theme);
  return (
    <TemplateContext.Provider value={preset}>
      {children}
    </TemplateContext.Provider>
  );
};

export function useTemplate(): TemplatePreset {
  return useContext(TemplateContext);
}
