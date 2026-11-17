# 归档 · GeekShortVideo 文字卡片模板（2026-11-17）

## 状态

**已归档，未删除，可随时回滚使用。**

自 2026-11-17 起，科技类选题默认走 `tech_story` → `TechStoryVideo`（全屏 AI 视频素材）。
`GeekShortVideo` 及其 5 套视觉 preset 的代码、样式、composition 全部保留，
在 `config/templates.yaml` 里把 `theme_mapping` 改回去就立刻恢复。

## 保留了什么

| 资产 | 位置 | 说明 |
|---|---|---|
| 组件 | `renderer/src/Root.tsx` → `GeekShortVideo` | 分镜卡片 + 字幕 + 音轨，逻辑未动 |
| 5 套 preset | `renderer/src/templates/presets/*.ts` | editorial_news / cupertino_glass / neo_brutalism / notion_craft / linear_obsidian |
| 分镜组件 | `components/{TerminalCodeView,UiCardView,StatChartView,MediaShowcaseView}.tsx` | 四种 visual_type 的卡片实现 |
| 封面 | `CoverStill` composition | 竖版/横版封面渲染 |

改造期间唯一动过的地方：`SafeAreaContainer` 增加了可选 `backdrop` 属性（传入才透明，
不传行为与原来完全一致）；分镜新增可选 `creator_take` 锐评条（不传不渲染）。

## 回滚方式

### 全量回滚（30 秒）

`config/templates.yaml`：

```yaml
theme_mapping:
  breaking-news: "editorial_news"
  ai-product: "cupertino_glass"
  tech-github: "notion_craft"
```

`src/pipeline/executors.py` 的 `COMPOSITION_BY_TEMPLATE` 里 `tech_story` 仍然存在，
但没有任何 theme 会映射到它，等于停用。要彻底移除，删掉 `tech_story` 那一行即可。

### 单条对照（不影响全局）

物料 JSON 里把 `template_id` 写成旧模板名（payload 优先级高于全局映射）：

```bash
$PY - <<'EOF'
import json
m = json.load(open("materials/xxx.json", encoding="utf-8"))
m["template_id"] = "linear_obsidian"
json.dump(m, open("materials/xxx_legacy.json", "w", encoding="utf-8"), ensure_ascii=False)
EOF
$PY -m src.pipeline.runner --material materials/xxx_legacy.json --stop-before-publish
```

### 代码级回滚

`git revert <commit>`（本次改造是一个独立 commit，配置 + 代码一起回退）。

## 为什么归档

用户反馈：卡片式文字模板在视频前面，把 AI 视频背景全挡住了。
新模板把文字压到上下两端（顶部标题/关键词、底部字幕），画面中部 y 760–1500 完全裸露。
实测帧差从 1.63 提到 4.20、静止帧从 15/180 降到 0/151。
