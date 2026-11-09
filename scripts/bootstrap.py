#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T0.4 运行时自举（Runtime Bootstrap）
=====================================
对应 spec_enhancement.md：§1.7 G-A / §4.9.1 / §5 T0.4

背景（实测）：
  `renderer/node_modules/.remotion` 与 `~/Library/Caches/remotion` 双双不存在，
  首次 `npx remotion render` 会联网下载 chrome-headless-shell（≈150MB），
  离线/内网环境直接失败。本脚本把「环境是否齐备」变成一条可判定的命令。

五层探测（对齐 §4.9.1）：
  binaries  可执行文件：ffmpeg / ffprobe / npx / tesseract（含 chi_sim 语言包）
  python    Python 解释器版本 + requirements.txt 中的包
  node      renderer/node_modules 与 remotion 版本
  browser   Remotion 浏览器（关键阻断项）
  fonts     中文字体（缺失会导致 OCR bbox 判据失效）

用法：
  python3 scripts/bootstrap.py --dry-run          # 只探测，不改环境（默认动作）
  python3 scripts/bootstrap.py --ensure           # 缺什么自动补什么（browser / node）
  python3 scripts/bootstrap.py --ensure --only browser
  python3 scripts/bootstrap.py --dry-run --json   # 机器可读

退出码：
  0   全部 PASS
  69  有缺失项（EX_UNAVAILABLE），**调用方必须拒跑，不得硬闯**
  2   参数错误
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
RENDERER = PROJ / "renderer"
REQUIREMENTS = PROJ / "requirements.txt"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MISSING = 69  # sysexits.h EX_UNAVAILABLE

# ---------------------------------------------------------------- 数据结构


@dataclass
class Check:
    layer: str
    item: str
    expect: str
    actual: str
    ok: bool
    hint: str = ""          # 缺失时的补救提示
    auto: str = ""          # 可自动执行的补救命令（空 = 需人工）
    cwd: Path | None = None
    note: str = ""          # 附加说明（如 hermetic / system-fallback）


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, c: Check) -> None:
        self.checks.append(c)

    @property
    def missing(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    @property
    def ok(self) -> bool:
        return not self.missing


# ---------------------------------------------------------------- 工具函数


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 20) -> tuple[int, str]:
    """执行命令，返回 (exit_code, 输出)。永不抛异常。"""
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=timeout,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "command not found"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"
    except Exception as exc:  # noqa: BLE001 - 探测失败本身就是结果，不吞异常语义
        return 1, f"{type(exc).__name__}: {exc}"


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(str(p)))


# ---------------------------------------------------------------- 1. binaries

BINARIES = {
    "ffmpeg": "brew install ffmpeg",
    "ffprobe": "brew install ffmpeg",
    "npx": "brew install node",
    "tesseract": "brew install tesseract tesseract-lang",
}


def probe_binaries(rep: Report) -> None:
    for name, hint in BINARIES.items():
        path = shutil.which(name)
        rep.add(Check(
            layer="binaries", item=name,
            expect="on PATH", actual=path or "NOT FOUND",
            ok=bool(path), hint=hint,
            auto="" if not path else "",  # 系统级安装一律人工确认
        ))

    # tesseract 中文语言包：landscape_guard 的 OCR 判据依赖 chi_sim
    code, out = _run(["tesseract", "--list-langs"])
    has_chi = "chi_sim" in out if code == 0 else False
    rep.add(Check(
        layer="binaries", item="tesseract/chi_sim",
        expect="chi_sim 语言包",
        actual="present" if has_chi else ("absent" if code == 0 else "tesseract 不可用"),
        ok=has_chi, hint="brew install tesseract-lang",
    ))


# ---------------------------------------------------------------- 2. python

def _req_names() -> list[str]:
    if not REQUIREMENTS.exists():
        return []
    names = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"([A-Za-z0-9_.\-]+)", line)
        if m:
            names.append(m.group(1))
    return names


