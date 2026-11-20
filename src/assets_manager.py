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

# ── 语义档位（R2/R5，2026-11-17）────────────────────────────────────────────
# 素材侧：tags 命中即归入该档（manifest 里的 tags 是人写的，直接复用，不新增字段）
DOMAIN_TAGS: dict[str, tuple[str, ...]] = {
    "tech_process": ("晶圆", "电路", "机房", "芯片", "服务器", "主板", "制造", "工艺", "冷却", "光纤"),
    "people_event": ("办公", "会议", "城市", "人物", "访谈", "握手", "写字楼", "街道", "财经", "抽象", "图形"),
}
# 选题侧：title + tags + 首场景旁白 里出现即判该档（只读产物，不加生成负担）
DOMAIN_SIGNALS: dict[str, tuple[str, ...]] = {
    "people_event": ("董事", "CEO", "创始人", "创始", "离职", "辞任", "回归", "公司", "财报", "股价",
                     "律所", "调查", "请愿", "人事", "任命", "并购", "融资", "传闻", "公关", "政策",
                     "发布会", "合作", "诉讼", "估值", "上市"),
    "tech_process": ("晶圆", "制程", "芯片", "模型", "推理", "训练", "框架", "开源", "算力", "显存",
                     "服务器", "编译", "量化", "GPU", "CUDA", "kernel", "部署", "带宽", "缓存", "算子"),
}

#: R5：池里没有匹配档位的素材 → 回退纯排版封面，并打这个 warning
NO_MATCHING_ASSET_FALLBACK = "NO_MATCHING_ASSET_FALLBACK"
#: 语义信号不足（判不出档位）→ 同样按 R5 回退，不静默复用唯一素材
NO_DOMAIN_SIGNAL = "NO_DOMAIN_SIGNAL"


def resolve_domain(*texts: str) -> Optional[str]:
    """从选题文本判档位。返回 tech_process / people_event / None（判不出）。"""
    blob = " ".join(str(t or "") for t in texts)
    if not blob.strip():
        return None
    scores = {d: sum(1 for k in keys if k.lower() in blob.lower()) for d, keys in DOMAIN_SIGNALS.items()}
    best = max(scores, key=lambda d: scores[d])
    if scores[best] == 0:
        return None
    # 两档打平也算判不出（宁可不选，也不赌）
    if sorted(scores.values())[-2:][0] == scores[best] and len(set(scores.values())) == 1:
        return None
    return best


def asset_domain(asset: dict) -> Optional[str]:
    tags = asset.get("tags") or []
    for dom, keys in DOMAIN_TAGS.items():
        if any(k in str(t) for t in tags for k in keys):
            return dom
    return None



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
    #: 幕间转场片段（2026-11-18）：场景交界处插 0.5s 全屏覆盖，不改时间轴
    mid: Optional[AssetChoice] = None
    #: 片尾收尾片段（2026-11-18）：最后 1.2s 全屏覆盖，不改时间轴
    end: Optional[AssetChoice] = None
    volume: float = 0.22
    duck_volume: float = 0.12
    rejected: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: 封面形态（SPEC-20261117 §八）：none=不插封面海报帧，平台封面图由视频第 0 帧抽
    #: typographic=纯排版卡片（旧 R5 回退口径，默认不再走，保留代码以备回滚）
    cover_mode: str = "none"
    #: 选题语义档位（R2）
    domain: Optional[str] = None

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
                "cover_mode": self.cover_mode,
                # 2026-11-19 回滚：mid/end 素材开头是黑场，压场景交界会全黑闪断，
                # 暂不注入渲染（选材记录保留在 audit 里，方便以后做无黑场素材再开）。
                "mid": None,
                "end": None,
            },
            "bgm_src": self.bgm.src if self.bgm else "",
            "bgm_volume": self.volume,
            "bgm_duck_volume": self.duck_volume,
        }


# ── 横版衍生副本（2026-11-17，方案 B）────────────────────────────────────────
#: 命名规范（RUNBOOK 5.5.2）：<stem>_landscape.<ext>
LANDSCAPE_SUFFIX = "_landscape"
PUBLIC_DIR = PROJ / "renderer" / "public"


def landscape_variant_src(src: str, public_dir: Path | None = None) -> Optional[str]:
    """按命名规范给竖版 src 找横版衍生副本，存在才返回。

    src 是 renderer/public 相对路径（如 ``assets/tech/video/open/open_cleanroom_v1.mp4``）；
    命中返回 ``assets/tech/video/open/open_cleanroom_v1_landscape.mp4``，未命中返回 None。
    副本不入 manifest、不入库（体积大），纯按文件系统约定探测。
    """
    if not src:
        return None
    root = Path(public_dir) if public_dir else PUBLIC_DIR
    rel = Path(src)
    cand_rel = rel.with_name(f"{rel.stem}{LANDSCAPE_SUFFIX}{rel.suffix}")
    if (root / cand_rel).exists():
        return str(cand_rel).replace("\\", "/")
    return None


