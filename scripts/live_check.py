"""真实镜像在线验证脚本（需可访问 hf-mirror.net）。

流程：
  1. 通过镜像列出 hf-internal-testing/tiny-random-bert 的文件
  2. 用下载引擎经镜像下载该仓库全部小文件（local_dir 模式）
  3. 校验文件落盘内容
  4. 再跑一次缓存模式，确认 snapshot 路径正确

用法：python scripts/live_check.py
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from app.core.download_engine import DownloadEngine
from app.core.hf_client import list_repo_files_full, test_mirror
from app.models import BatchSpec, TaskState

ENDPOINT = os.environ.get("HFM_ENDPOINT", "https://hf-mirror.net")
REPO = "hf-internal-testing/tiny-random-bert"


def wait_all(engine, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snaps = engine.snapshot()
        if snaps and all(s["state"] in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED) for s in snaps):
            return snaps
        time.sleep(0.2)
    raise TimeoutError("下载超时")


def main() -> int:
    print(f"[1/4] 测试镜像 {ENDPOINT} …")
    result = test_mirror(ENDPOINT)
    print("   ", "✅" if result["ok"] else "❌", result["message"], f"(HTTP {result['status']})")
    if not result["ok"]:
        print("镜像不可用，中止。")
        return 2

    print(f"[2/4] 列出 {REPO} 的文件 …")
    files = list_repo_files_full(ENDPOINT, REPO, "main")
    for f in files:
        print(f"    {f.path}  {f.size} B  lfs={f.is_lfs}")
    assert files, "文件列表为空"

    print("[2.5/4] 模糊搜索（无作者关键词）…")
    from app.core.hf_client import search_models
    found = search_models(ENDPOINT, "tiny-random-bert", limit=5)
    ids = [r["repo_id"] for r in found]
    assert REPO in ids, f"搜索结果中未找到目标仓库：{ids}"
    print("    搜索命中:", ids[:3])

    out = Path("build_live_check") / "local_dir"
    print(f"[3/4] local_dir 模式下载 {len(files)} 个文件 → {out} …")
    engine = DownloadEngine(token_provider=lambda: None, max_workers=4, max_retries=2, retry_base_delay=1.0)
    engine.submit(BatchSpec(repo_id=REPO, revision="main", files=files, mode="local_dir",
                            local_dir=str(out), endpoint=ENDPOINT))
    snaps = wait_all(engine)
    failed = [s for s in snaps if s["state"] is not TaskState.COMPLETED]
    for s in snaps:
        print(f"    {s['filename']}: {s['state'].value} {s['error'][:60]}")
    if failed:
        print("❌ 有文件失败")
        return 1
    sample = next(s for s in snaps if s["filename"] == "config.json")
    content = Path(sample["local_path"]).read_text(encoding="utf-8")
    assert "model_type" in content, "config.json 内容异常"
    print("    config.json 内容校验通过")

    print("[4/4] 缓存模式下载 config.json …")
    cache = Path("build_live_check") / "cache"
    engine2 = DownloadEngine(token_provider=lambda: None, max_workers=1, max_retries=2, retry_base_delay=1.0)
    engine2.submit(BatchSpec(repo_id=REPO, revision="main", files=[files[0]], mode="cache",
                             cache_dir=str(cache), endpoint=ENDPOINT))
    snaps2 = wait_all(engine2)
    p = snaps2[0]["local_path"]
    print("    snapshot 路径:", p)
    assert "snapshots" in p and Path(p).is_file()
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