def probe_python(rep: Report) -> None:
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    ok_ver = sys.version_info >= (3, 10)
    rep.add(Check(
        layer="python", item="interpreter",
        expect=">=3.10", actual=f"{ver} ({sys.executable})", ok=ok_ver,
        hint="安装 Python 3.10+ 并重建虚拟环境；注意用同一个解释器跑 runner 与本脚本",
    ))

    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover - 3.8 以下不可能进入
        metadata = None  # type: ignore[assignment]

    for pkg in _req_names():
        ver_str, ok = "NOT INSTALLED", False
        if metadata is not None:
            try:
                ver_str, ok = metadata.version(pkg), True
            except metadata.PackageNotFoundError:
                pass
            except Exception:  # noqa: BLE001
                ver_str = "ERROR"
        rep.add(Check(
            layer="python", item=pkg,
            expect="in requirements.txt",
            actual=ver_str, ok=ok,
            hint=f"pip install '{pkg}'",
            auto=f"{sys.executable} -m pip install -r {REQUIREMENTS}" if not ok else "",
            cwd=PROJ,
        ))


# ---------------------------------------------------------------- 3. node

def _remotion_declared() -> str:
    pkg = RENDERER / "package.json"
    if not pkg.exists():
        return ""
    try:
        deps = json.loads(pkg.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    for key in ("dependencies", "devDependencies"):
        v = (deps.get(key) or {}).get("remotion")
        if v:
            return str(v)
    return ""


def probe_node(rep: Report) -> None:
    node_ver = ""
    if shutil.which("node"):
        code, out = _run(["node", "-v"])
        node_ver = out.strip() if code == 0 else ""
    rep.add(Check(
        layer="node", item="node",
        expect="present", actual=node_ver or "NOT FOUND",
        ok=bool(node_ver), hint="brew install node",
    ))

    nm = RENDERER / "node_modules"
    rep.add(Check(
        layer="node", item="renderer/node_modules",
        expect="exists", actual="exists" if nm.is_dir() else "MISSING",
        ok=nm.is_dir(), hint="npm --prefix renderer install",
        auto="npm install", cwd=RENDERER,
    ))

    declared = _remotion_declared()
    installed = ""
    pkg_json = nm / "remotion" / "package.json"
    if pkg_json.exists():
        try:
            installed = json.loads(pkg_json.read_text(encoding="utf-8")).get("version", "")
        except Exception:  # noqa: BLE001
            installed = ""
    ok = bool(installed) and installed.startswith("4.")
    rep.add(Check(
        layer="node", item="remotion",
        expect=f">=4 (declared {declared or 'n/a'})",
        actual=installed or "NOT INSTALLED",
        ok=ok, hint="npm --prefix renderer install",
    ))


# ---------------------------------------------------------------- 4. browser

BROWSER_ROOTS = [RENDERER / "node_modules" / ".remotion", _expand("~/Library/Caches/remotion")]
BROWSER_PATTERNS = ("chrome-headless-shell", "chrome-headless", "headless_shell")
SYSTEM_BROWSERS = [
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
]


def _find_hermetic_browser() -> str:
    """项目内/缓存目录里的 chrome-headless-shell（自包含，离线可分发）。"""
    for root in BROWSER_ROOTS:
        if not root.exists():
            continue
        for cand in root.rglob("*"):
            if any(p in cand.name.lower() for p in BROWSER_PATTERNS) and (cand.is_file() or cand.is_dir()):
                return str(cand)
    return ""


def _find_system_browser() -> str:
    """系统已装 Chrome/Chromium/Edge —— Remotion 会直接复用，不下载。"""
    for p in SYSTEM_BROWSERS:
        if p.exists():
            return str(p)
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def probe_browser(rep: Report) -> None:
    hermetic = _find_hermetic_browser()
    system = "" if hermetic else _find_system_browser()

    if hermetic:
        actual, ok, note = hermetic, True, "hermetic（项目内，离线可跑）"
    elif system:
        actual, ok, note = system, True, "system-fallback（依赖系统浏览器，迁移需重装）"
    else:
        actual, ok, note = "MISSING（首次渲染将联网下载 ≈150MB）", False, ""

    rep.add(Check(
        layer="browser", item="chrome-headless-shell",
        expect="项目内 .remotion 或系统 Chrome/Chromium",
        actual=actual,
        ok=ok,
        hint="npx remotion browser ensure（离线环境请预置 .remotion 目录随仓库分发）",
        auto="npx remotion browser ensure", cwd=RENDERER,
        note=note,
    ))


# ---------------------------------------------------------------- 5. fonts

FONT_HINTS = ("noto sans cjk", "pingfang", "hiragino sans gb", "microsoft yahei", "source han sans")


def _font_names() -> list[str]:
    if shutil.which("fc-list"):
        code, out = _run(["fc-list", ":", "family"])
        if code == 0 and out.strip():
            return [ln.strip() for ln in out.splitlines() if ln.strip()]
    found: list[str] = []
    for d in (_expand("~/Library/Fonts"), Path("/Library/Fonts"), Path("/System/Library/Fonts")):
        if d.is_dir():
            found.extend(p.name for p in d.glob("*") if p.is_file())
    return found


def probe_fonts(rep: Report) -> None:
    names = _font_names()
    blob = " \n".join(names).lower()
    hit = next((h for h in FONT_HINTS if h in blob), "")
    rep.add(Check(
        layer="fonts", item="CJK font",
        expect="Noto Sans CJK SC / PingFang / Hiragino Sans GB",
        actual=hit or ("no CJK match" if names else "font list unavailable"),
        ok=bool(hit),
        hint="brew install --cask font-noto-sans-cjk-sc",
    ))


# ---------------------------------------------------------------- 汇总与输出

LAYER_ORDER = ("binaries", "python", "node", "browser", "fonts")
ICON = {True: "PASS", False: "FAIL"}


def _print_table(rep: Report) -> None:
    rows = [("LAYER", "ITEM", "EXPECT", "ACTUAL", "STATUS", "NOTE")]
    for c in rep.checks:
        rows.append((c.layer, c.item, c.expect, c.actual, ICON[c.ok], c.note))
    w = [max(len(r[i]) for r in rows) for i in range(6)]
    for idx, r in enumerate(rows):
        line = "  ".join(str(r[i]).ljust(w[i]) for i in range(6)).rstrip()
        print(line)
        if idx == 0:
            print("  ".join("-" * w[i] for i in range(6)))


def _ensure(rep: Report, only: set[str] | None) -> int:
    todo = [c for c in rep.missing if (not only or c.layer in only)]
    if not todo:
        print("[ensure] 无需安装，全部已就绪")
        return 0

    ran = 0
    manual: list[Check] = []
    for c in todo:
        if not c.auto:
            manual.append(c)
            continue
        print(f"[ensure] {c.layer}/{c.item} → {c.auto}")
        code, out = _run(c.auto.split(), cwd=c.cwd, timeout=1800)
        tail = "\n".join(out.strip().splitlines()[-5:])
        if code != 0:
            print(f"[ensure] 失败（exit={code}）:\n{tail}")
            manual.append(c)
            continue
        print(f"[ensure] OK: {c.item}")
        ran += 1

    if manual:
        print("\n[ensure] 以下项需人工处理：")
        for c in manual:
            print(f"  - {c.layer}/{c.item}: {c.hint}")
    return ran


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T0.4 运行时自举（五层探测 + 可选自动安装）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="只探测不改环境（默认）")
    g.add_argument("--ensure", action="store_true", help="缺失项自动安装（browser/node/py 包）")
    ap.add_argument("--only", default="", help="限定层：binaries,python,node,browser,fonts")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--quiet", action="store_true", help="仅在失败时输出")
    args = ap.parse_args(argv)

    only = {s.strip() for s in args.only.split(",") if s.strip()} or None
    if only and not only <= set(LAYER_ORDER):
        ap.error(f"--only 取值必须是 {LAYER_ORDER} 的子集")

    rep = Report()
    for probe in (probe_binaries, probe_python, probe_node, probe_browser, probe_fonts):
        probe(rep)

    if not args.json and not args.quiet:
        _print_table(rep)
    elif not args.json and args.quiet and rep.missing:
        _print_table(rep)

    if args.ensure:
        _ensure(rep, only)
        rep = Report()
        for probe in (probe_binaries, probe_python, probe_node, probe_browser, probe_fonts):
            probe(rep)
        if not args.json:
            print("\n[ensure] 复检结果：")
            _print_table(rep)

    if args.json:
        print(json.dumps({
            "ok": rep.ok,
            "missing": [f"{c.layer}/{c.item}" for c in rep.missing],
            "checks": [c.__dict__ | {"cwd": str(c.cwd) if c.cwd else None} for c in rep.checks],
        }, ensure_ascii=False, indent=2))
    else:
        total, bad = len(rep.checks), len(rep.missing)
        print(f"\nBOOTSTRAP: {total - bad}/{total} PASS")
        if rep.missing:
            print("MISSING: " + ", ".join(f"{c.layer}/{c.item}" for c in rep.missing))
            print("→ 存在缺失项，按契约拒绝运行（EXIT=69）")

    return EXIT_OK if rep.ok else EXIT_MISSING


if __name__ == "__main__":
    sys.exit(main())
