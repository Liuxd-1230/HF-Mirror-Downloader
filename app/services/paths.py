"""应用数据目录定位。

优先级：
  1. 源码运行 → 项目根目录（main.py 所在目录），日志即 <根>/logs/app.log
  2. 打包 exe  → exe 所在目录（可写时）
  3. 以上不可写 → %LOCALAPPDATA%/HFMirrorDownloader
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _candidate_dirs() -> list[Path]:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    else:
        candidates.append(Path(__file__).resolve().parents[2])
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "HFMirrorDownloader")
    else:
        candidates.append(Path.home() / ".hfmirror-downloader")
    return candidates


def app_base_dir() -> Path:
    """返回可写的数据目录（第一个可写入的候选）。"""
    for cand in _candidate_dirs():
        try:
            probe = cand / "logs"
            probe.mkdir(parents=True, exist_ok=True)
            marker = probe / ".write_test"
            marker.write_text("ok", encoding="utf-8")
            marker.unlink()
            return cand
        except OSError:
            continue
    raise RuntimeError("找不到可写的数据目录")  # pragma: no cover


def logs_dir() -> Path:
    return app_base_dir() / "logs"


def logs_file() -> Path:
    return logs_dir() / "app.log"


def config_dir() -> Path:
    return app_base_dir()
