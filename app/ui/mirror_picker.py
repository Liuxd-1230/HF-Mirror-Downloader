"""镜像切换对话框：并行测试预设镜像 → 选择 → 应用到引擎与设置。"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from .mirror_test import MIRROR_PRESETS, MirrorTestWorker

logger = logging.getLogger(__name__)


class MirrorPickerDialog(QDialog):
    def __init__(self, current_endpoint: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("切换下载源")
        self.setMinimumWidth(520)
        self.chosen_endpoint: str | None = None
        self._workers: list[MirrorTestWorker] = []
        self._radios: dict[str, QRadioButton] = {}
        self._status: dict[str, QLabel] = {}

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        tip = QLabel("并行测试各下载源的连通性与延迟，选择一个可用的。切换只影响之后的下载，已完成文件不受影响。")
        tip.setObjectName("mutedLabel")
        lay.addWidget(tip)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.auto_retry = QCheckBox("切换后自动重试失败的任务")
        self.auto_retry.setChecked(True)

        for i, ep in enumerate(MIRROR_PRESETS):
            radio = QRadioButton(ep)
            radio.setChecked(ep.rstrip("/") == current_endpoint.rstrip("/"))
            status = QLabel("测试中…")
            status.setObjectName("mutedLabel")
            form.addRow(radio, status)
            self._radios[ep] = radio
            self._status[ep] = status
            w = MirrorTestWorker(ep)
            w.done.connect(lambda res, _ep=ep: self._on_tested(_ep, res))
            self._workers.append(w)
        lay.addLayout(form)
        lay.addWidget(self.auto_retry)

        row = QHBoxLayout()
        row.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        b_ok = QPushButton("切换")
        b_ok.setProperty("cssClass", "primary")
        b_ok.clicked.connect(self._on_accept)
        row.addWidget(b_cancel)
        row.addWidget(b_ok)
        lay.addLayout(row)

        for w in self._workers:
            w.start()

    def _on_tested(self, endpoint: str, result: dict):
        label = self._status.get(endpoint)
        if label is None:
            return
        if result["ok"]:
            label.setText(f"✅ 延迟 {result['latency_ms']:.0f} ms")
            label.setObjectName("okLabel")
        else:
            label.setText(f"❌ HTTP {result['status'] or '—'} · {result['message']}")
            label.setObjectName("errLabel")
        label.style().unpolish(label)
        label.style().polish(label)

    def _on_accept(self):
        for ep, radio in self._radios.items():
            if radio.isChecked():
                self.chosen_endpoint = ep
                break
        if self.chosen_endpoint is None:
            return
        self.accept()

    def closeEvent(self, event):
        for w in self._workers:
            if w.isRunning():
                w.wait(200)
        super().closeEvent(event)
