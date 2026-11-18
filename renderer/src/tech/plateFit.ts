/**
 * 底板大字（plate_bigtext）的**纯计算层** · SPEC-20261116 R4
 *
 * 这里不引 react / remotion / DOM，只做三件事：
 *   1) 字号降档 + 折行能不能装下（`fitPlateText`）
 *   2) 文本宽度的 em 估算（`textEm`，CJK 1em、ASCII 0.56em）
 *   3) 对比度校验（`contrastRatio`，WCAG 相对亮度）
 *
 * 为什么要抽成纯函数：R4/R5 是「装不下就降级、对比度不够就回退」的判定，
 * 这类逻辑埋在 TSX 里只能靠渲染成片肉眼看，改一次回归一次；抽出来就能用
 * `node --test` / 普通断言脚本逐档跑，且**与渲染结果无关**（不依赖 remotion 运行时）。
 */

export type Aspect = 'portrait' | 'landscape';

export interface PlateSpec {
  /** 正文基准字号（px） */
  baseFontSize: number;
  /** 逐档降字号的下限（px） */
  minFontSize: number;
  /** 每屏最多行数 */
  maxLines: number;
  /** 每行最多字数（硬上限，先于 em 预算生效） */
  maxCharsPerLine: number;
  /** 底板设计基准宽度（px） */
  maxPlateWidth: number;
  /** 底板**可扩展**上限（px）：§2.1 的 880 + 2 行 + 最小 88px 只能容 ~16 字，
      与 §5 A3「20 字也要装下」矛盾；竖屏放宽到 1024（1080 - 28*2），横屏维持 1500（A2 有硬上限）。 */
  maxPlateWidthHard: number;
  /** 底板内边距：上下 / 左右（px） */
  padY: number;
  padX: number;
  /** 底板圆角（px） */
  radius: number;
  /** 底板距画面底部（px），要留出平台 UI 遮挡区 */
  bottom: number;
  /** 底板不透明度下限 */
  opacity: number;
  lineHeight: number;
  /** 数字屏（stat_chart）大数字字号 */
  numberFontSize: number;
}

/** SPEC §2.1 尺寸表：竖横两套，禁止共用一套像素 */
export const PLATE_SPECS: Record<Aspect, PlateSpec> = {
  portrait: {
    baseFontSize: 104,
    minFontSize: 88,
    maxLines: 2,
    maxCharsPerLine: 14,
    maxPlateWidth: 880,
    maxPlateWidthHard: 1024,
    padY: 56,
    padX: 72,
    radius: 32,
    bottom: 320,
    opacity: 0.92,
    lineHeight: 1.15,
    numberFontSize: 180,
  },
  landscape: {
    baseFontSize: 88,
    minFontSize: 76,
    maxLines: 2,
    maxCharsPerLine: 18,
    maxPlateWidth: 1500,
    maxPlateWidthHard: 1500,
    padY: 44,
    padX: 60,
    radius: 28,
    bottom: 200,
    opacity: 0.92,
    lineHeight: 1.15,
    numberFontSize: 148,
  },
};

/** 单字宽度（em）：CJK/全角 1.0，ASCII 窄字 0.56，空格 0.30 */
export function charEm(ch: string): number {
  const code = ch.codePointAt(0) ?? 0;
  if (ch === ' ') return 0.3;
  // CJK 统一表意文字 / 全角标点 / 假名 / 韩文
  if (
    (code >= 0x1100 && code <= 0x11ff) ||
    (code >= 0x2e80 && code <= 0xa4cf) ||
    (code >= 0xac00 && code <= 0xd7a3) ||
    (code >= 0xf900 && code <= 0xfaff) ||
    (code >= 0xfe30 && code <= 0xfe4f) ||
    (code >= 0xff00 && code <= 0xff60) ||
    (code >= 0xffe0 && code <= 0xffe6)
  ) {
    return 1;
  }
  if (/[A-Z0-9]/.test(ch)) return 0.62;
  if (/[a-z]/.test(ch)) return 0.54;
  return 0.32; // 标点 / 括号等
}

/** 字符串宽度（em） */
export function textEm(text: string): number {
  let sum = 0;
  for (const ch of Array.from(text || '')) sum += charEm(ch);
  return sum;
}

