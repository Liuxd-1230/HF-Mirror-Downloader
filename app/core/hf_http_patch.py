"""huggingface_hub 2.x 的 HTTP client 线程隔离补丁。

背景：hf 2.0 的 get_session() 返回全局共享的 httpx2.Client。多个下载线程
并发流式读时，Windows 上偶发 WSAENOTSOCK（WinError 10038，操作非套接字）。
本补丁把全局 client 换成"线程路由代理"：每个线程首次使用时通过官方
default_client_factory() 懒建自己的 client，互不共享套接字。

对上游的依赖面：仅 _http._GLOBAL_CLIENT / _http.default_client_factory，
hf 升级后如结构变化，apply() 会静默跳过（保持原行为）。
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_applied = False


class _ThreadLocalClientProxy:
    """把对共享 client 的访问路由到当前线程自己的 client。"""

    def __init__(self, factory):
        self._factory = factory
        self._tls = threading.local()

    def _real(self):
        client = getattr(self._tls, "client", None)
        if client is None:
            client = self._tls.client = self._factory()
            logger.debug("为线程 %s 创建独立 httpx2 client", threading.current_thread().name)
        return client

    def __getattr__(self, name):
        return getattr(self._real(), name)

    def __enter__(self):
        return self._real()

    def __exit__(self, *exc):
        return False


def apply() -> None:
    """安装线程隔离补丁（幂等，失败不抛出）。"""
    global _applied
    if _applied:
        return
    try:
        from huggingface_hub.utils import _http

        if not hasattr(_http, "_GLOBAL_CLIENT"):
            logger.debug("hf _http 无 _GLOBAL_CLIENT，跳过线程隔离补丁")
            return
        _http._GLOBAL_CLIENT = _ThreadLocalClientProxy(_http.default_client_factory)
        _applied = True
        logger.info("已启用 httpx2 client 线程隔离（修复并发下载 WinError 10038）")
    except Exception as exc:  # noqa: BLE001
        logger.warning("httpx2 线程隔离补丁安装失败（继续使用默认行为）：%s", exc)
