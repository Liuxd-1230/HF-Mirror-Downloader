"""历史记录页。"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.formatting import human_size
from ..models import BatchSpec, RepoFile
from .common import open_in_explorer

logger = logging.getLogger(__name__)

STATUS_TEXT = {"completed": "✅ 成功", "failed": "❌ 失败", "canceled": "⏹ 已取消"}


class HistoryPage(QWidget):
    re_download = Signal(dict)  # 历史行 dict → MainWindow 转为下载任务

    def __init__(self, history_store, settings_service, parent=None):
        super().__init__(parent)
        self.history_store = history_store
        self.settings_service = settings_service

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("按仓库名 / 文件名 / 路径过滤…")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self.refresh)
        bar.addWidget(self.search, 1)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(btn_refresh)
        btn_clear = QPushButton("清空历史")
        btn_clear.setProperty("cssClass", "danger")
        btn_clear.clicked.connect(self._clear)
        bar.addWidget(btn_clear)
        lay.addLayout(bar)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels(["时间", "仓库", "文件", "大小", "状态", "镜像", "本地位置"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(6, QHeaderView.Stretch)
        self.tree.header().resizeSection(0, 150)
        self.tree.header().resizeSection(3, 90)
        self.tree.itemDoubleClicked.connect(lambda *_: self.open_selected())
        lay.addWidget(self.tree, 1)

        act_row = QHBoxLayout()
        tip = QLabel("双击行 = 打开所在目录")
        tip.setObjectName("mutedLabel")
        act_row.addWidget(tip, 1)
        b_folder = QPushButton("打开所在目录")
        b_folder.clicked.connect(self.open_selected)
        act_row.addWidget(b_folder)
        b_copy = QPushButton("复制路径")
        b_copy.clicked.connect(self.copy_path)
        act_row.addWidget(b_copy)
        b_redo = QPushButton("重新下载")
        b_redo.setProperty("cssClass", "primary")
        b_redo.clicked.connect(self.re_download_selected)
        act_row.addWidget(b_redo)
        lay.addLayout(act_row)

        self.refresh()

    def refresh(self):
        rows = self.history_store.query(self.search.text())
        self.tree.clear()
        for r in rows:
            item = QTreeWidgetItem([
                r["created_at"],
                r["repo_id"],
                r["filename"],
                human_size(r["size"]) if r["size"] else "—",
                STATUS_TEXT.get(r["status"], r["status"]),
                r["mirror"] or "—",
                r["local_path"] or (r["error"] or ""),
            ])
            item.setData(0, Qt.UserRole, r)
            item.setToolTip(6, r["error"] or r["local_path"] or "")
            self.tree.addTopLevelItem(item)
        if not rows:
            tip = QTreeWidgetItem(["暂无历史记录", "", "", "", "", "", ""])
            tip.setFlags(Qt.NoItemFlags)
            self.tree.addTopLevelItem(tip)

    def _selected_row(self) -> dict | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def open_selected(self):
        row = self._selected_row()
        if not row:
            return
        path = row["local_path"]
        if path:
            open_in_explorer(path)
        else:
            QMessageBox.information(self, "提示", "该记录没有本地路径（可能未成功下载）。")

    def copy_path(self):
        from PySide6.QtGui import QGuiApplication

        row = self._selected_row()
        if row and row["local_path"]:
            QGuiApplication.clipboard().setText(os.path.abspath(row["local_path"]))

    def re_download_selected(self):
        row = self._selected_row()
        if not row:
            return
        s = self.settings_service.settings
        spec = BatchSpec(
            repo_id=row["repo_id"],
            revision=row["revision"] or "main",
            files=[RepoFile(path=row["filename"], size=row["size"])],
            mode=s.download_mode,
            local_dir=s.local_dir if s.download_mode == "local_dir" else None,
            cache_dir=s.cache_dir if s.download_mode == "cache" else None,
            endpoint=s.endpoint,
        )
        self.re_download.emit(spec)

    def _clear(self):
        ret = QMessageBox.question(self, "清空历史", "确定要删除全部下载历史吗？（不影响已下载的文件）")
        if ret == QMessageBox.Yes:
            self.history_store.clear()
            self.refresh()
