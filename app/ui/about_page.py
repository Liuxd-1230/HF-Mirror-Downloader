"""关于页。"""

from __future__ import annotations

import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from .. import APP_VERSION
from ..services.paths import logs_dir


class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        title = QLabel("HF Mirror Downloader")
        title.setObjectName("sidebarTitle")
        lay.addWidget(title)
        ver = QLabel(f"版本 {APP_VERSION}")
        ver.setObjectName("mutedLabel")
        lay.addWidget(ver)

        desc = QLabel(
            "为 Hugging Face 模型下载提供简单易用的图形界面，默认通过 hf-mirror 镜像下载，\n"
            "支持断点续传、并发下载、HF 官方缓存结构、下载历史与等价命令预览。\n\n"
            "底层复用 huggingface_hub 官方库：缓存结构、断点续传、大文件处理与官方工具完全兼容。"
        )
        lay.addWidget(desc)

        links = QVBoxLayout()
        for text, url in [
            ("hf-mirror（镜像）", "https://hf-mirror.net"),
            ("Hugging Face", "https://huggingface.co"),
            ("huggingface_hub 文档", "https://huggingface.co/docs/huggingface_hub"),
        ]:
            b = QPushButton(text)
            b.setProperty("cssClass", "flat")
            b.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            b.setMaximumWidth(260)
            links.addWidget(b)
        lay.addLayout(links)

        lay.addStretch(1)

        b_log = QPushButton("打开日志文件夹")
        b_log.clicked.connect(lambda: os.startfile(logs_dir()))  # noqa: S606
        b_log.setMaximumWidth(200)
        lay.addWidget(b_log)

        copyright_ = QLabel("本项目仅供学习交流使用，模型文件版权归原发布者所有。")
        copyright_.setObjectName("mutedLabel")
        lay.addWidget(copyright_)
