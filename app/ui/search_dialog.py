"""模型搜索对话框：不知道作者时按关键词模糊搜索。

输入 "qwen3 4b" 之类的关键词 → 列出镜像上的匹配模型（按下载量排序），
支持二次过滤，双击或点「选择」回到下载页自动解析。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import icons

logger = logging.getLogger(__name__)


class ModelSearchDialog(QWidget):
    """非模态搜索结果窗（由 DownloadPage 持有并填充结果）。"""

    repo_picked = Signal(str)  # 选中 repo_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.Window)  # 独立顶层窗口（否则会嵌在主窗口里叠字）
        self.setWindowTitle("模型搜索")
        self.resize(760, 520)
        self._results: list[dict] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        tip = QLabel("关键词不含“作者/仓库名”格式时，会自动按下载量搜索匹配的模型。双击结果或点下方按钮开始解析。")
        tip.setObjectName("mutedLabel")
        lay.addWidget(tip)

        row = QHBoxLayout()
        self.query_label = QLabel("关键词：—")
        row.addWidget(self.query_label, 1)
        self.status_label = QLabel("")
        self.status_label.setObjectName("mutedLabel")
        row.addWidget(self.status_label)
        lay.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["模型", "下载量", "赞", "任务类型"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().resizeSection(1, 110)
        self.tree.header().resizeSection(2, 70)
        self.tree.header().resizeSection(3, 130)
        self.tree.itemDoubleClicked.connect(self._pick_item)
        lay.addWidget(self.tree, 1)

        bottom = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("在结果中再过滤…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_local_filter)
        bottom.addWidget(self.filter_edit, 1)
        b_ok = QPushButton(" 解析并查看文件")
        b_ok.setIcon(icons.icon(icons.SEARCH, "#ffffff"))
        b_ok.setProperty("cssClass", "primary")
        b_ok.clicked.connect(self._pick_current)
        bottom.addWidget(b_ok)
        lay.addLayout(bottom)

    # ---------------------------------------------------------------- 数据

    def begin(self, query: str):
        self.query_label.setText(f"关键词：{query}")
        self.status_label.setText("搜索中…")
        self.tree.clear()
        self._results = []
        self.show()
        self.raise_()
        self.activateWindow()

    def set_results(self, results: list[dict]):
        self._results = results
        self.tree.clear()
        for r in results:
            item = QTreeWidgetItem([
                r["repo_id"],
                _fmt_downloads(r.get("downloads")),
                str(r.get("likes") or 0),
                r.get("pipeline_tag") or "—",
            ])
            item.setData(0, Qt.UserRole, r)
            item.setToolTip(0, " · ".join(r.get("tags") or []))
            self.tree.addTopLevelItem(item)
        self.status_label.setText(f"{len(results)} 个结果" if results else "没有匹配的模型")
        self.filter_edit.clear()
        if results:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
            self.tree.setFocus()

    def set_failed(self, message: str):
        self.status_label.setText(f"❌ {message}")

    # ---------------------------------------------------------------- 交互

    def _apply_local_filter(self, text: str):
        text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            it.setHidden(bool(text) and text not in it.text(0).lower())

    def _pick_item(self, item, _col):
        self.repo_picked.emit(item.data(0, Qt.UserRole)["repo_id"])
        self.hide()

    def _pick_current(self):
        item = self.tree.currentItem()
        if item is not None:
            self._pick_item(item, 0)


def _fmt_downloads(n) -> str:
    if not n:
        return "—"
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f} 亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f} 万"
    return str(n)
