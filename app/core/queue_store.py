"""未完成下载队列的跨会话持久化（queue.json）。

主窗口周期性把 DownloadEngine.export_incomplete_batches() 的结果落盘；
下次启动若存在未完成队列，展示「全部续传 / 清除」横幅。
续传时 hf_hub_download 会自动从 .incomplete 断点继续，无需额外逻辑。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class QueueStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def save(self, batches: list[dict]) -> None:
        if not batches:
            self.clear()
            return
        payload = {
            "version": 1,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "batches": batches,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(str(tmp), str(self.path))
        except OSError as exc:
            logger.warning("保存下载队列失败：%s", exc)

    def load(self) -> list[dict]:
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return []
        batches = data.get("batches") if isinstance(data, dict) else None
        if not isinstance(batches, list):
            return []
        # 基本结构校验，坏记录直接丢弃
        ok = []
        for b in batches:
            if not isinstance(b, dict):
                continue
            if not isinstance(b.get("repo_id"), str) or not isinstance(b.get("files"), list):
                continue
            files = [
                f for f in b["files"]
                if isinstance(f, dict) and isinstance(f.get("path"), str)
            ]
            if not files:
                continue
            b["files"] = files
            ok.append(b)
        return ok

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("清除下载队列失败：%s", exc)
