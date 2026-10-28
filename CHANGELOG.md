# Changelog

All notable changes to this project will be documented in this file.

## [1.0.1] - 2026-10-28

### Added
- **Xiaohongshu CDP Driver (`src/providers/xiaohongshu_cdp.py`)**: Direct integration with persistent Brave browser instance over CDP (`9222`), eliminating headless anti-bot detection and WAF error `300012`.
- **System Service (`com.user.brave-automation.plist`)**: Background launchd daemon maintaining persistent authenticated Brave browser session.
- **Dynamic Storyboard & Template Selection Engine (`src/hotspot_extractor.py`)**:
  - Embedded viral content evaluation rules learned from high-engagement notes (e.g. JetBrains, ElevenLabs, GPT-6.1).
  - High-priority scoring for ecosystem disruption, commercial tool open-source alternatives, and dramatic price/cost drops.
  - Rewriting guidance for low-engagement topics: translated pure academic/infrastructure jargon into concrete developer benefits.
  - Dynamic template mapping (`notion_craft`, `linear_obsidian`, `retro_terminal`, `cyber_blueprint`) to eliminate visual monotony.
- **Two-Hotspot Extraction Mode**: Supports generating two distinct, non-overlapping video topics per batch.

### Changed
- **Content Filtering & Safety Rules**:
  - Complete elimination of external URLs, domains, and search CTA phrases (e.g. "GitHub搜索", "暗号", "私信") from all narration, subtitles, and UI cards to prevent platform shadow-bans.
  - Comment seeding logic updated to use high-engagement analogies (e.g. coffee cup sizes, benchmark tradeoffs).
- **Target Routing**: All video previews, approvals, and alert dispatches routed exclusively to private DM (`ou_bc831d683c9b59369bc86ee1561ed226`).

### Fixed
- Fixed duplicate video generation caused by re-triggering render during approval actions in `run_feishu_worker.py`.
- Fixed comment interaction timeouts by implementing natural click activation on native input elements.

---

## [1.0.0] - 2026-10-28

### Initial Release
- End-to-end automated short video pipeline with Edge-TTS, Remotion rendering, Feishu mobile approval cards, and SQLite state tracking.
