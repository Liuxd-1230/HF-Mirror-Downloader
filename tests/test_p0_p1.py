"""P0/P1 新能力的测试：队列持久化、错误翻译（httpx/网络OSError）、续传基线、镜像切换。"""

import threading
import time

import pytest

import huggingface_hub.constants as hf_constants

from app.core.download_engine import DownloadEngine
from app.core.errors import KIND_LOCAL, KIND_NETWORK, translate_exception
from app.core.queue_store import QueueStore
from app.models import BatchSpec, DownloadTask, RepoFile, TaskState
from tests.fake_hf_server import FakeHFServer, FakeRepo

server_endpoint = ""


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch):
    monkeypatch.setattr(hf_constants, "DOWNLOAD_CHUNK_SIZE", 128 * 1024)


@pytest.fixture
def server():
    srv = FakeHFServer().start()
    globals()["server_endpoint"] = srv.endpoint
    yield srv
    srv.stop()


def make_engine(token=None, workers=4):
    return DownloadEngine(
        token_provider=(lambda: token) if token else (lambda: None),
        max_workers=workers,
        max_retries=0,
        retry_base_delay=0.1,
    )


def wait_for(engine, cond, timeout=30.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snaps = engine.snapshot()
        if cond(snaps):
            return snaps
        time.sleep(interval)
    raise AssertionError(f"等待超时: {[(s['filename'], s['state'].value) for s in engine.snapshot()]}")


# ---------------------------------------------------------------- 队列持久化


def test_queue_store_roundtrip(tmp_path):
    store = QueueStore(tmp_path / "queue.json")
    assert store.load() == []
    batches = [
        {
            "batch_id": "abc123",
            "repo_id": "org/repo",
            "revision": "main",
            "mode": "local_dir",
            "local_dir": r"E:\LmModels\repo",
            "cache_dir": None,
            "endpoint": "https://hf-mirror.net",
            "files": [
                {"path": "a.gguf", "size": 10, "lfs_sha256": "x" * 64, "oid": None,
                 "done": False, "bytes_done": 4, "state": "已暂停"},
                {"path": "b.json", "size": 2, "lfs_sha256": None, "oid": "y" * 40,
                 "done": True, "bytes_done": 0, "state": "已完成"},
            ],
        }
    ]
    store.save(batches)
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0]["repo_id"] == "org/repo"
    assert loaded[0]["files"][0]["bytes_done"] == 4
    assert loaded[0]["files"][1]["done"] is True
    store.clear()
    assert store.load() == []
    # 空队列保存 = 清除文件
    store.save(batches)
    store.save([])
    assert store.load() == []


def test_queue_store_rejects_corrupt(tmp_path):
    p = tmp_path / "queue.json"
    p.write_text("{not json", encoding="utf-8")
    assert QueueStore(p).load() == []
    p.write_text('{"batches": [{"files": "bad"}]}', encoding="utf-8")
    assert QueueStore(p).load() == []


# ---------------------------------------------------------------- 引擎导出/指纹/切换端点/暂停排队任务


def test_engine_export_and_fingerprint(server, tmp_path):
    repo = server.add_repo(FakeRepo("q-org/q-model"))
    repo.add_file("a.bin", b"A" * 300_000)
    repo.add_file("b.bin", b"B" * 300_000)
    engine = make_engine()
    engine.submit(
        BatchSpec(repo_id=repo.repo_id, revision="main",
                  files=[RepoFile(path="a.bin", size=300_000), RepoFile(path="b.bin", size=300_000)],
                  mode="local_dir", local_dir=str(tmp_path / "out"), endpoint=server.endpoint)
    )
    wait_for(engine, lambda s: all(t["state"] is TaskState.COMPLETED for t in s))
    assert engine.export_incomplete_batches() == []  # 全部完成后无待续队列
    fp1 = engine.fingerprint()
    assert engine.fingerprint() == fp1


def test_engine_export_includes_paused_tasks(server, tmp_path):
    repo = server.add_repo(FakeRepo("q2-org/q2-model"))
    repo.add_file("big.bin", b"Z" * 1_200_000)
    server.throttle_seconds = 0.01
    try:
        engine = make_engine(workers=1)
        engine.submit(
            BatchSpec(repo_id=repo.repo_id, revision="main",
                      files=[RepoFile(path="big.bin", size=1_200_000)],
                      mode="local_dir", local_dir=str(tmp_path / "out"), endpoint=server.endpoint)
        )
        tid = engine.snapshot()[0]["task_id"]
        wait_for(engine, lambda s: s[0]["bytes_done"] > 100_000)
        engine.pause(tid)
        wait_for(engine, lambda s: s[0]["state"] is TaskState.PAUSED)
        batches = engine.export_incomplete_batches()
        assert len(batches) == 1
        assert batches[0]["repo_id"] == "q2-org/q2-model"
        assert batches[0]["files"][0]["done"] is False
        assert batches[0]["files"][0]["bytes_done"] > 0
        assert engine.fingerprint() != ""
    finally:
        server.throttle_seconds = 0.0


