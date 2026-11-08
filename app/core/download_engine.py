"""下载引擎（纯 Python，无 Qt 依赖）。

设计要点
========
1. 复用 huggingface_hub：逐文件调用 hf_hub_download()，endpoint 直指镜像。
   缓存结构、断点续传、etag 校验、Windows 符号链接降级全部由官方库完成。
2. 进度与暂停/取消：向 hf_hub_download 传入自定义 tqdm_class（继承原生
   tqdm，hf 对非 hf 子类不注入 disable 参数，update() 必然被调用）。
   每次 chunk 更新时检查 cancel_event，需要暂停/取消时抛出协作式中断
   异常 —— .incomplete 临时文件保留在磁盘上，恢复时官方库自动 Range 续传。
3. 并发：每任务一个守护线程 + 可动态调最大值的动态信号量（设置页改并发
   数即时生效）。
4. 重试：仅对网络类错误自动重试（指数退避）；401/403/404 不重试。
"""

from __future__ import annotations

import hashlib
import inspect
import io
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from ..models import BatchSpec, DownloadTask, TaskState
from .errors import (
    KIND_AUTH,
    KIND_NETWORK,
    RETRYABLE_KINDS,
    FriendlyError,
    translate_exception,
)
from .formatting import format_eta
from . import hf_http_patch

logger = logging.getLogger(__name__)

# hf 2.0 全局共享 httpx2.Client 在 Windows 多线程并发流式下载时偶发
# WinError 10038（WSAENOTSOCK）——启用每线程独立 client 的隔离补丁。
hf_http_patch.apply()

_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_FALLBACK_CHUNK = 8 * 1024 * 1024


class _TaskInterrupted(Exception):
    """tqdm 钩子内部抛出的协作式中断（暂停或取消）。"""

    def __init__(self, action: str):  # "pause" | "cancel"
        self.action = action


class DynamicSemaphore:
    """最大并发数可动态调整的信号量（条件变量实现）。"""

    def __init__(self, initial: int):
        self._cond = threading.Condition()
        self._max = max(1, initial)
        self._available = self._max

    def set_max(self, value: int) -> None:
        value = max(1, value)
        with self._cond:
            delta = value - self._max
            self._max = value
            self._available = max(0, min(self._max, self._available + delta))
            self._cond.notify_all()

    def acquire(self, cancel_event: threading.Event | None = None) -> bool:
        """获取许可。等待期间 cancel_event 置位则返回 False（调用方放弃）。"""
        with self._cond:
            while self._available <= 0:
                if cancel_event is not None and cancel_event.is_set():
                    return False
                self._cond.wait(timeout=0.2)
            self._available -= 1
            return True

    def release(self) -> None:
        with self._cond:
            self._available = min(self._max, self._available + 1)
            self._cond.notify_all()


def _make_progress_class(task: DownloadTask):
    """为单个任务生成绑定了取消检查的 tqdm 子类。"""

    from tqdm.auto import tqdm as base_tqdm

    class _NullIO:
        def write(self, *_args, **_kw) -> int:
            return 0

        def flush(self) -> None:
            pass

        def isatty(self) -> bool:
            return False

        def fileno(self) -> int:
            raise io.UnsupportedOperation

    class _HookedTqdm(base_tqdm):
        def __init__(self, *args, **kwargs):
            # hf 以 initial=resume_size 建立进度条 → 以此为续传进度基线，
            # 保证“恢复下载后进度从已下载处开始”而不是从 0 重走。
            try:
                init = int(kwargs.get("initial") or 0)
            except (TypeError, ValueError):
                init = 0
            if init > 0:
                task.set_resume_baseline(init)
            # 禁止真实输出到控制台/文件；GUI 自己展示进度
            kwargs["file"] = _NullIO()
            kwargs["disable"] = False
            kwargs.setdefault("mininterval", 0.3)
            super().__init__(*args, **kwargs)

        def update(self, n=1):
            if task.cancel_event.is_set():
                raise _TaskInterrupted(task.stop_action or "cancel")
            super().update(n)
            if n and n > 0:
                task.note_bytes(n)

    return _HookedTqdm


