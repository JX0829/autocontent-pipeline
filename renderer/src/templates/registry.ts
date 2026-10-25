import { TemplateId, TemplatePreset } from './types';
import { editorialNewsPreset } from './presets/editorial_news';
import { cupertinoGlassPreset } from './presets/cupertino_glass';
import { neoBrutalismPreset } from './presets/neo_brutalism';
import { notionCraftPreset } from './presets/notion_craft';
import { linearObsidianPreset } from './presets/linear_obsidian';

// 内置 5 套默认模板注册表
const TEMPLATE_REGISTRY: Record<string, TemplatePreset> = {
  [editorialNewsPreset.id]: editorialNewsPreset,
  [cupertinoGlassPreset.id]: cupertinoGlassPreset,
  [neoBrutalismPreset.id]: neoBrutalismPreset,
  [notionCraftPreset.id]: notionCraftPreset,
  [linearObsidianPreset.id]: linearObsidianPreset,
};

// 默认按 theme 的路由映射表
const DEFAULT_THEME_TO_TEMPLATE: Record<string, TemplateId> = {
  'breaking-news': 'editorial_news',
  'ai-product': 'cupertino_glass',
  'tech-github': 'notion_craft',
};

/**
 * 动态扩展接口：允许外部插件或新增 preset 文件一行代码注册新模板
 */
export function registerTemplate(preset: TemplatePreset): void {
  TEMPLATE_REGISTRY[preset.id] = preset;
}

/**
 * 获取所有已注册的模板列表
 */
export function listRegisteredTemplates(): TemplatePreset[] {
  return Object.values(TEMPLATE_REGISTRY);
}

/**
 * 根据 templateId 与 payload.theme 解析最终生效的 TemplatePreset
 */
export function resolveTemplatePreset(
  templateId?: string,
  payloadTheme?: string
): TemplatePreset {
  if (templateId && templateId !== 'auto' && TEMPLATE_REGISTRY[templateId]) {
    return TEMPLATE_REGISTRY[templateId];
  }
  if (payloadTheme && DEFAULT_THEME_TO_TEMPLATE[payloadTheme]) {
    const mappedId = DEFAULT_THEME_TO_TEMPLATE[payloadTheme];
    if (TEMPLATE_REGISTRY[mappedId]) {
      return TEMPLATE_REGISTRY[mappedId];
    }
  }
  return linearObsidianPreset;
}
