#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务契约回归测试（T4.1 / T4.2 / T4.5 / T4.7）

把独立复核时手工跑过的用例固化下来，防止这些"主路径踩不到、外部调用才中招"的
缺陷再次回归。特点：

  · 秒级完成：不渲染、不联网、不合成音频（真实渲染仍由 GATE-3 覆盖）
  · 期望值尽量从配置推导，不在测试里重新写死业务字面量
  · 依赖真实 run 目录的用例，没有产物时标 SKIP 而不是 FAIL

用法：
    python3 scripts/test_task_contracts.py          # 全部
    python3 scripts/test_task_contracts.py -v       # 打印每条明细
退出码：0 全过（含 SKIP）；1 有 FAIL
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

VERBOSE = "-v" in sys.argv
PASS: list[str] = []
FAIL: list[str] = []
SKIP: list[str] = []


def ok(name: str, detail: str = "") -> None:
    PASS.append(name)
    print(f"  ✅ {name}" + (f"  {detail}" if VERBOSE and detail else ""))


def bad(name: str, detail: str) -> None:
    FAIL.append(name)
    print(f"  ❌ {name}  {detail}")


def skip(name: str, why: str) -> None:
    SKIP.append(name)
    print(f"  ⏭️  {name}  SKIP（{why}）")


def expect_raises(name: str, fn, exc_types, hint: str = "") -> None:
    try:
        fn()
    except exc_types as e:  # noqa: BLE001 — 断言型测试，就是要捕获
        ok(name, f"{type(e).__name__}: {str(e)[:60]}")
    except Exception as e:  # noqa: BLE001
        bad(name, f"错误类型不对: {type(e).__name__}: {e}")
    else:
        bad(name, f"未拦截{hint}")


def scan_literals(patterns: list[str], roots=("src", "scripts")) -> list[str]:
    """扫源码里的字面量，排除归档/缓存/已废弃入口。"""
    skip_parts = {"_deprecated", "__pycache__", ".archive"}
    # 排除已废弃入口，以及本文件自身（检测器里必然写着待检测的模式串）
    skip_files = {"render_landscape_bilibili.py", "test_task_contracts.py"}
    hits: list[str] = []
    rx = re.compile("|".join(patterns))
    for root in roots:
        for f in Path(root).rglob("*.py"):
            if skip_parts & set(f.parts) or f.name in skip_files:
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if rx.search(line):
                    hits.append(f"{f}:{i}: {line.strip()[:80]}")
    return hits


