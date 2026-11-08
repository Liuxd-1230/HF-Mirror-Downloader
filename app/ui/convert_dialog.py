"""链接转换小工具：官方链接 ↔ 镜像直链，blob 自动转 resolve。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ..core.url_parser import parse_input


class ConvertDialog(QDialog):
    def __init__(self, mirror_endpoint: str, parent=None):
        super().__init__(parent)
        self.mirror = mirror_endpoint.rstrip("/")
        self.setWindowTitle("链接转换")
        self.setMinimumWidth(680)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        tip = QLabel(
            "把 Hugging Face 官方链接转换为镜像直链。\n"
            "支持仓库链接、blob 链接（自动转 resolve）、resolve 链接；也可反向转换。"
        )
        tip.setObjectName("mutedLabel")
        lay.addWidget(tip)

        lay.addWidget(QLabel("输入链接："))
        self.input = QLineEdit()
        self.input.setPlaceholderText("https://huggingface.co/xxx/xxx/resolve/main/model.gguf")
        self.input.textChanged.connect(self._convert)
        lay.addWidget(self.input)

        lay.addWidget(QLabel("镜像直链："))
        row1 = QHBoxLayout()
        self.mirror_out = QLineEdit()
        self.mirror_out.setReadOnly(True)
        row1.addWidget(self.mirror_out, 1)
        b1 = QPushButton("复制")
        b1.clicked.connect(lambda: self._copy(self.mirror_out.text()))
        row1.addWidget(b1)
        b1b = QPushButton("浏览器打开")
        b1b.clicked.connect(lambda: self._open(self.mirror_out.text()))
        row1.addWidget(b1b)
        lay.addLayout(row1)

        lay.addWidget(QLabel("官方链接："))
        row2 = QHBoxLayout()
        self.official_out = QLineEdit()
        self.official_out.setReadOnly(True)
        row2.addWidget(self.official_out, 1)
        b2 = QPushButton("复制")
        b2.clicked.connect(lambda: self._copy(self.official_out.text()))
        row2.addWidget(b2)
        b2b = QPushButton("浏览器打开")
        b2b.clicked.connect(lambda: self._open(self.official_out.text()))
        row2.addWidget(b2b)
        lay.addLayout(row2)

        self.status = QLabel("")
        self.status.setObjectName("mutedLabel")
        lay.addWidget(self.status)

        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        row3 = QHBoxLayout()
        row3.addStretch(1)
        row3.addWidget(close)
        lay.addLayout(row3)

    def _convert(self):
        text = self.input.text().strip()
        if not text:
            self.mirror_out.clear()
            self.official_out.clear()
            self.status.clear()
            return
        try:
            p = parse_input(text)
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"❌ 无法解析：{exc}")
            return
        self.status.setText("✅ 解析成功：仓库 " + p.repo_id + (f"，文件 {p.filename}" if p.is_file else ""))
        if p.is_file:
            official = f"https://huggingface.co/{p.repo_id}/resolve/{p.revision}/{p.filename}"
            mirror = f"{self.mirror}/{p.repo_id}/resolve/{p.revision}/{p.filename}"
        else:
            official = f"https://huggingface.co/{p.repo_id}"
            mirror = f"{self.mirror}/{p.repo_id}"
        self.official_out.setText(official)
        self.mirror_out.setText(mirror)

    def _copy(self, text: str):
        if text:
            QGuiApplication.clipboard().setText(text)

    def _open(self, text: str):
        if text:
            QDesktopServices.openUrl(QUrl(text))
