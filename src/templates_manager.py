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

    def get_next_round_robin_template(self, state_file: str | Path = "data/round_robin_state.json") -> str:
        """从持久化状态中按顺序获取下一个轮换模板 (T1 -> T2 -> T3 -> T4 -> T5 -> T1 ...)"""
        seq = self._config.get("round_robin_sequence") or BUILTIN_TEMPLATE_IDS
        enabled = self.list_enabled_templates()
        active_seq = [tid for tid in seq if tid in enabled]
        if not active_seq:
            return "linear_obsidian"

        state_p = Path(state_file)
        state_data = {}
        if state_p.exists():
            try:
                import json
                with open(state_p, "r", encoding="utf-8") as f:
                    state_data = json.load(f)
            except Exception:
                pass

        last_idx = state_data.get("last_index", -1)
        next_idx = (last_idx + 1) % len(active_seq)
        selected_tpl = active_seq[next_idx]

        try:
            import json
            state_p.parent.mkdir(parents=True, exist_ok=True)
            with open(state_p, "w", encoding="utf-8") as f:
                json.dump({"last_index": next_idx, "last_template": selected_tpl}, f, indent=2)
        except Exception:
            pass

        return selected_tpl

    def resolve_template_id(
        self,
        theme: str,
        cli_template: Optional[str] = None,
        payload_template: Optional[str] = None,
        account_template: Optional[str] = None,
    ) -> str:
        """按优先级解析最终模板 ID:
        1. CLI 显式指定 (--template)
        2. DraftScriptPayload.template_id 字段 (非 auto)
        3. 账号配置 config/accounts.yaml 中的 default_template (非 auto)
        4. 全局配置 default_template 为 round_robin 时，按 T1-T5 轮换选择
        5. 全局 config/templates.yaml 中的 theme_mapping 按 theme 自动映射
        """
        for candidate in (cli_template, payload_template, account_template):
            if candidate and candidate != "auto" and self.is_valid_template(candidate):
                return candidate

        default_tpl = self._config.get("default_template", "round_robin")
        if default_tpl == "round_robin":
            return self.get_next_round_robin_template()

        theme_map = self._config.get("theme_mapping", DEFAULT_THEME_MAPPING)
        mapped = theme_map.get(theme)
        if mapped and self.is_valid_template(mapped):
            return mapped

        if default_tpl != "auto" and self.is_valid_template(default_tpl):
            return default_tpl

        return "linear_obsidian"

    def get_template_meta(self, template_id: str) -> dict:
        templates_cfg = self._config.get("templates", {})
        return templates_cfg.get(template_id, {})