def with_landscape_assets(props: dict[str, Any],
                          public_dir: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """把 props 里的 open/bg 换成横版衍生副本，返回 (新 props, 报告)。

    · 有 ``_landscape`` 副本 → 换掉，横版用原生 16:9 素材，不再中心裁切竖版；
    · 没有副本 → 原样保留（回落竖版裁切，行为与改造前一致），报告里记 fallback；
    · 只替换路径，不动 open_start_from / bgm / 文案，竖版 props 不受影响（入参是深拷贝）。
    """
    import copy

    lp = copy.deepcopy(props)
    assets = lp.get("assets") or {}
    report: dict[str, Any] = {"aspect": "landscape", "used": {}, "fallback": []}
    # 2026-11-19：mid/end 也要换横版副本，否则横版会在幕间/片尾插入竖版素材，
    # 被 objectFit=cover 硬裁成 16:9（画面主体被切掉）。竖版没有副本时保留原片并记 fallback。
    for slot in ("open", "bg", "mid", "end"):
        cur = assets.get(slot)
        if not cur:
            continue
        hit = landscape_variant_src(cur, public_dir)
        if hit:
            assets[slot] = hit
            report["used"][slot] = {"from": cur, "to": hit}
        else:
            report["fallback"].append(slot)
    assets["aspect"] = "landscape"
    lp["assets"] = assets
    binding = lp.get("asset_binding") or {}
    if binding:
        binding["landscape"] = report
        lp["asset_binding"] = binding
    return lp, report


def _seed_of(run_id: str) -> int:
    return int(hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12], 16)


#: bg 首帧亮度下限（BUGS-20261120 R2b）：低于此值片头就是一块黑板。
#: 实测 bg_neural_mesh_v1 首帧 23.7 / 近黑 91%，bg_circuit_v1_loop 42.4 / 近黑 0%。
BG_MIN_FIRST_YAVG = 32.0


