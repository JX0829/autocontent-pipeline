/**
 * plateFit 纯函数单测（SPEC-20261116 R4/R5）
 *
 * 跑法（零依赖，Node ≥22 原生剥类型）：
 *   node --experimental-strip-types renderer/src/tech/plateFit.test.ts
 * 退出码 0 = 全过；非 0 = 有断言失败（verify_all 会据此判红）。
 */
import assert from 'node:assert/strict';
import {
  PLATE_SPECS,
  charEm,
  textEm,
  wrapByEm,
  fitPlateText,
  contrastRatio,
  pickContrastSafe,
  relativeLuminance,
} from './plateFit.ts';

let pass = 0;
const fails: string[] = [];
function it(name: string, fn: () => void): void {
  try {
    fn();
    pass += 1;
    console.log(`  ✅ ${name}`);
  } catch (e) {
    fails.push(`${name}: ${(e as Error).message}`);
    console.log(`  ❌ ${name} → ${(e as Error).message}`);
  }
}

console.log('=== plateFit 纯函数单测 ===');

it('① 尺寸表：竖/横两套且不共用像素', () => {
  assert.equal(PLATE_SPECS.portrait.baseFontSize, 104);
  assert.equal(PLATE_SPECS.landscape.baseFontSize, 88);
  assert.equal(PLATE_SPECS.portrait.numberFontSize, 180);
  assert.equal(PLATE_SPECS.landscape.numberFontSize, 148);
  assert.equal(PLATE_SPECS.portrait.maxPlateWidth, 880);
  assert.equal(PLATE_SPECS.landscape.maxPlateWidth, 1500);
  assert.equal(PLATE_SPECS.portrait.bottom, 320);
  assert.equal(PLATE_SPECS.landscape.bottom, 200);
  assert.ok(PLATE_SPECS.portrait.opacity >= 0.92);
  assert.ok(PLATE_SPECS.landscape.opacity >= 0.92);
});

it('② charEm：CJK=1，ASCII 窄字 <1，空格最小', () => {
  assert.equal(charEm('四'), 1);
  assert.equal(charEm('，'), 1);
  assert.ok(charEm('A') < 0.7);
  assert.ok(charEm('a') < 0.6);
  assert.ok(charEm(' ') < charEm('a'));
});

it('③ textEm 累加且对空串为 0', () => {
  assert.equal(textEm(''), 0);
  assert.equal(textEm('四个'), 2);
  assert.ok(textEm('四个AI') > 2 && textEm('四个AI') < 4);
});

it('④ 短句不降档：8 字在竖屏基准 104px 一行装下', () => {
  const r = fitPlateText('四个旗舰一个没发', 'portrait');
  assert.equal(r.ok, true);
  assert.equal(r.fontSize, 104);
  assert.equal(r.lines.length, 1);
  assert.equal(r.step, 0);
});

it('⑤ 14 字仍不降档：104px 折 2 行（880 基准宽下 14 字一行装不下是物理事实）', () => {
  const r = fitPlateText('一二三四五六七八九十一二三四', 'portrait');
  assert.equal(r.ok, true);
  assert.equal(r.fontSize, 104);
  assert.ok(r.lines.length <= 2);
  assert.equal(r.lines.join(''), '一二三四五六七八九十一二三四');
});

it('⑥ 20 字：折成 2 行，不出现省略号/截断', () => {
  const text = '一二三四五六七八九十一二三四五六七八九十';
  const r = fitPlateText(text, 'portrait');
  assert.equal(r.ok, true);
  assert.ok(r.lines.length <= 2);
  assert.equal(r.lines.join(''), text); // 一个字都不能丢
  assert.ok(!r.lines.join('').includes('…'));
});

it('⑦ 超长（60 字）装不下 → ok=false + PLATE_OVERFLOW（上层回退现样式）', () => {
  const text = 'AI'.repeat(30);
  const r = fitPlateText(text, 'portrait');
  assert.equal(r.ok, false);
  assert.equal(r.reason, 'PLATE_OVERFLOW');
  assert.deepEqual(r.lines, []); // 不返回半截文本，避免上层「将就着用」
});

it('⑧ 横屏基准 88px、底板 ≤1500（A2）；18 字折 2 行且不丢字', () => {
  const text = '一二三四五六七八九十一二三四五六七八'; // 18 字
  const land = fitPlateText(text, 'landscape');
  assert.equal(land.ok, true);
  assert.ok(land.lines.length <= 2);
  assert.equal(land.lines.join(''), text);
  assert.ok(land.maxLineEm * land.fontSize + 120 <= 1500, '底板宽不得超过 1500');
  const port = fitPlateText(text, 'portrait');
  assert.equal(port.ok, true);
  assert.ok(port.lines.length >= 1);
  assert.ok(port.fontSize <= 104);
});

it('⑨ 纯函数确定性：同输入两次结果完全一致', () => {
  const text = '四个旗舰 一个没发 OpenAI 这轮真的急了';
  assert.deepEqual(fitPlateText(text, 'portrait'), fitPlateText(text, 'portrait'));
});

it('⑩ wrapByEm 不吞字：折行后拼回来等于原文（去空白）', () => {
  const text = 'OpenAI在智利签了备忘录，但文件不是约束性合同';
  const lines = wrapByEm(text, 8, 14);
  assert.equal(lines.join(''), text);
});

it('⑪ 空文本 → EMPTY_TEXT（不渲染空底板）', () => {
  assert.equal(fitPlateText('   ', 'portrait').reason, 'EMPTY_TEXT');
});

it('⑫ 对比度：白字配深底 ≥ 7:1（AAA）', () => {
  const r = contrastRatio('#FFFFFF', '#0A0E14');
  assert.ok(r >= 7, `实际 ${r.toFixed(2)}`);
});

it('⑬ 对比度对称且等同色为 1:1', () => {
  assert.equal(contrastRatio('#FFFFFF', '#000000').toFixed(2), contrastRatio('#000000', '#FFFFFF').toFixed(2));
  assert.equal(contrastRatio('#123456', '#123456').toFixed(2), '1.00');
});

it('⑭ 低对比前景 → 反色成白字后达标', () => {
  const v0 = pickContrastSafe('#141414', '#0A0E14', '#FFFFFF', 7);
  assert.equal(v0.ok, true);
  assert.equal(v0.inverted, true); // 深字配深底本身不达标，反色后过
  const v2 = pickContrastSafe('#111111', '#0A0E14', '#FFFFFF', 7);
  assert.equal(v2.ok, true);
  assert.equal(v2.inverted, true);
  assert.ok(v2.ratio >= 7);
});

it('⑮ 反色仍不达标 → ok=false + PLATE_CONTRAST_FAIL（上层回退）', () => {
  const v = pickContrastSafe('#8A8A8A', '#7A7A7A', '#9A9A9A', 7);
  assert.equal(v.ok, false);
  assert.ok(String(v.reason).includes('PLATE_CONTRAST_FAIL'));
});

it('⑯ relativeLuminance 单调：黑 < 灰 < 白', () => {
  assert.ok(relativeLuminance('#000000') < relativeLuminance('#808080'));
  assert.ok(relativeLuminance('#808080') < relativeLuminance('#FFFFFF'));
});

console.log(`\nPLATE FIT 单测 ${pass} PASS / ${fails.length} FAIL`);
if (fails.length) {
  for (const f of fails) console.log(`  - ${f}`);
  process.exit(1);
}
