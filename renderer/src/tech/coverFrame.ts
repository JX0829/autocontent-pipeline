/**
 * 封面版式统一常量（2026-11-18 定稿，**所有模板的封面必须复用这组值**）
 *
 * 来由：用户当天连改五轮，最后定稿一句话——
 *   「居中布局、向下偏移 32、字体小一点、水平边距最小 64、上下内容隐藏、图面干净吸引人」
 *
 * 别再往各模板里抄数字：
 *   · 抖音个人主页/作品列表把 9:16 封面按 **3:4 居中裁剪**，1080x1920 只剩 y∈[240,1680]；
 *   · 文案必须整体落在 240~1680 之间，否则副标题会被平台切掉；
 *   · 上下各 240px 由 cover_compose 填纯深色底（#05070b）+ 70px 羽化，所以别指望那块有啥画面；
 *   · 底图永不含文字（成片里的锐评/备忘录不参与封面），见 cover_compose.pick_clean_source。
 *
 * 新模板接封面：import 这里，跑 `scripts/test_cover_34_safe.py`，README 附录 A 有检查清单。
 */

/** 3:4 可见区上边界（1080x1920 画布内） */
export const COVER_SAFE_34_TOP = 240;
/** 3:4 可见区下边界（1080x1920 画布内） */
export const COVER_SAFE_34_BOTTOM = 1680;

/** 竖版水平安全边距（最小值，别再往小调） */
export const COVER_PAD_X = 64;
/** 竖版文案块整体向下偏移（视觉重心略低） */
export const COVER_OFFSET_Y = 32;

/** 主标题字号 */
export const COVER_MAIN_SIZE = 76;
/** 副标题字号 */
export const COVER_SUB_SIZE = 28;
/** 小标（分类 kicker）字号 */
export const COVER_KICKER_SIZE = 20;

/** 虚化/纯色填充带高度（= (1920 - 1440) / 2） */
export const COVER_BAND_HEIGHT = 240;