# ═══════════════════════════════ T4.1 run_root 隔离
def test_t41() -> None:
    print("\n[T4.1] run_root 隔离 + 产物命名")
    from src.pipeline.executors import ExecutorError, _assert_has_audio

    tmp = Path(tempfile.mkdtemp())
    silent = tmp / "silent.mp4"
    withaudio = tmp / "withaudio.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=8:duration=1",
                    "-an", str(silent), "-loglevel", "error"], check=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=8:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(withaudio),
                    "-loglevel", "error"], check=True)

    expect_raises("无声片被拦截（str 入参）", lambda: _assert_has_audio(str(silent)),
                  ExecutorError, "了无声片")
    expect_raises("无声片被拦截（Path 入参）", lambda: _assert_has_audio(silent),
                  ExecutorError, "了无声片")
    try:
        _assert_has_audio(withaudio)
        ok("有声成片放行")
    except Exception as e:  # noqa: BLE001
        bad("有声成片放行", f"误杀: {e}")

    # 公共 tasks 目录不得再被写入
    tasks = PROJ / "renderer" / "public" / "tasks"
    subdirs = [d for d in tasks.iterdir() if d.is_dir()] if tasks.exists() else []
    if subdirs:
        bad("公共 tasks 目录为空", f"残留 {len(subdirs)} 个目录（应走 runs/<run_id>）")
    else:
        ok("公共 tasks 目录为空")

    # 真实 run 产物（有则验，无则 SKIP）
    runs = sorted((PROJ / "output").glob("*/*/props/aligned_render.json"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not runs:
        skip("真实 run 的 props 隔离", "没有历史产物")
        skip("runs/<run_id> 软链指向 props", "没有历史产物")
        return
    props_json = runs[0]
    run_root = props_json.parent.parent
    rid = run_root.name
    data = json.loads(props_json.read_text(encoding="utf-8"))
    ok("真实 run 的 props 隔离", f"{rid}/props/aligned_render.json")
    audio = (data.get("scenes") or [{}])[0].get("audio_src", "")
    if audio.startswith(f"runs/{rid}/"):
        ok("audio_src 走 runs/<run_id>/", audio)
    else:
        bad("audio_src 走 runs/<run_id>/", f"实际 {audio!r}")
    if data.get("bgm_src") == "sfx/bgm_loop.wav":
        ok("bgm_src 仍指向 public 根")
    else:
        bad("bgm_src 仍指向 public 根", f"实际 {data.get('bgm_src')!r}")

    link = PROJ / "renderer" / "public" / "runs" / rid
    if link.is_symlink() and link.resolve() == (run_root / "props").resolve():
        ok("runs/<run_id> 软链指向 props", str(link.name))
    else:
        bad("runs/<run_id> 软链指向 props", f"存在={link.exists()} 是软链={link.is_symlink()}")

    # 产物命名统一
    yml = (PROJ / "pipeline.yaml").read_text(encoding="utf-8")
    if "video_16_9.mp4" in yml:
        ok("pipeline.yaml 声明 video_16_9.mp4")
    else:
        bad("pipeline.yaml 声明 video_16_9.mp4", "未找到")
    old = scan_literals([r"video_landscape_16_9"], roots=("src", "scripts"))
    old = [h for h in old if "cover_landscape_path" not in h and "video_landscape_path" not in h]
    if old:
        bad("旧产物命名零残留", f"{len(old)} 处: {old[0]}")
    else:
        ok("旧产物命名零残留")


# ═══════════════════════════════ T4.2 去硬编码
def test_t42() -> None:
    print("\n[T4.2] 账号/来源配置化")
    from src.utils.accounts import (AccountConfigError, by_id, default_account,
                                    for_platform, load_accounts)

    accounts = load_accounts()
    enabled = [a for a in accounts if a.get("enabled", True)]
    if not enabled:
        skip("账号配置", "accounts.yaml 无启用账号")
        return

    dft = default_account()
    dft_id = dft.get("id") if isinstance(dft, dict) else str(dft)
    if dft_id == enabled[0].get("id"):
        ok("default_account 取首个启用账号", dft_id)
    else:
        bad("default_account 取首个启用账号", f"{dft_id} != {enabled[0].get('id')}")

    platforms = {a.get("platform") for a in enabled}
    if "douyin" in platforms:
        got = for_platform("douyin")
        got_id = got.get("id") if isinstance(got, dict) else str(got)
        if got_id == next(a["id"] for a in enabled if a.get("platform") == "douyin"):
            ok("for_platform(douyin) 命中配置账号", got_id)
        else:
            bad("for_platform(douyin) 命中配置账号", got_id)
    else:
        skip("for_platform(douyin)", "无启用 douyin 账号")

    if by_id("__no_such_account__") is None:
        ok("by_id(不存在) 返回 None")
    else:
        bad("by_id(不存在) 返回 None", "返回了非 None")

    empty_platforms = {"douyin", "bilibili"} - platforms   # xhs 已下线，不再纳入契约
    if empty_platforms:
        p = sorted(empty_platforms)[0]
        expect_raises(f"for_platform({p}) 无账号即报错", lambda: for_platform(p),
                      AccountConfigError, f"了 {p} 无账号")
    else:
        skip("for_platform(空平台) 报错", "三个平台都有启用账号")

    # source_url 三态（契约现由 src/pipeline/manifest.py 承担）
    from src.pipeline.manifest import load_manifest, validate
    m = load_manifest({"title": "测试物料标题", "source_url": "https://example.com/a"})
    if m.source_url == "https://example.com/a":
        ok("source_url 直传")
    else:
        bad("source_url 直传", f"实际 {m.source_url}")
    m2 = load_manifest({"title": "测试物料标题", "body": "这是一段足够长的测试正文内容，用于校验 body 兜底路径。"})
    if m2.source_url is None and not validate(m2):
        ok("body 存在时 source_url 可空")
    else:
        bad("body 存在时 source_url 可空", str(validate(m2)))
    if any("body 或 source_url" in e for e in validate({"title": "测试物料标题"})):
        ok("source_url 缺失即报错")
    else:
        bad("source_url 缺失即报错", "validate 未命中 anyOf 规则")

    hits = scan_literals([r"127\.0\.0\.1:8317", r"douyin-tech-main",
                          r"news\.hot-digest\.local"])
    if hits:
        bad("账号/来源字面量零残留", f"{len(hits)} 处: {hits[0]}")
    else:
        ok("账号/来源字面量零残留")


# ═══════════════════════════════ T4.5 TTS 接线
def test_t45() -> None:
    print("\n[T4.5] TTS 槽位接线")
    from src.pipeline.config import MissingConfig, resolve_env
    from src.tts.aligner import TTSAligner

    for slot, vkey, rkey in (("tts.tech", "TTS_VOICE_TECH", "TTS_RATE"),
                             ("tts.news", "TTS_VOICE_NEWS", "TTS_RATE")):
        a = TTSAligner.from_slot(slot)
        if a.voice == resolve_env(vkey) and a.rate == resolve_env(rkey):
            ok(f"from_slot({slot}) 对齐 env", f"{a.voice} {a.rate}")
        else:
            bad(f"from_slot({slot}) 对齐 env",
                f"{a.voice}/{a.rate} vs {resolve_env(vkey)}/{resolve_env(rkey)}")

    expect_raises("无参构造被拒", lambda: TTSAligner(), TypeError, "了无参构造")
    expect_raises("空音色被拒", lambda: TTSAligner(voice="", rate=resolve_env("TTS_RATE") or "x"),
                  ValueError, "了空音色")
    expect_raises("非法槽位被拒", lambda: TTSAligner.from_slot("tts.__nope__"),
                  MissingConfig, "了非法槽位")

    # 缺配置必须炸：子进程 + AUTOCONTENT_ENV_FILE=/dev/null，避免 .env 回填
    env = {k: v for k, v in os.environ.items()
           if k not in ("TTS_VOICE_TECH", "TTS_VOICE_NEWS", "TTS_RATE")}
    env["AUTOCONTENT_ENV_FILE"] = "/dev/null"
    code = (
        "import sys; sys.path.insert(0, '.')\n"
        "from src.tts.aligner import TTSAligner\n"
        "try:\n"
        "    TTSAligner.from_slot('tts.tech')\n"
        "except Exception as e:\n"
        "    print(getattr(e, 'code', None)); raise SystemExit(0)\n"
        "raise SystemExit(1)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip() == "78":
        ok("缺配置抛 MissingConfig(code=78)")
    else:
        bad("缺配置抛 MissingConfig(code=78)", f"rc={r.returncode} out={r.stdout.strip()!r}")

    hits = scan_literals([r"Neural\b", r"rate\s*=\s*[\"']\+10%"])
    if hits:
        bad("音色/语速字面量零残留", f"{len(hits)} 处: {hits[0]}")
    else:
        ok("音色/语速字面量零残留")


# ═══════════════════════════════ T4.7 出片/发片解耦
def test_t47() -> None:
    print("\n[T4.7] 出片 / 发片解耦")
    from src.pipeline.contracts import ContractError, load_pipeline
    from src.pipeline.runner import dispatch_policy, write_artifacts_manifest

    saved = os.environ.get("DISPATCH_MODE")
    try:
        for mode, want in (("", False), ("publish", False), ("output_only", True)):
            os.environ["DISPATCH_MODE"] = mode
            got, got_mode, aspects = dispatch_policy(load_pipeline())
            if got is want:
                ok(f"DISPATCH_MODE={mode or '(空)'} → output_only={want}", f"mode={got_mode}")
            else:
                bad(f"DISPATCH_MODE={mode or '(空)'} → output_only={want}", f"实际 {got}")
    finally:
        if saved is None:
            os.environ.pop("DISPATCH_MODE", None)
        else:
            os.environ["DISPATCH_MODE"] = saved

    cfg_off = {"steps": [{"id": "dispatch", "params": {"enabled": False}}],
               "inputs": {"aspects": ["vertical"]}}
    if dispatch_policy(cfg_off)[0] is True:
        ok("dispatch.enabled=false → output_only")
    else:
        bad("dispatch.enabled=false → output_only", "未生效")

    for aspects, label in (([], "aspects 为空"), (["square"], "aspects 非法值")):
        cfg = {"steps": [{"id": "dispatch", "params": {}}], "inputs": {"aspects": aspects}}
        expect_raises(f"{label} 被拒", lambda c=cfg: dispatch_policy(c),
                      ContractError, f"了{label}")

    # artifacts.txt 父目录自动创建（复核时发现的 bug）
    tmp = Path(tempfile.mkdtemp()) / "not_created_yet"
    try:
        p = write_artifacts_manifest(tmp, {"video": str(PROJ / "pipeline.yaml")}, "output_only")
        if p.exists() and str(PROJ / "pipeline.yaml") in p.read_text(encoding="utf-8"):
            ok("artifacts.txt 自动创建父目录")
        else:
            bad("artifacts.txt 自动创建父目录", "文件内容不符")
    except Exception as e:  # noqa: BLE001
        bad("artifacts.txt 自动创建父目录", f"{type(e).__name__}: {e}")

    runner_src = (PROJ / "src" / "pipeline" / "runner.py").read_text(encoding="utf-8")
    leaked = [k for k in ("xhs_storage_state", "xiaohongshu_cdp") if k in runner_src]
    if leaked:
        bad("runner 无登录态耦合", f"出现 {leaked}")
    else:
        ok("runner 无登录态耦合")

    # 交付契约：artifacts.txt 必须按 aspect 分组，交付脚本必须拒绝缺产物
    tmp2 = Path(tempfile.mkdtemp())
    sample = {"video": str(PROJ / "pipeline.yaml"), "cover": str(PROJ / "pipeline.yaml"),
              "video_16_9": str(PROJ / "pipeline.yaml"), "cover_16_9": str(PROJ / "pipeline.yaml")}
    p2 = write_artifacts_manifest(tmp2, sample, "output_only")
    txt = p2.read_text(encoding="utf-8")
    if "[vertical]" in txt and "[landscape]" in txt:
        ok("artifacts.txt 按竖版/横版分组标注")
    else:
        bad("artifacts.txt 按竖版/横版分组标注", "缺分组标签")

    r = subprocess.run([sys.executable, "scripts/send_run_artifacts.py",
                        "--run-id", "__no_such_run__", "--dry-run"],
                       cwd=str(PROJ), capture_output=True, text=True, timeout=60)
    if r.returncode == 2:
        ok("交付脚本拒绝不存在的 run")
    else:
        bad("交付脚本拒绝不存在的 run", f"rc={r.returncode}")


def test_p0_blockers() -> None:
    """P0 三项阻断回归（2026-11-11 移交单）：B站入口 / 出片两条 / 抖音 claim。

    每条都对应一次实测复现：修复前崩溃或留脏台账，修复后必须是「可读错误 + 约定退出码」。
    """
    from src.pipeline.runner import find_runs_for_identity, resolve_run_identity
    from src.db import PipelineDB

    old_root = PROJ / "output" / "20261107" / "old-id"
    new_root = PROJ / "output" / "20261111" / "new-id"

    def ident(content, named=None, explicit="", resume=False):
        return resolve_run_identity(
            content, named, explicit_run_id=explicit, resume=resume,
            new_run_id="gen-id", new_run_root=new_root, task_id="new-task")

    blocked = {"run_id": "old-id", "status": "BLOCKED",
               "run_root": str(old_root), "task_id": "old-task"}
    done = {"run_id": "old-id", "status": "DONE", "run_root": str(old_root), "task_id": "old-task"}

    # 0) force_new 落库：唯一索引 uq_run_content 下必须能建第二条 run，旧行转归档
    with tempfile.TemporaryDirectory() as td:
        db = PipelineDB(str(Path(td) / "s.db"))
        h = "hash-p0"
        db.upsert_run("run-a", "task-a", h, "acct", Path(td) / "a")
        same = db.upsert_run("run-b", "task-b", h, "acct", Path(td) / "b")
        forced = db.upsert_run("run-b", "task-b", h, "acct", Path(td) / "b", force_new=True)
        found = db.find_run_by_content(h, "acct") or {}
        archived = db.get_run("run-a") or {}
        if (same, forced, found.get("run_id"), archived.get("content_hash"), archived.get("run_root")) \
                == ("run-a", "run-b", "run-b", f"{h}#superseded:run-a", str(Path(td) / "a")):
            ok("force_new 建新 run 并归档旧行（唯一索引不冲突、旧 run_root 保留）")
        else:
            bad("force_new 建新 run 并归档旧行", f"same={same} forced={forced} "
                                               f"found={found.get('run_id')} archived={archived.get('content_hash')}")

    # 1) 显式新 --run-id + 同内容 BLOCKED → 全新目录（不劫持旧 run）
    rid, root, task, mode, force = ident(blocked, explicit="new-id")
    if (rid, Path(root), mode, force) == ("new-id", new_root, "explicit_new", True):
        ok("显式 --run-id 不复用 BLOCKED 历史 run（走新目录 + force_new）", f"mode={mode}")
    else:
        bad("显式 --run-id 不复用 BLOCKED 历史 run",
            f"实际 {(rid, root, mode, force)}")

    # 2) 同内容已 DONE：即便显式给新 run_id 也必须幂等跳过（GATE-3 B 段契约）
    if ident(done, explicit="new-id")[3] == "skip":
        ok("同内容 DONE 时幂等跳过（显式 run-id 也不越过）")
    else:
        bad("同内容 DONE 时幂等跳过", f"{ident(done, explicit='new-id')}")

    # 3) 显式 run-id 在库里有记录 → 按 **id** 用它自己的 run_root（不按内容猜）
    named = {"run_id": "g3-a", "status": "RUNNING",
             "run_root": str(PROJ / "output" / "20261111" / "g3-a"), "task_id": "t-a"}
    rid2, root2, _, mode2, force2 = ident(blocked, named, explicit="g3-a", resume=True)
    if (rid2, Path(root2), mode2, force2) == ("g3-a", PROJ / "output" / "20261111" / "g3-a",
                                              "resume", False):
        ok("显式 run-id 按 id 取原 run_root（resume 不被内容行劫持）")
    else:
        bad("显式 run-id 按 id 取原 run_root", f"{(rid2, root2, mode2)}")

    # 4) 未显式指定时的复用规则必须保留（否则续跑找不到产物）
    rid3, root3, _, mode3, force3 = ident(blocked)
    if (rid3, Path(root3), mode3, force3) == ("old-id", old_root, "retry", False):
        ok("未显式指定 run-id 时仍复用历史 run（retry）")
    else:
        bad("未显式指定 run-id 时仍复用历史 run（retry）", f"{(rid3, root3, mode3)}")
    if ident(None, None)[3] == "fresh":
        ok("无历史 run → fresh")
    else:
        bad("无历史 run → fresh", f"{ident(None, None)}")

    # 4b) 接线：两个查询各查一次，显式 run-id 必须按 id 取（只测纯函数会漏这里）
    class _FakeDB:
        def __init__(self):
            self.by_content = 0
            self.by_id: list[str] = []

        def find_run_by_content(self, content_hash, account):
            self.by_content += 1
            return blocked

        def get_run(self, run_id):
            self.by_id.append(run_id)
            return None

    fake = _FakeDB()
    c_run, n_run = find_runs_for_identity(fake, "h", "acct", "new-id")
    if c_run == blocked and n_run is None and fake.by_content == 1 and fake.by_id == ["new-id"]:
        ok("显式 run-id 时按 id 查（接线）")
    else:
        bad("显式 run-id 时按 id 查（接线）",
            f"content={fake.by_content} by_id={fake.by_id}")

    # 5) render 前置清理：失效软链必须删，指向有效目录的软链必须留
    import shutil
    from src.pipeline.executors import RENDERER, _prune_dangling_public_links

    runs_dir = RENDERER / "public" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    dead = runs_dir / "__p0_dangling__"
    live = runs_dir / "__p0_live__"
    keep = Path(tempfile.mkdtemp(prefix="p0props-"))
    for link, target in ((dead, Path("/tmp/__p0_no_such_dir__")), (live, keep)):
        if link.is_symlink():
            link.unlink()
        link.symlink_to(target, target_is_directory=True)
    try:
        pruned = _prune_dangling_public_links()
        if "__p0_dangling__" in pruned and not dead.exists() and live.is_symlink() and live.exists():
            ok("render 前置清理失效软链（有效软链不动）", f"pruned={pruned}")
        else:
            bad("render 前置清理失效软链", f"pruned={pruned} dead_exists={dead.exists()} live={live.exists()}")
    finally:
        for link in (dead, live):
            if link.is_symlink():
                link.unlink()
        shutil.rmtree(keep, ignore_errors=True)

    # 5b) 接线：exec_render 必须在读 draft/渲染**之前**先清软链（只测函数本身会漏）
    import src.pipeline.executors as _ex
    hits: list[int] = []
    _orig_prune = _ex._prune_dangling_public_links
    _ex._prune_dangling_public_links = lambda: hits.append(1) or []

    class _Ctx:
        run_root = Path(tempfile.mkdtemp(prefix="p0ctx-"))
        run_id = "p0ctx"
        task_id = "p0ctx"
        material = None
        artifacts: dict = {}
        params: dict = {}
        step: dict = {}
        db_path = ""

    try:
        try:
            _ex.exec_render(_Ctx())
        except Exception:  # noqa: BLE001 — 没有 draft 必然报错，只关心 prune 是否已执行
            pass
    finally:
        _ex._prune_dangling_public_links = _orig_prune
        shutil.rmtree(_Ctx.run_root, ignore_errors=True)
    if hits:
        ok("exec_render 在渲染前调用软链清理（接线）")
    else:
        bad("exec_render 在渲染前调用软链清理（接线）", "prune 未被调用")

    # 6) B 站上传器入口：provider 形态（非 --run-id）不能再崩在 `import src`
    from src.utils.py_interp import find_interpreter
    bili_py, probes = find_interpreter()
    if not bili_py:
        skip("B站上传器入口（provider 形态）", f"无解释器可 import bilibili_api: "
                                          f"{[p['reason'] for p in probes][-1:]}")
    else:
        bad_video = subprocess.run(
            [bili_py, "scripts/bilibili_live_uploader.py", "--video", "/tmp/__p0_none__.mp4",
             "--title", "P0", "--desc", "x", "--tags", "科技"],
            cwd=str(PROJ), capture_output=True, text=True, timeout=120)
        if bad_video.returncode == 2 and "视频不存在" in bad_video.stdout \
                and "No module named 'src'" not in (bad_video.stdout + bad_video.stderr):
            ok("B站上传器 provider 形态（不再崩 import src）", f"rc={bad_video.returncode}")
        else:
            bad("B站上传器 provider 形态（不再崩 import src）",
                f"rc={bad_video.returncode} err={bad_video.stderr.strip()[-120:]}")

        blank = subprocess.run(
            [bili_py, "scripts/bilibili_live_uploader.py", "--video", "/tmp/__p0_none__.mp4",
             "--title", "   ", "--desc", "x", "--tags", "科技"],
            cwd=str(PROJ), capture_output=True, text=True, timeout=120)
        if blank.returncode == 2 and "title" in blank.stdout:
            ok("B站上传器拦纯空格标题（不再吃 21009）", f"rc={blank.returncode}")
        else:
            bad("B站上传器拦纯空格标题", f"rc={blank.returncode} out={blank.stdout.strip()[-120:]}")

        # 走台账的真实形态：同一视频已有未收尾 claim → rc=3（证明 `import src` 已通）
        import shutil as _sh
        tmpdir = tempfile.mkdtemp(prefix="p0ledger-")
        try:
            video = Path(tmpdir) / "tiny.mp4"
            video.write_bytes(b"x")
            env = dict(os.environ, SQLITE_DB_PATH=str(Path(tmpdir) / "ledger.sqlite"))
            from src.publish_ledger import check_and_claim
            check_and_claim("bilibili", "bilibili-tech-main", video, "P0",
                            path=Path(tmpdir) / "ledger.sqlite")
            blocked = subprocess.run(
                [bili_py, "scripts/bilibili_live_uploader.py", "--video", str(video),
                 "--title", "P0", "--desc", "x", "--tags", "科技"],
                cwd=str(PROJ), capture_output=True, text=True, timeout=120, env=env)
            if blocked.returncode == 3 and "blocked" in blocked.stdout:
                ok("B站上传器走到台账查重（import src 打通）", f"rc={blocked.returncode}")
            else:
                bad("B站上传器走到台账查重（import src 打通）",
                    f"rc={blocked.returncode} out={blocked.stdout.strip()[-160:]} "
                    f"err={blocked.stderr.strip()[-120:]}")
        finally:
            _sh.rmtree(tmpdir, ignore_errors=True)

    # 7) 抖音：CDP 掉线 → rc=2 且**不留 claim**，第二次不被自己拦住
    import shutil as _sh2
    tmpdir = tempfile.mkdtemp(prefix="p0dy-")
    try:
        video = Path(tmpdir) / "tiny.mp4"
        video.write_bytes(b"x")
        db_path = Path(tmpdir) / "ledger.sqlite"
        env = dict(os.environ, SQLITE_DB_PATH=str(db_path))
        args = [sys.executable, "scripts/publish_douyin_cdp.py", "--video", str(video),
                "--title", "P0抖音", "--cdp-url", "http://127.0.0.1:1"]
        first = subprocess.run(args, cwd=str(PROJ), capture_output=True, text=True,
                               timeout=180, env=env)
        second = subprocess.run(args, cwd=str(PROJ), capture_output=True, text=True,
                                timeout=180, env=env)
        rows = 0
        if db_path.exists():
            import sqlite3
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute("SELECT COUNT(*) FROM publish_ledger").fetchone()[0]
        if first.returncode == 2 and second.returncode == 2 and rows == 0:
            ok("抖音 CDP 掉线不留 claim（两次都 rc=2，不被查重拦）", f"rows={rows}")
        else:
            bad("抖音 CDP 掉线不留 claim",
                f"rc1={first.returncode} rc2={second.returncode} rows={rows}")
    finally:
        _sh2.rmtree(tmpdir, ignore_errors=True)

    # 8) 前置失败释放 claim 的状态机：claimed → VALIDATION → failed（可重试）
    from src.publish_ledger import PublishBlocked, check_and_claim as _claim, record_outcome
    tmpdir = tempfile.mkdtemp(prefix="p0rel-")
    try:
        video = Path(tmpdir) / "tiny.mp4"
        video.write_bytes(b"y")
        db_path = Path(tmpdir) / "ledger.sqlite"
        key, _ = _claim("douyin", "douyin-tech-main", video, "P0", path=db_path)
        state = record_outcome(key, False, status="VALIDATION", reason="preflight", path=db_path)
        try:
            key2, _ = _claim("douyin", "douyin-tech-main", video, "P0", path=db_path)
        except PublishBlocked as exc:
            key2 = ""
            why = str(exc)
        else:
            why = ""
        if state == "failed" and key2 == key:
            ok("前置失败释放 claim 后可重新占位", f"state={state}")
        else:
            bad("前置失败释放 claim 后可重新占位", f"state={state} key2={key2} why={why[:60]}")
    finally:
        _sh2.rmtree(tmpdir, ignore_errors=True)


def main() -> int:
    print("=== 任务契约回归（T4.1 / T4.2 / T4.5 / T4.7 / P0 阻断）===")
    for fn in (test_t41, test_t42, test_t45, test_t47, test_p0_blockers):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — 单个分区炸掉不该吞掉其余结果
            bad(f"{fn.__name__} 分区异常", f"{type(e).__name__}: {e}")

    print("\n════════════ 汇总 ════════════")
    print(f"  ✅ PASS {len(PASS)}   ❌ FAIL {len(FAIL)}   ⏭️  SKIP {len(SKIP)}")
    if FAIL:
        for f in FAIL:
            print(f"     ❌ {f}")
        print("\nCONTRACT FAIL")
        return 1
    print("\nCONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