def test_pause_pending_task_stays_paused(server, tmp_path):
    """并发=1 时排队中的任务被暂停应停在 PAUSED，而不是被标成 CANCELLED。"""
    repo = server.add_repo(FakeRepo("q3-org/q3-model"))
    for i in range(3):
        repo.add_file(f"f{i}.bin", b"Q" * 400_000)
    server.throttle_seconds = 0.01
    try:
        engine = make_engine(workers=1)
        engine.submit(
            BatchSpec(repo_id=repo.repo_id, revision="main",
                      files=[RepoFile(path=f"f{i}.bin", size=400_000) for i in range(3)],
                      mode="local_dir", local_dir=str(tmp_path / "out"), endpoint=server.endpoint)
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            snaps = engine.snapshot()
            if any(s["state"] is TaskState.DOWNLOADING for s in snaps) and \
               any(s["state"] is TaskState.PENDING for s in snaps):
                break
            time.sleep(0.02)
        pending_id = next(s["task_id"] for s in engine.snapshot() if s["state"] is TaskState.PENDING)
        engine.pause(pending_id)
        wait_for(engine, lambda s: next(x for x in s if x["task_id"] == pending_id)["state"] is TaskState.PAUSED)
    finally:
        server.throttle_seconds = 0.0


def test_update_endpoint_applies_to_retry(server, tmp_path):
    """镜像切换后重试失败任务应使用新端点。"""
    # 服务器 A：需要 Token → 失败
    srv_a = FakeHFServer().start()
    try:
        gated = FakeRepo("sw-org/sw-model", require_token=True)
        gated.add_file("config.json", b"{}")
        srv_a.add_repo(gated)

        # 服务器 B：同仓库、无需 Token → 成功
        open_repo = FakeRepo("sw-org/sw-model")
        open_repo.add_file("config.json", b"{}")
        server.add_repo(open_repo)

        engine = make_engine()
        engine.submit(
            BatchSpec(repo_id="sw-org/sw-model", revision="main",
                      files=[RepoFile(path="config.json", size=2)],
                      mode="local_dir", local_dir=str(tmp_path / "out"), endpoint=srv_a.endpoint)
        )
        snaps = wait_for(engine, lambda s: s[0]["state"] is TaskState.FAILED)
        assert snaps[0]["error_kind"] == "auth"

        engine.update_endpoint(server.endpoint)
        tid = snaps[0]["task_id"]
        task = engine._tasks[tid]
        assert task.endpoint == server.endpoint
        engine.retry(tid)
        wait_for(engine, lambda s: s[0]["state"] is TaskState.COMPLETED)
        assert (tmp_path / "out" / "config.json").read_bytes() == b"{}"
    finally:
        srv_a.stop()


# ---------------------------------------------------------------- 续传进度基线


def test_resume_baseline():
    task = DownloadTask(
        repo_id="o/r", revision="main", filename="f.bin", size=1000,
        mode="local_dir", local_dir="x", cache_dir=None, endpoint="e", batch_id="b",
    )
    assert task.bytes_done == 0
    task.set_resume_baseline(600)
    assert task.bytes_done == 600
    task.note_bytes(50)
    assert task.bytes_done == 650
    # 更小的基线不回退
    task.set_resume_baseline(100)
    assert task.bytes_done == 650


# ---------------------------------------------------------------- 错误翻译：httpx / 网络 OSError


def test_translate_httpx_connect_error():
    import httpx2

    fe = translate_exception(httpx2.ConnectError("connection refused"), "https://hf-mirror.net")
    assert fe.kind == KIND_NETWORK
    assert "网络" in fe.message


def test_translate_httpx_read_error():
    import httpx2

    fe = translate_exception(httpx2.RemoteProtocolError("peer closed"))
    assert fe.kind == KIND_NETWORK
    assert "中断" in fe.message or "续传" in fe.message


def test_translate_connection_reset_is_network_not_local():
    exc = OSError(10054, "远程主机强迫关闭了一个现有的连接。")
    exc.errno = 10054
    fe = translate_exception(exc)
    assert fe.kind == KIND_NETWORK, f"应为网络错误，实际: {fe.message}"
    assert "重置" in fe.message


def test_translate_disk_full_still_local():
    exc = OSError(28, "No space left on device")
    exc.errno = 28
    exc.winerror = 112
    fe = translate_exception(exc)
    assert fe.kind == KIND_LOCAL
    assert "磁盘空间不足" in fe.message


# ---------------------------------------------------------------- 模糊搜索（无作者）


def test_search_models_via_fake(server):
    from app.core.hf_client import search_models

    a = server.add_repo(FakeRepo("search-org/alpha-model"))
    a.add_file("config.json", b"{}")
    server.add_repo(FakeRepo("other/beta-model")).add_file("config.json", b"{}")
    results = search_models(server.endpoint, "alpha", limit=10)
    ids = [r["repo_id"] for r in results]
    assert ids == ["search-org/alpha-model"]
    assert results[0]["downloads"] == 1000


def test_search_models_empty_query_lists_all(server):
    from app.core.hf_client import search_models

    server.add_repo(FakeRepo("s2-org/m1")).add_file("a", b"x")
    results = search_models(server.endpoint, "", limit=10)
    assert len(results) >= 1
