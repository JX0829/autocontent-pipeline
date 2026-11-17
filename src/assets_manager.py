"""素材库管理器：按 manifest 选素材，可复现、防同质化、留审计。

设计要点
--------
1. **只认素材库配置，不认具体素材**：新增素材 = 放进目录 + 写 manifest，
   不改渲染代码；新增素材库 = 加一个 library 段 + manifest。
2. **可复现**：以 run_id 的 sha256 做种子，同一 run 重渲染必得同一素材。
3. **防同质化**：读取 data/asset_history.json，最近 N 条用过的素材不再选；
   池子只剩一个可选时才允许重复（并记 warn）。
4. **审计**：返回 picked/rejected/seed 等，由执行体写 asset_binding.json。

用法
----
    from src.assets_manager import AssetManager
    am = AssetManager()
    sel = am.select(run_id="deepseek-quota-1", library="tech_story")
    sel.open   # {"id": ..., "src": "assets/tech/video/open/open_wafer_v1.mp4", ...}
    sel.bgm    # {"id": ..., "src": ..., "volume": 0.22, "duck_volume": 0.12}
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

import yaml

PROJ = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJ / "config" / "assets.yaml"
DEFAULT_HISTORY = PROJ / "data" / "asset_history.json"
HISTORY_KEEP = 20  # 保留最近 20 条成片的选取记录


class AssetError(RuntimeError):
    """素材库不可用（缺 manifest / 必需 slot 为空）。"""


@dataclass
class AssetChoice:
    id: str
    slot: str
    src: str                      # 相对 renderer/public 的路径，供 staticFile 使用
    file: str                     # 相对素材库根目录的路径
    tags: list[str] = field(default_factory=list)
    license: str = ""
    duration_s: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Selection:
    library: str
    seed: int
    open: Optional[AssetChoice]
    bg: AssetChoice
    bgm: Optional[AssetChoice]
    volume: float = 0.22
    duck_volume: float = 0.12
    rejected: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_audit(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    def to_render_props(self) -> dict[str, Any]:
        """渲染器需要的字段（扁平、只放路径与音量）。"""
        return {
            "assets": {
                "open": self.open.src if self.open else None,
                "bg": self.bg.src,
                "open_start_from": (self.open.extra.get("start_from") if self.open else 0) or 0,
            },
            "bgm_src": self.bgm.src if self.bgm else "",
            "bgm_volume": self.volume,
            "bgm_duck_volume": self.duck_volume,
        }


def _seed_of(run_id: str) -> int:
    return int(hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12], 16)


class AssetManager:
    def __init__(self, config_path: Path | str = DEFAULT_CONFIG,
                 history_path: Path | str = DEFAULT_HISTORY,
                 root: Path | str = PROJ):
        self.root = Path(root)
        self.config_path = Path(config_path)
        self.history_path = Path(history_path)
        if not self.config_path.exists():
            raise AssetError(f"素材库配置缺失: {self.config_path}")
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        self._manifests: dict[str, dict] = {}

    # ------------------------------------------------------------ 内部
    def _library(self, name: Optional[str]) -> tuple[str, dict]:
        libs = self.config.get("libraries") or {}
        name = name or self.config.get("default_library")
        if not name or name not in libs:
            raise AssetError(f"未知素材库: {name}（已注册: {list(libs)}）")
        lib = libs[name]
        if not lib.get("enabled", True):
            raise AssetError(f"素材库已停用: {name}")
        return name, lib

    def _manifest(self, name: str, lib: dict) -> dict:
        if name in self._manifests:
            return self._manifests[name]
        mpath = self.root / lib["manifest"]
        if not mpath.exists():
            raise AssetError(f"manifest 缺失: {mpath}")
        data = yaml.safe_load(mpath.read_text(encoding="utf-8")) or {}
        self._manifests[name] = data
        return data

    def _load_history(self) -> dict:
        if not self.history_path.exists():
            return {"picks": []}
        try:
            return json.loads(self.history_path.read_text(encoding="utf-8"))
        except Exception:
            return {"picks": []}

    def recent_ids(self, window: int, slot: str) -> set[str]:
        hist = self._load_history()
        ids: set[str] = set()
        for rec in hist.get("picks", [])[-window:]:
            for s, v in (rec.get("by_slot") or {}).items():
                if s == slot and v:
                    ids.add(v)
        return ids

    def _record(self, run_id: str, by_slot: dict[str, Optional[str]]) -> None:
        hist = self._load_history()
        picks = [p for p in hist.get("picks", []) if p.get("run_id") != run_id]
        picks.append({"run_id": run_id, "by_slot": by_slot})
        hist["picks"] = picks[-HISTORY_KEEP:]
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")

    def _pick_slot(self, assets: list[dict], slot: str, slot_cfg: dict,
                   rng: random.Random, recent: set[str], lib_root: str,
                   warnings: list[str], rejected: list[str]) -> Optional[AssetChoice]:
        pool = [a for a in assets if a.get("slot") == slot]
        if not pool:
            if slot_cfg.get("required"):
                raise AssetError(f"必需素材位为空: {slot}")
            warnings.append(f"素材位 {slot} 无素材，已跳过")
            return None

        # 优先用衍生版（如 _loop 无缝循环）
        prefer = slot_cfg.get("prefer_derived")
        if prefer:
            derived = [a for a in pool if prefer in str(a.get("id", ""))]
            if derived:
                pool = derived

        fresh = [a for a in pool if a["id"] not in recent]
        if not fresh:
            fresh = pool
            warnings.append(
                f"素材位 {slot} 池子已用尽（最近用过的 {len(recent)} 个覆盖全池），本轮允许重复")
        rejected.extend(a["id"] for a in pool if a not in fresh)

        chosen = rng.choice(sorted(fresh, key=lambda a: a["id"]))
        rel_root = str(Path(lib_root).relative_to("renderer/public")).replace("\\", "/")
        return AssetChoice(
            id=chosen["id"], slot=slot,
            src=f"{rel_root}/{chosen['file']}".replace("\\", "/"),
            file=chosen["file"],
            tags=chosen.get("tags") or [],
            license=chosen.get("license", ""),
            duration_s=float(chosen.get("duration_s") or 0),
            extra={"start_from": int(chosen.get("start_from") or 0)},
        )

    # ------------------------------------------------------------ 对外
    def select(self, run_id: str, library: Optional[str] = None) -> Selection:
        name, lib = self._library(library)
        lib_root = lib["root"]
        manifest = self._manifest(name, lib)
        assets = manifest.get("assets") or []
        slots = (lib.get("slots") or {})
        rng = random.Random(_seed_of(run_id))
        warnings: list[str] = []
        rejected: list[str] = []

        picked: dict[str, Optional[AssetChoice]] = {}
        for slot in ("open", "bg", "bgm"):
            cfg = slots.get(slot) or {}
            window = int(cfg.get("no_repeat_window", 0))
            pool_size = len([a for a in assets if a.get("slot") == slot])
            if window and pool_size:
                # 永远至少留一个新鲜素材可选，避免池子小的时候每轮都告警
                window = min(window, max(0, pool_size - 1))
            recent = self.recent_ids(window, slot) if window else set()
            picked[slot] = self._pick_slot(assets, slot, cfg, rng, recent, lib_root, warnings, rejected)

        if picked["bg"] is None:
            raise AssetError("素材库不可用：正文背景 bg 必须有素材")

        bgm_cfg = slots.get("bgm") or {}
        sel = Selection(
            library=name, seed=_seed_of(run_id),
            open=picked["open"], bg=picked["bg"], bgm=picked["bgm"],
            volume=float(bgm_cfg.get("volume", 0.22)),
            duck_volume=float(bgm_cfg.get("duck_volume", 0.12)),
            rejected=sorted(set(rejected)), warnings=warnings,
        )
        self._record(run_id, {s: (c.id if c else None) for s, c in picked.items()})
        return sel
