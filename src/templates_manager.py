"""可插拔视觉模板管理与路由解析器 (Python 主控层)."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Literal, Optional
import yaml

TemplateIdType = Literal[
    "auto",
    "editorial_news",
    "cupertino_glass",
    "neo_brutalism",
    "notion_craft",
    "linear_obsidian",
]

BUILTIN_TEMPLATE_IDS: List[str] = [
    "editorial_news",
    "cupertino_glass",
    "neo_brutalism",
    "notion_craft",
    "linear_obsidian",
]

DEFAULT_THEME_MAPPING: Dict[str, str] = {
    "breaking-news": "editorial_news",
    "ai-product": "cupertino_glass",
    "tech-github": "notion_craft",
}


class TemplateManager:
    """负责加载 config/templates.yaml 并按优先级决策最终注入 Remotion 的 template_id."""

    def __init__(self, config_path: str | Path = "config/templates.yaml") -> None:
        self.config_path = Path(config_path)
        self._config = self._load_config()

    def _load_config(self) -> dict:
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {
            "default_template": "auto",
            "theme_mapping": DEFAULT_THEME_MAPPING,
            "templates": {tid: {"name": tid, "enabled": True} for tid in BUILTIN_TEMPLATE_IDS},
        }

    def list_enabled_templates(self) -> List[str]:
        templates_cfg = self._config.get("templates", {})
        enabled = [
            tid for tid, meta in templates_cfg.items() if meta.get("enabled", True)
        ]
        return enabled or BUILTIN_TEMPLATE_IDS

    def is_valid_template(self, template_id: str) -> bool:
        if template_id == "auto":
            return True
        return template_id in self.list_enabled_templates()

    def resolve_template_id(
        self,
        theme: str,
        cli_template: Optional[str] = None,
        payload_template: Optional[str] = None,
        account_template: Optional[str] = None,
    ) -> str:
        """按优先级解析最终模板 ID:
        1. CLI 显式指定 (--template)
        2. DraftScriptPayload.template_id 字段
        3. 账号配置 config/accounts.yaml 中的 default_template
        4. 全局 config/templates.yaml 中的 theme_mapping 按 theme 自动映射
        """
        for candidate in (cli_template, payload_template, account_template):
            if candidate and candidate != "auto" and self.is_valid_template(candidate):
                return candidate

        theme_map = self._config.get("theme_mapping", DEFAULT_THEME_MAPPING)
        mapped = theme_map.get(theme)
        if mapped and self.is_valid_template(mapped):
            return mapped

        default_tpl = self._config.get("default_template", "linear_obsidian")
        if default_tpl != "auto" and self.is_valid_template(default_tpl):
            return default_tpl

        return "linear_obsidian"

    def get_template_meta(self, template_id: str) -> dict:
        templates_cfg = self._config.get("templates", {})
        return templates_cfg.get(template_id, {})