def _first_frame_yavg(path: Path) -> float | None:
    """素材首帧 YAVG（ffmpeg signalstats）。取不到返回 None（不因测量失败卡死选材）。"""
    try:
        from src.utils.black_check import frame_luma  # 延迟导入：渲染/测试无 ffmpeg 也能跑
        frames = frame_luma(path)
        return float(frames[0][1]) if frames else None
    except Exception:  # noqa: BLE001
        return None


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

    def recent_ids(self, window: int, slot: str, exclude_run_id: str | None = None) -> set[str]:
        """最近 window 条成片用过的该槽位素材。

        `exclude_run_id`：重选同一个 run（幂等重渲）时必须排除自己的历史记录，
        否则第一次选的素材会被自己"挤掉"，同物料重渲选到不同素材（2026-11-18 实测踩到，
        直接违反"同 run_id 可复现"）。
        """
        hist = self._load_history()
        recs = [r for r in hist.get("picks", [])
                if not (exclude_run_id and r.get("run_id") == exclude_run_id)]
        ids: set[str] = set()
        for rec in recs[-window:]:
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

        # 衍生版（如 _loop 无缝循环）语义（2026-11-18 修两次）：
        #   · 旧实现把候选池缩成「带 _loop 的素材」→ 只有 circuit 一族有 loop ⇒ 12/12 全是 circuit。
        #   · 中间实现「先随机再同族升级」→ circuit 一族有 3 条副本(v1/v1_loop/v2)，概率被放大到 44%。
        #   · 现实现：**按 family 去重**后再随机（每族只留一个代表，有衍生版就用衍生版）
        #     ⇒ 随机单位是「视觉族」而不是「文件」，抽到的族天然不重复。
        prefer = slot_cfg.get("prefer_derived")
        if prefer:
            fam_pool: dict[str, dict] = {}
            for a in pool:
                fam = str(a.get("family") or a["id"])
                cur = fam_pool.get(fam)
                if cur is None or (prefer in str(a.get("id", "")) and prefer not in str(cur.get("id", ""))):
                    fam_pool[fam] = a
            pool = list(fam_pool.values())

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
    def select(self, run_id: str, library: Optional[str] = None,
               draft: Optional[dict] = None, locked: Optional[dict] = None) -> Selection:
        """选素材。

        - `draft`：draft_script 内容，用 title/tags/首场景旁白 判语义档位（R2）；
        - `locked`：{"open": id, "bg": id, "cover": id}，物料显式指定则必须尊重（R3）；
        - 档位判不出或池里没有该档 → **R5 回退**：open=None + cover_mode=typographic，
          并打 NO_MATCHING_ASSET_FALLBACK / NO_DOMAIN_SIGNAL，不再静默复用唯一素材。
        """
        name, lib = self._library(library)
        lib_root = lib["root"]
        manifest = self._manifest(name, lib)
        assets = manifest.get("assets") or []
        slots = (lib.get("slots") or {})
        rng = random.Random(_seed_of(run_id))
        warnings: list[str] = []
        rejected: list[str] = []

        locked = locked or {}
        _d = draft or {}
        _scenes = _d.get("scenes") or []
        _narration = " ".join(str(x.get("segment_narration") or "") for x in _scenes[:2] if isinstance(x, dict))
        domain = resolve_domain(_d.get("title"), " ".join(map(str, _d.get("tags") or [])), _narration)
        cover_mode = "none"

        # 语义筛池：只对画面位（open/bg）过滤；bgm 与语义无关
        by_domain: dict[str, list[dict]] = {"open": assets, "bg": assets, "bgm": assets}
        if domain:
            matched = [a for a in assets if asset_domain(a) in (None, domain)]
            only_match = [a for a in assets if asset_domain(a) == domain]
            if only_match:
                by_domain["open"] = matched
                by_domain["bg"] = matched
            else:
                # 池里没有任何本档素材 → R5：不借用别的档位凑封面
                warnings.append(f"{NO_MATCHING_ASSET_FALLBACK}: 素材池无「{domain}」档（现有: "
                                f"{sorted({asset_domain(a) or '未标注' for a in assets})}）"
                                f" → 按全池随机（2026-11-18 放宽，不再留空）")
                by_domain["open"] = assets
                cover_mode = "none"
        else:
            warnings.append(f"{NO_DOMAIN_SIGNAL}: 选题判不出语义档位，封面回退纯排版"
                            f" → 开场素材按全池随机")
            by_domain["open"] = assets
            cover_mode = "none"

        picked: dict[str, Optional[AssetChoice]] = {}
        locked_slots: set[str] = set()
        for slot in ("open", "bg", "bgm", "mid", "end"):
            cfg = slots.get(slot) or {}
            window = int(cfg.get("no_repeat_window", 0))
            pool_size = len([a for a in assets if a.get("slot") == slot])
            if window and pool_size:
                # 永远至少留一个新鲜素材可选，避免池子小的时候每轮都告警
                window = min(window, max(0, pool_size - 1))
            recent = self.recent_ids(window, slot, exclude_run_id=run_id) if window else set()
            # R3：物料显式锁定优先（按 id 精确命中，命中不到就报错，不静默换）
            want_id = locked.get(slot)
            if want_id:
                hit = next((a for a in assets if a.get("id") == want_id), None)
                if hit is None:
                    raise AssetError(f"物料锁定的素材不存在: {slot}={want_id}（可用: "
                                     f"{sorted(a['id'] for a in assets if a.get('slot') == slot)}）")
                rel_root = str(Path(lib_root).relative_to("renderer/public")).replace("\\", "/")
                picked[slot] = AssetChoice(
                    id=hit["id"], slot=slot,
                    src=f"{rel_root}/{hit['file']}".replace("\\", "/"),
                    file=hit["file"], tags=hit.get("tags") or [],
                    license=hit.get("license", ""),
                    duration_s=float(hit.get("duration_s") or 0),
                    extra={"start_from": int(hit.get("start_from") or 0), "locked_by_material": True},
                )
                warnings.append(f"按物料锁定选用 {slot}={want_id}")
                locked_slots.add(slot)
                continue
            picked[slot] = self._pick_slot(by_domain.get(slot, assets), slot, cfg, rng, recent, lib_root,
                                           warnings, rejected)

        if picked["bg"] is None:
            raise AssetError("素材库不可用：正文背景 bg 必须有素材")

        # BUGS-20261120 R2：bg 首帧亮度会影响片头观感，但**片头亮度已由 open 素材兜底**
        # （R2a，open 首帧实测 38~58），所以 bg 这里只做审计 + 提示，不硬剔除 ——
        # 硬剔除会让整个素材族退出轮换（门禁「素材轮换 ≥80%」立刻红），得不偿失。
        # 真正必须亮的地方是「封面底图」（cover_compose 里跳过近黑 >40% 的素材）。
        _bg_y = _first_frame_yavg(Path(lib_root) / str(picked["bg"].file))
        if _bg_y is not None:
            picked["bg"].extra = dict(picked["bg"].extra or {}, first_frame_yavg=round(_bg_y, 1))
            if _bg_y < BG_MIN_FIRST_YAVG:
                warnings.append(
                    f"bg {picked['bg'].id} 首帧 YAVG={_bg_y:.1f} < {BG_MIN_FIRST_YAVG:.0f}："
                    f"片头亮度靠 open 素材兜底（open 已不再被丢弃），封面底图会自动跳过该素材")
                if "bg" in locked_slots:
                    raise AssetError(
                        f"OPENING_TOO_DARK: 物料锁定的 bg={picked['bg'].id} 首帧 YAVG={_bg_y:.1f} "
                        f"< {BG_MIN_FIRST_YAVG:.0f}（片头会整屏发黑）。请换一条亮素材，"
                        f"或解除锁定让系统自动选（实测可用的亮素材首帧 YAVG ≥ 42）")

        bgm_cfg = slots.get("bgm") or {}
        sel = Selection(
            library=name, seed=_seed_of(run_id),
            open=picked["open"], bg=picked["bg"], bgm=picked["bgm"],
            mid=picked.get("mid"), end=picked.get("end"),
            volume=float(bgm_cfg.get("volume", 0.22)),
            duck_volume=float(bgm_cfg.get("duck_volume", 0.12)),
            rejected=sorted(set(rejected)), warnings=warnings,
            cover_mode=cover_mode, domain=domain,
        )
        self._record(run_id, {s: (c.id if c else None) for s, c in picked.items()})
        return sel
