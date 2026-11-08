"""下载历史（SQLite）。

记录每次下载：仓库、文件、时间、本地位置、镜像、是否成功。
不记录任何 Token。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL,
    revision TEXT,
    filename TEXT NOT NULL,
    size INTEGER,
    local_path TEXT,
    mirror TEXT,
    mode TEXT,
    status TEXT NOT NULL,          -- completed / failed / canceled
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_downloads_created ON downloads(created_at DESC);
"""


class HistoryStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def add(
        self,
        repo_id: str,
        revision: str,
        filename: str,
        size: int | None,
        local_path: str,
        mirror: str,
        mode: str,
        status: str,
        error: str = "",
    ) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._lock, self._conn() as conn:
                conn.execute(
                    "INSERT INTO downloads (repo_id, revision, filename, size, local_path, mirror, mode, status, error, created_at, finished_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (repo_id, revision, filename, size, local_path, mirror, mode, status, error, now, now if status == "completed" else None),
                )
        except sqlite3.Error as exc:
            logger.warning("写入历史失败：%s", exc)

    def query(self, keyword: str = "", limit: int = 500) -> list[dict]:
        sql = "SELECT * FROM downloads"
        args: list = []
        if keyword.strip():
            sql += " WHERE repo_id LIKE ? OR filename LIKE ? OR local_path LIKE ?"
            kw = f"%{keyword.strip()}%"
            args = [kw, kw, kw]
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        try:
            with self._lock, self._conn() as conn:
                return [dict(r) for r in conn.execute(sql, args).fetchall()]
        except sqlite3.Error as exc:
            logger.warning("读取历史失败：%s", exc)
            return []

    def completed_for_repo(self, repo_id: str) -> dict[str, str]:
        """返回某仓库已成功下载的文件 → 最近一次本地路径（用于文件列表“已下载”标记）。"""
        try:
            with self._lock, self._conn() as conn:
                rows = conn.execute(
                    "SELECT filename, local_path FROM downloads"
                    " WHERE repo_id = ? AND status = 'completed' AND local_path != ''"
                    " ORDER BY id DESC",
                    (repo_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            logger.warning("查询历史失败：%s", exc)
            return {}
        out: dict[str, str] = {}
        for r in rows:
            out.setdefault(r["filename"], r["local_path"])
        return out

    def clear(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM downloads")
