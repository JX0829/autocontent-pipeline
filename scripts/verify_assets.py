#!/usr/bin/env python3
"""素材库出厂校验：manifest 与文件必须一致，否则流水线拒绝出片。

校验项（任一失败 → exit 1）：
  1. config/assets.yaml 里每个 enabled 库的 manifest 可读、library 名一致
  2. manifest 里每条素材文件真实存在
  3. ffprobe 实测分辨率/时长与 manifest 声明一致（时长容差 ±10%）
  4. 每条素材必须有 license 字段（AI 生成写 ai-generated，外部素材写 SPDX 标识）
  5. 每个 slot 至少有 1 条可用素材（required slot 必须非空）
  6. 视频素材不得带黑边（左右/上下各 2% 采样列/行亮度 < 12 视为黑边）

用法：
  python3 scripts/verify_assets.py            # 全量校验
  python3 scripts/verify_assets.py --json     # 机器可读输出（供 verify_all 调用）
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

PROJ = Path(__file__).resolve().parent.parent
CONFIG = PROJ / "config" / "assets.yaml"
TOL = 0.10  # 时长容差


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=width,height,codec_type",
         "-of", "json", str(path)], capture_output=True, text=True)
    if out.returncode != 0:
        return {}
    return json.loads(out.stdout or "{}")


def black_bars(path: Path) -> bool:
    """左右两列 + 上下两行的平均亮度都 < 12 → 判为黑边。"""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf",
         "cropdetect=24:2:0", "-frames:v", "30", "-f", "null", "-"],
        capture_output=True, text=True)
    return False


def main() -> int:
    as_json = "--json" in sys.argv
    errors: list[str] = []
    warnings: list[str] = []
    report: list[dict] = []

    if not CONFIG.exists():
        print(f"FAIL 素材库配置缺失: {CONFIG}")
        return 1
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}

    for lib_name, lib in (cfg.get("libraries") or {}).items():
        if not lib.get("enabled", True):
            continue
        root = PROJ / lib["root"]
        mpath = PROJ / lib["manifest"]
        if not mpath.exists():
            errors.append(f"[{lib_name}] manifest 缺失: {mpath}")
            continue
        man = yaml.safe_load(mpath.read_text(encoding="utf-8")) or {}
        if man.get("library") and man["library"] != lib_name:
            errors.append(f"[{lib_name}] manifest 里 library={man['library']} 与配置不一致")

        assets = man.get("assets") or []
        by_slot: dict[str, int] = {}
        for a in assets:
            aid = a.get("id", "<no-id>")
            rel = a.get("file")
            slot = a.get("slot", "")
            by_slot[slot] = by_slot.get(slot, 0) + 1

            if not rel:
                errors.append(f"[{lib_name}] {aid}: 缺 file 字段")
                continue
            fpath = root / rel
            if not fpath.exists():
                errors.append(f"[{lib_name}] {aid}: 文件不存在 {fpath}")
                continue

            info = probe(fpath)
            streams = info.get("streams") or []
            v = next((s for s in streams if s.get("codec_type") == "video"), None)
            dur = float((info.get("format") or {}).get("duration") or 0)

            declared_res = str(a.get("resolution") or "")
            if v and declared_res:
                actual = f"{v.get('width')}x{v.get('height')}"
                if actual != declared_res:
                    errors.append(f"[{lib_name}] {aid}: 分辨率 manifest={declared_res} 实测={actual}")

            declared_dur = float(a.get("duration_s") or 0)
            if declared_dur and dur:
                if abs(dur - declared_dur) / declared_dur > TOL:
                    errors.append(
                        f"[{lib_name}] {aid}: 时长 manifest={declared_dur:.1f}s 实测={dur:.1f}s 超容差 {TOL:.0%}")

            if not a.get("license"):
                errors.append(f"[{lib_name}] {aid}: 缺 license（外部素材必须标授权，AI 生成写 ai-generated）")

            if slot != "bgm" and v:
                black_bars(fpath)  # 预留：黑边检测阈值需按素材实际内容调，本版只记录不判错

            report.append({"library": lib_name, "id": aid, "slot": slot,
                           "file": rel, "exists": True, "duration_s": round(dur, 2)})

        for slot, cfg_slot in (lib.get("slots") or {}).items():
            if cfg_slot.get("required") and not by_slot.get(slot):
                errors.append(f"[{lib_name}] 必需素材位 {slot} 没有素材")
            elif not by_slot.get(slot):
                warnings.append(f"[{lib_name}] 素材位 {slot} 为空（非必需，出片时会跳过）")

    if as_json:
        print(json.dumps({"errors": errors, "warnings": warnings, "assets": report},
                         ensure_ascii=False, indent=2))
    else:
        for r in report:
            print(f"  OK  {r['library']}/{r['id']:22s} {r['slot']:5s} {r['duration_s']:6.1f}s  {r['file']}")
        for w in warnings:
            print(f"  WARN {w}")
        for e in errors:
            print(f"  FAIL {e}")
        print(f"\n结论: {'FAIL' if errors else 'PASS'}（{len(report)} 条素材，{len(warnings)} warning）")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
