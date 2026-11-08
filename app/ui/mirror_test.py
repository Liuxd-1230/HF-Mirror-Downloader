"""镜像测试的后台线程（下载页与设置页共用，避免在 UI 线程发网络请求）。"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class MirrorTestWorker(QThread):
    done = Signal(dict)  # {ok, latency_ms, status, message, endpoint}

    def __init__(self, endpoint: str, parent=None):
        super().__init__(parent)
        self.endpoint = endpoint

    def run(self):
        from ..core.hf_client import test_mirror

        self.done.emit(test_mirror(self.endpoint))


def mirror_label(endpoint: str) -> str:
    """镜像的显示名：HF-Mirror / Hugging Face / 自定义源。"""
    host = endpoint.split("//")[-1].strip("/")
    if "hf-mirror" in host:
        return "HF-Mirror"
    if "huggingface" in host:
        return "Hugging Face"
    return "自定义源"


MIRROR_PRESETS = [
    "https://hf-mirror.net",
    "https://hf-mirror.com",
    "https://huggingface.co",
]