class DownloadEngine:
    """管理一批批下载任务：并发调度、暂停/恢复/取消/重试、速度统计。"""

    def __init__(
        self,
        token_provider: Callable[[], str | None],
        history=None,
        max_workers: int = 4,
        max_retries: int = 3,
        retry_base_delay: float = 2.0,
    ):
        self._token_provider = token_provider
        self.history = history
        self._sem = DynamicSemaphore(max_workers)
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._tasks: dict[str, DownloadTask] = {}
        self._order: list[str] = []
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()

    # ---------- 对外 API ----------

    def set_max_workers(self, n: int) -> None:
        self._sem.set_max(n)

    def set_max_retries(self, n: int) -> None:
        self._max_retries = max(0, n)

    def submit(self, spec: BatchSpec) -> str:
        batch_id = uuid.uuid4().hex[:8]
        with self._lock:
            for f in spec.files:
                task = DownloadTask(
                    repo_id=spec.repo_id,
                    revision=spec.revision,
                    filename=f.path,
                    size=f.size,
                    mode=spec.mode,
                    local_dir=spec.local_dir,
                    cache_dir=spec.cache_dir,
                    endpoint=spec.endpoint,
                    batch_id=batch_id,
                    lfs_sha256=getattr(f, "lfs_sha256", None),
                    git_oid=getattr(f, "oid", None),
                )
                self._tasks[task.task_id] = task
                self._order.append(task.task_id)
                self._start_thread(task)
        logger.info(
            "提交下载批次 %s：%s@%s，%d 个文件，模式 %s",
            batch_id, spec.repo_id, spec.revision, len(spec.files), spec.mode,
        )
        return batch_id

    def pause(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        if task.state is TaskState.DOWNLOADING:
            task.stop_action = "pause"
            task.cancel_event.set()
            task.set_state(TaskState.PAUSED, note="暂停中…")
        elif task.state is TaskState.PENDING:
            # 还在排队等并发名额：同样标记暂停，等待中的线程会自行退出
            task.stop_action = "pause"
            task.cancel_event.set()
            task.set_state(TaskState.PAUSED)

    def resume(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task and task.state in (TaskState.PAUSED, TaskState.CANCELLED, TaskState.FAILED):
            self._reset_and_rerun(task)

    def cancel(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if not task or task.state in (TaskState.COMPLETED, TaskState.CANCELLED):
            return
        if task.state is TaskState.DOWNLOADING:
            task.stop_action = "cancel"
            task.cancel_event.set()
            # 状态由线程在捕获中断后置为 CANCELLED
        else:
            task.set_state(TaskState.CANCELLED)

    def retry(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task and task.state in (TaskState.FAILED, TaskState.CANCELLED, TaskState.PAUSED):
            self._reset_and_rerun(task)

    def pause_all(self) -> None:
        for tid in list(self._active_ids()):
            self.pause(tid)

    def resume_all(self) -> None:
        for tid in list(self._order):
            self.resume(tid)

    def cancel_all(self) -> None:
        for tid in list(self._order):
            self.cancel(tid)

    def retry_failed(self, batch_id: str | None = None) -> None:
        for tid in list(self._order):
            t = self._tasks.get(tid)
            if t and t.state is TaskState.FAILED and (batch_id is None or t.batch_id == batch_id):
                self.retry(tid)

    def snapshot(self) -> list[dict]:
        with self._lock:
            ids = list(self._order)
        out = []
        for tid in ids:
            t = self._tasks.get(tid)
            if t:
                out.append(t.snapshot())
        return out

    def batch_summary(self, batch_id: str) -> dict:
        tasks = [t for t in self._tasks.values() if t.batch_id == batch_id]
        total = sum(t.size or 0 for t in tasks)
        done = 0
        done_count = failed_count = active = 0
        for t in tasks:
            done += min(t.bytes_done, t.size or t.bytes_done)
            if t.state is TaskState.COMPLETED:
                done_count += 1
            elif t.state is TaskState.FAILED:
                failed_count += 1
            if t.state in (TaskState.DOWNLOADING, TaskState.PENDING):
                active += 1
        # 汇总速度：对各任务速度快照求和
        speed = sum(t.snapshot()["speed"] for t in tasks)
        remaining = max(total - done, 0)
        eta = remaining / speed if speed > 0 else None
        return {
            "batch_id": batch_id,
            "total": total,
            "done": done,
            "speed": speed,
            "eta": eta,
            "done_count": done_count,
            "failed_count": failed_count,
            "active": active,
            "count": len(tasks),
        }

    def all_tasks_finished(self, batch_id: str) -> bool:
        return all(
            t.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED)
            for t in self._tasks.values()
            if t.batch_id == batch_id
        )

    def update_endpoint(self, endpoint: str) -> None:
        """切换下载源：所有未完成任务的下一次尝试改用新 endpoint。"""
        endpoint = endpoint.rstrip("/")
        with self._lock:
            for t in self._tasks.values():
                if t.state is not TaskState.COMPLETED:
                    t.endpoint = endpoint
        logger.info("下载源已切换为 %s（对未完成任务生效）", endpoint)

    def export_incomplete_batches(self) -> list[dict]:
        """导出仍有未完成文件的批次（供跨会话队列持久化）。"""
        with self._lock:
            ids = list(self._order)
        batches: dict[str, dict] = {}
        for tid in ids:
            t = self._tasks.get(tid)
            if t is None:
                continue
            b = batches.get(t.batch_id)
            if b is None:
                b = batches[t.batch_id] = {
                    "batch_id": t.batch_id,
                    "repo_id": t.repo_id,
                    "revision": t.revision,
                    "mode": t.mode,
                    "local_dir": t.local_dir,
                    "cache_dir": t.cache_dir,
                    "endpoint": t.endpoint,
                    "files": [],
                }
            b["files"].append(
                {
                    "path": t.filename,
                    "size": t.size,
                    "lfs_sha256": t.lfs_sha256,
                    "oid": t.git_oid,
                    "done": t.state is TaskState.COMPLETED,
                    "bytes_done": t.bytes_done if t.state is not TaskState.COMPLETED else 0,
                    "state": t.state.value,
                }
            )
        return [b for b in batches.values() if any(not f["done"] for f in b["files"])]

    def fingerprint(self) -> str:
        """任务状态/进度的变化指纹（主窗口据此决定是否落盘队列）。"""
        with self._lock:
            ids = list(self._order)
        parts = []
        for tid in ids:
            t = self._tasks.get(tid)
            if t is None:
                continue
            done = t.state is TaskState.COMPLETED
            parts.append(f"{tid}:{t.state.value}:{0 if done else t.bytes_done}")
        return "|".join(parts)

    # ---------- 内部实现 ----------

    def _active_ids(self) -> list[str]:
        return [
            tid for tid, t in self._tasks.items()
            if t.state in (TaskState.DOWNLOADING, TaskState.PENDING)
        ]

    def _reset_and_rerun(self, task: DownloadTask) -> None:
        with self._lock:
            live = self._threads.get(task.task_id)
        # 旧线程可能还差最后一次清理才退出（例如正在抛出中断异常），
        # 最多等 3 秒；等不到就不重启，用户可再次点恢复。
        if live is not None and live.is_alive():
            live.join(timeout=3.0)
            if live.is_alive():
                return
        task.cancel_event.clear()
        task.set_state(TaskState.PENDING, error="", error_kind="", note="")
        self._start_thread(task)

    def _start_thread(self, task: DownloadTask) -> None:
        def run():
            if not self._sem.acquire(task.cancel_event):
                # 等待并发名额期间被暂停/取消（未持有许可，无需释放）
                if task.state not in (TaskState.PAUSED, TaskState.CANCELLED):
                    task.set_state(
                        TaskState.PAUSED if (task.stop_action or "cancel") == "pause" else TaskState.CANCELLED
                    )
                return
            try:
                self._run_task(task)
            finally:
                self._sem.release()
                with self._lock:
                    self._threads.pop(task.task_id, None)

        th = threading.Thread(target=run, daemon=True, name=f"dl-{task.filename[:24]}")
        with self._lock:
            self._threads[task.task_id] = th
        th.start()

    def _run_task(self, task: DownloadTask) -> None:
        while True:
            task.attempts += 1
            task.cancel_event.clear()
            try:
                self._download_once(task)
                return
            except _TaskInterrupted as intr:
                if intr.action == "cancel":
                    task.set_state(TaskState.CANCELLED)
                else:
                    task.set_state(TaskState.PAUSED)
                self._record_history(task)
                return
            except Exception as exc:  # noqa: BLE001
                friendly = translate_exception(exc, task.endpoint)
                task.set_state(
                    TaskState.FAILED,
                    error=friendly.message,
                    error_kind=friendly.kind,
                )
                logger.error(
                    "下载失败 %s/%s [%s] 尝试 %d：%s",
                    task.repo_id, task.filename, friendly.kind, task.attempts, exc,
                    exc_info=friendly.kind not in (KIND_AUTH, KIND_NETWORK),
                )
                can_retry = (
                    friendly.kind in RETRYABLE_KINDS
                    and task.attempts <= self._max_retries
                    and not task.cancel_event.is_set()
                )
                if can_retry:
                    delay = min(self._retry_base_delay * (2 ** (task.attempts - 1)), 20.0)
                    logger.info("将在 %.1f 秒后重试（第 %d 次）：%s", delay, task.attempts, task.filename)
                    task.set_state(TaskState.PENDING, note=f"等待重试（{task.attempts}/{self._max_retries}）…")
                    if task.cancel_event.wait(delay):
                        if (task.stop_action or "cancel") == "pause":
                            task.set_state(TaskState.PAUSED)
                        else:
                            task.set_state(TaskState.CANCELLED)
                        self._record_history(task)
                        return
                    task.set_state(TaskState.DOWNLOADING, note="")
                    continue
                # 出错的同时用户请求了暂停 → 以暂停为准
                if task.cancel_event.is_set() and (task.stop_action or "cancel") == "pause":
                    task.set_state(TaskState.PAUSED)
                    return
                self._record_history(task)
                return

    def _download_once(self, task: DownloadTask) -> None:
        from huggingface_hub import hf_hub_download

        if task.cancel_event.is_set():
            raise _TaskInterrupted("cancel")

        token = None
        try:
            token = self._token_provider()
        except Exception:  # noqa: BLE001
            logger.exception("读取 Token 失败，按无 Token 继续")

        task.set_state(TaskState.DOWNLOADING)
        task.bytes_done = 0
        task._samples.clear()
        task._start_bytes = 0

        progress_class = _make_progress_class(task)
        kwargs: dict = dict(
            repo_id=task.repo_id,
            filename=task.filename,
            revision=task.revision,
            endpoint=task.endpoint.rstrip("/") or None,
            token=token,
            tqdm_class=progress_class,
            etag_timeout=15,
        )
        if task.mode == "local_dir":
            kwargs["local_dir"] = task.local_dir
        else:
            kwargs["cache_dir"] = task.cache_dir

        # 兼容检查：hf_hub_download 必须支持 tqdm_class（2.0 确认支持）
        if "tqdm_class" not in inspect.signature(hf_hub_download).parameters:  # pragma: no cover
            kwargs.pop("tqdm_class")

        started = time.time()
        try:
            path = hf_hub_download(**kwargs)
        except _TaskInterrupted:
            raise
        except Exception as exc:
            # 部分镜像（如 Cloudflare 缓存层）会丢失 X-Repo-Commit 等 HF 标准头，
            # 导致官方库拒绝下载。此时降级为自研 HTTP 续传下载（仍按官方缓存结构落盘）。
            if "X-Repo-Commit" in str(exc) or type(exc).__name__ == "FileMetadataError":
                logger.warning("镜像响应缺少 HF 标准头，改用降级下载：%s/%s", task.repo_id, task.filename)
                path = self._fallback_download(task, token)
            else:
                raise

        task.set_state(TaskState.COMPLETED, local_path=str(path))
        if task.bytes_done == 0:
            task.note = "文件已存在，跳过下载"
        logger.info(
            "完成 %s/%s → %s（用时 %.1f 秒，本次 %s）",
            task.repo_id, task.filename, path, time.time() - started,
            "秒传" if task.bytes_done == 0 else f"{task.bytes_done} 字节",
        )
        self._record_history(task)

    # ---------- 降级下载（镜像响应缺少 HF 标准头时使用） ----------

    def _repo_commit(self, task: DownloadTask, token: str | None) -> str | None:
        """查询仓库 revision 对应的 commit hash（带缓存）。"""
        with self._lock:
            cache = getattr(self, "_commit_cache", None)
            if cache is None:
                cache = self._commit_cache = {}
        key = (task.repo_id, task.revision, bool(token))
        if key in cache:
            return cache[key]
        from .hf_client import build_api

        info = build_api(task.endpoint, token).model_info(task.repo_id, revision=task.revision)
        commit = getattr(info, "sha", None)
        if commit:
            with self._lock:
                cache[key] = commit
        return commit

    def _fallback_download(self, task: DownloadTask, token: str | None) -> str:
        """自研 HTTP 续传下载。

        仅当 hf_hub_download 因镜像返回非标准响应而失败时调用。
        缓存模式按官方结构落盘：blobs/<etag> + snapshots/<commit>/<path> + refs/<revision>；
        local_dir 模式直接放到 <local_dir>/<path>。
        """
        import requests

        etag = task.lfs_sha256 or task.git_oid
        url = f"{task.endpoint.rstrip('/')}/{task.repo_id}/resolve/{task.revision}/{quote(task.filename)}"
        headers = {"Accept-Encoding": "identity"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        if task.mode == "local_dir":
            final = Path(task.local_dir) / task.filename
            final.parent.mkdir(parents=True, exist_ok=True)
            part = final.with_name(final.name + ".part")
        else:
            if not etag:
                raise RuntimeError(f"镜像未提供 {task.filename} 的元数据，无法降级下载到缓存模式")
            cache_root = Path(task.cache_dir) / f"models--{task.repo_id.replace('/', '--')}"
            blob_dir = cache_root / "blobs"
            blob_dir.mkdir(parents=True, exist_ok=True)
            part = blob_dir / f"{etag}.incomplete"

        resume = part.stat().st_size if part.exists() else 0
        already_complete = task.size is not None and task.size > 0 and resume == task.size
        if resume > 0 and not already_complete:
            # 续传：进度从已存在字节开始计
            with task._lock:
                task.bytes_done = resume
                task._start_bytes = resume
        logger.info("降级下载 %s（已续传 %d 字节，完整=%s）", url, resume, already_complete)

        if not already_complete:
            req_headers = dict(headers)
            if resume > 0:
                req_headers["Range"] = f"bytes={resume}-"

            with requests.get(url, headers=req_headers, stream=True, timeout=(10, 60), allow_redirects=True) as resp:
                if resp.status_code not in (200, 206):
                    from .errors import classify_status, FriendlyError

                    kind = classify_status(resp.status_code)
                    msg = {
                        "auth": "该模型可能需要登录 Hugging Face 或接受模型许可（HTTP 401/403）。",
                        "notfound": "文件不存在：该文件可能已被移除、改名，或不在当前分支中。",
                    }.get(kind, f"镜像服务器返回 HTTP {resp.status_code}")
                    raise FriendlyError(kind, msg)
                append_mode = resume > 0 and resp.status_code == 206
                with open(part, "ab" if append_mode else "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=_FALLBACK_CHUNK):
                        if task.cancel_event.is_set():
                            raise _TaskInterrupted(task.stop_action or "cancel")
                        if chunk:
                            fh.write(chunk)
                            task.note_bytes(len(chunk))

        # 校验（LFS 有 sha256 时）
        if task.lfs_sha256:
            logger.info("校验 sha256：%s", part)
            h = hashlib.sha256()
            with open(part, "rb") as fh:
                for blk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
                    h.update(blk)
            if h.hexdigest() != task.lfs_sha256:
                part.unlink(missing_ok=True)
                raise FriendlyError(
                    KIND_NETWORK,
                    "文件校验失败（sha256 不匹配）：下载内容损坏，已删除临时文件。请重试或更换下载源。",
                )

        # 落盘
        if task.mode == "local_dir":
            os.replace(str(part), str(final))
            return str(final)

        blob = blob_dir / etag
        os.replace(str(part), str(blob))

        commit = self._repo_commit(task, token)
        if commit:
            snapshot_file = cache_root / "snapshots" / commit / task.filename
            snapshot_file.parent.mkdir(parents=True, exist_ok=True)
            if not snapshot_file.exists():
                try:
                    rel = os.path.relpath(blob, start=snapshot_file.parent)
                    os.symlink(rel, snapshot_file)
                except OSError:
                    shutil.copy2(str(blob), str(snapshot_file))
            # refs/<revision> → commit（revision 本身是 commit hash 时不写）
            if not _COMMIT_RE.fullmatch(task.revision or ""):
                refs_dir = cache_root / "refs"
                refs_dir.mkdir(parents=True, exist_ok=True)
                (refs_dir / task.revision).write_text(commit, encoding="utf-8")
            return str(snapshot_file)
        # 拿不到 commit（API 也不可用）→ 退回直接使用 blob 文件
        return str(blob)

    def _record_history(self, task: DownloadTask) -> None:
        if self.history is None:
            return
        status = {
            TaskState.COMPLETED: "completed",
            TaskState.FAILED: "failed",
            TaskState.CANCELLED: "canceled",
            TaskState.PAUSED: "canceled",
        }.get(task.state)
        if status is None:
            return
        self.history.add(
            repo_id=task.repo_id,
            revision=task.revision,
            filename=task.filename,
            size=task.size,
            local_path=task.local_path,
            mirror=task.endpoint,
            mode=task.mode,
            status=status,
            error=task.error,
        )