/** 贪心折行：按 em 预算切，尽量在标点/空格后断 */
export function wrapByEm(text: string, perLineEm: number, maxCharsPerLine: number): string[] {
  const chars = Array.from(text || '');
  const lines: string[] = [];
  let cur: string[] = [];
  let curEm = 0;
  const softBreaks = new Set(['，', ',', '。', '.', '、', ' ', '；', ';', '：', ':', '！', '!', '？', '?']);
  for (const ch of chars) {
    const w = charEm(ch);
    if (cur.length > 0 && (curEm + w > perLineEm || cur.length + 1 > maxCharsPerLine)) {
      lines.push(cur.join(''));
      cur = [];
      curEm = 0;
    }
    cur.push(ch);
    curEm += w;
    if (softBreaks.has(ch) && cur.length > 0) {
      lines.push(cur.join(''));
      cur = [];
      curEm = 0;
    }
  }
  if (cur.length) lines.push(cur.join(''));
  // 吞掉空行
  return lines.filter((l) => l.length > 0);
}

export interface FitResult {
  ok: boolean;
  aspect: Aspect;
  fontSize: number;
  lines: string[];
  /** 实际最宽一行的 em，便于上层算底板宽度 */
  maxLineEm: number;
  /** 命中的降档序号（0 = 基准字号） */
  step: number;
  reason?: string;
}

/** 逐档降字号找「装得下」的那一档；都不行就报 ok=false（上层回退现样式） */
export function fitPlateText(text: string, aspect: Aspect = 'portrait'): FitResult {
  const spec = PLATE_SPECS[aspect];
  const t = (text || '').trim();
  if (!t) {
    return { ok: false, aspect, fontSize: spec.baseFontSize, lines: [], maxLineEm: 0, step: 0, reason: 'EMPTY_TEXT' };
  }
  const avail = spec.maxPlateWidthHard - spec.padX * 2;
  let step = 0;
  for (let size = spec.baseFontSize; size >= spec.minFontSize; size -= 8, step += 1) {
    const perLineEm = avail / size;
    const lines = wrapByEm(t, perLineEm, spec.maxCharsPerLine);
    const maxLineEm = lines.reduce((m, l) => Math.max(m, textEm(l)), 0);
    if (lines.length <= spec.maxLines && maxLineEm <= perLineEm + 1e-6) {
      return { ok: true, aspect, fontSize: size, lines, maxLineEm, step };
    }
  }
  // 降到最小字号仍装不下 → 不回退截断、不加省略号，交给上层回退现样式（R4）
  return {
    ok: false,
    aspect,
    fontSize: spec.minFontSize,
    lines: [],
    maxLineEm: 0,
    step,
    reason: 'PLATE_OVERFLOW',
  };
}

/** #RRGGBB / #RGB → 相对亮度（WCAG 2.x） */
export function relativeLuminance(hex: string): number {
  const m = /^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.exec((hex || '').trim());
  if (!m) return 0;
  let h = m[1];
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  const toLin = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  const r = toLin(parseInt(h.slice(0, 2), 16));
  const g = toLin(parseInt(h.slice(2, 4), 16));
  const b = toLin(parseInt(h.slice(4, 6), 16));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** 前景/背景对比度（1~21）。R5 要求 ≥7（WCAG AAA） */
export function contrastRatio(fg: string, bg: string): number {
  const l1 = relativeLuminance(fg);
  const l2 = relativeLuminance(bg);
  const hi = Math.max(l1, l2);
  const lo = Math.min(l1, l2);
  return (hi + 0.05) / (lo + 0.05);
}

export interface ContrastVerdict {
  ok: boolean;
  ratio: number;
  fg: string;
  bg: string;
  inverted: boolean;
  reason?: string;
}

/** R5：不够 7:1 就试反色；反色也不行返回 ok=false（上层回退现样式 + PLATE_CONTRAST_FAIL） */
export function pickContrastSafe(fg: string, bg: string, altFg = '#FFFFFF', threshold = 7): ContrastVerdict {
  const r1 = contrastRatio(fg, bg);
  if (r1 >= threshold) return { ok: true, ratio: r1, fg, bg, inverted: false };
  const r2 = contrastRatio(altFg, bg);
  if (r2 >= threshold) return { ok: true, ratio: r2, fg: altFg, bg, inverted: true };
  return {
    ok: false,
    ratio: Math.max(r1, r2),
    fg,
    bg,
    inverted: false,
    reason: `PLATE_CONTRAST_FAIL: ${r1.toFixed(2)}:1 / 反色 ${r2.toFixed(2)}:1 均 < ${threshold}:1`,
  };
}
