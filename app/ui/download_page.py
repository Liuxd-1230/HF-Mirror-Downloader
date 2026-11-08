"""下载页：输入解析 → 文件列表筛选 → 选择 → 下载进度 → 完成操作。"""

from __future__ import annotations

import fnmatch
import logging
import os
import re

from PySide6.QtCore import QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import cache_utils
from ..core.errors import KIND_AUTH, KIND_NETWORK
from ..core.formatting import format_eta, human_size, human_speed
from ..core.url_parser import ParseError, parse_input
from ..models import AppSettings, BatchSpec, RepoFile, TaskState
from . import icons
from .common import disk_free, open_in_explorer, open_path

logger = logging.getLogger(__name__)

QUICK_FILTERS = ["全部", "GGUF", "Safetensors", "LiteRT", "ZIP", "配置文件"]


class _ListWorker(QThread):
    """后台获取仓库文件列表。"""

    done = Signal(str, str, object)   # repo_id, revision, list[RepoFile]（携带请求标识，防竞态）
    failed = Signal(str, str, str, str)  # repo_id, revision, 中文消息, kind

    def __init__(self, endpoint: str, repo_id: str, revision: str, token: str | None):
        super().__init__()
        self.endpoint = endpoint
        self.repo_id = repo_id
        self.revision = revision
        self.token = token

    def run(self):
        from ..core.errors import translate_exception
        from ..core.hf_client import list_repo_files_full

        try:
            files = list_repo_files_full(self.endpoint, self.repo_id, self.revision, self.token)
            self.done.emit(self.repo_id, self.revision, files)
        except Exception as exc:  # noqa: BLE001
            fe = translate_exception(exc, self.endpoint)
            self.failed.emit(self.repo_id, self.revision, fe.message, fe.kind)


class _SearchWorker(QThread):
    """按关键词模糊搜索模型（无需知道作者）。"""

    done = Signal(str, list)   # query, results
    failed = Signal(str, str)  # query, 中文消息

    def __init__(self, endpoint: str, query: str, token: str | None):
        super().__init__()
        self.endpoint = endpoint
        self.query = query
        self.token = token

    def run(self):
        from ..core.errors import translate_exception
        from ..core.hf_client import search_models

        try:
            results = search_models(self.endpoint, self.query, limit=24, token=self.token)
            self.done.emit(self.query, results)
        except Exception as exc:  # noqa: BLE001
            fe = translate_exception(exc, self.endpoint)
            self.failed.emit(self.query, fe.message)


class FileListPanel(QWidget):
    """文件列表 + 筛选工具条。"""

    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # 筛选工具条（两行）：行1 = 类型 chips + 批量选择；行2 = 过滤输入框
        bar = QHBoxLayout()
        self._type_group = QButtonGroup(self)
        self._type_group.setExclusive(True)
        for i, name in enumerate(QUICK_FILTERS):
            btn = QPushButton(name)
            btn.setProperty("cssClass", "chip")
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.clicked.connect(self._apply_filters)
            self._type_group.addButton(btn, i)
            bar.addWidget(btn)
        lay.addLayout(bar)

        filter_row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("文件名过滤：子串或 glob，如 PTQ1_0、*Q4_K_M*.gguf（多个用空格/逗号分隔）")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._schedule_filter)
        filter_row.addWidget(self.filter_edit, 1)
        btn_all = QPushButton("全选")
        btn_none = QPushButton("全不选")
        btn_invert = QPushButton("反选")
        for b in (btn_all, btn_none, btn_invert):
            b.setProperty("cssClass", "flat")
        for b, fn in ((btn_all, self.select_all), (btn_none, self.select_none), (btn_invert, self.invert_selection)):
            b.clicked.connect(fn)
            filter_row.addWidget(b)
        lay.addLayout(filter_row)

        # 文件树
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["文件名", "大小", "类型", "路径"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree.header().resizeSection(1, 100)
        self.tree.header().resizeSection(2, 90)
        self.tree.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.tree, 1)

        self._loading = False
        self._suppress_check_signal = False
        # 过滤防抖：停止输入 180ms 后才真正过滤，避免每敲一个字符全表重算
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(180)
        self._filter_timer.timeout.connect(self._run_filters)

    # ---------- 数据 ----------

    def set_files(self, files: list[RepoFile], preselect: str = "", downloaded: dict[str, str] | None = None) -> None:
        # 填充期间抑制逐行 itemChanged 信号，否则每行都会触发全量统计 → O(n²)
        self._suppress_check_signal = True
        self.tree.setUpdatesEnabled(False)
        downloaded = downloaded or {}
        try:
            self.tree.setSortingEnabled(False)
            self.tree.clear()
            preselect = preselect.lower()
            for f in files:
                item = QTreeWidgetItem([f.name, human_size(f.size), f.category, f.path])
                item.setData(0, Qt.UserRole, f)
                item.setData(0, Qt.UserRole + 2, f.path.lower())  # 预计算，过滤时不再 lower()
                item.setData(0, Qt.UserRole + 3, f.category)       # 预计算类型，过滤时不再重算
                item.setData(1, Qt.UserRole + 1, f.size if f.size is not None else -1)
                item.setToolTip(3, f.path)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                if f.path in downloaded:
                    # “已下载”标记：类型列追加文案（过滤用类型存在 UserRole+3，不受显示影响）
                    item.setText(2, f"{f.category} · ✅已下载")
                    item.setToolTip(0, f"已下载过：{downloaded[f.path]}")
                if preselect and (f.path.lower() == preselect or f.path.lower().endswith("/" + preselect)):
                    item.setCheckState(0, Qt.Checked)
                else:
                    item.setCheckState(0, Qt.Unchecked)
                self.tree.addTopLevelItem(item)
            self.tree.setSortingEnabled(True)
            self._run_filters()
        finally:
            self.tree.setUpdatesEnabled(True)
            self._suppress_check_signal = False
        self.selection_changed.emit()

    def clear_files(self) -> None:
        self.tree.clear()
        self.selection_changed.emit()

    def checked_files(self) -> list[RepoFile]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) == Qt.Checked:
                out.append(it.data(0, Qt.UserRole))
        return out

    def checked_stats(self) -> tuple[int, int]:
        """(已勾选数量, 总字节)"""
        files = self.checked_files()
        return len(files), sum(f.size or 0 for f in files)

    def visible_items(self) -> list[QTreeWidgetItem]:
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())
                if not self.tree.topLevelItem(i).isHidden()]

    # ---------- 筛选与选择 ----------

    def _apply_filters(self, *_):
        self._run_filters()

    def _schedule_filter(self, *_):
        self._filter_timer.start()

    def _run_filters(self):
        type_id = self._type_group.checkedId()
        type_name = QUICK_FILTERS[type_id] if type_id >= 0 else "全部"
        raw = self.filter_edit.text().strip()
        # 每个模式只编译一次
        pats = []
        for p in (re.split(r"[,\s;，；]+", raw) if raw else []):
            pl = p.lower()
            if any(c in p for c in "*?["):
                pats.append(("glob", pl, re.compile(fnmatch.translate(pl))))
            else:
                pats.append(("sub", pl, None))
        want_type = type_name == "全部"
        # 批量隐藏时关闭重绘，避免逐行触发视口更新
        self.tree.setUpdatesEnabled(False)
        try:
            for i in range(self.tree.topLevelItemCount()):
                it = self.tree.topLevelItem(i)
                ok = want_type or it.data(0, Qt.UserRole + 3) == type_name
                if ok and pats:
                    path = it.data(0, Qt.UserRole + 2)
                    name = path.rsplit("/", 1)[-1]
                    ok = any(
                        (kind == "sub" and sub in path)
                        or (kind == "glob" and (rx.match(path) or rx.match(name)))
                        for kind, sub, rx in pats
                    )
                it.setHidden(not ok)
        finally:
            self.tree.setUpdatesEnabled(True)

    def select_all(self):
        self._set_visible_checks(Qt.Checked)

    def select_none(self):
        self._set_visible_checks(Qt.Unchecked)

    def invert_selection(self):
        self._suppress_check_signal = True
        self.tree.setUpdatesEnabled(False)
        try:
            for it in self.visible_items():
                it.setCheckState(0, Qt.Unchecked if it.checkState(0) == Qt.Checked else Qt.Checked)
        finally:
            self.tree.setUpdatesEnabled(True)
            self._suppress_check_signal = False
        self.selection_changed.emit()

    def _set_visible_checks(self, state: Qt.CheckState):
        self._suppress_check_signal = True
        self.tree.setUpdatesEnabled(False)
        try:
            for it in self.visible_items():
                it.setCheckState(0, state)
        finally:
            self.tree.setUpdatesEnabled(True)
            self._suppress_check_signal = False
        self.selection_changed.emit()

    def _on_item_changed(self, _item):
        if not self._suppress_check_signal:
            self.selection_changed.emit()


def task_palette(theme: str) -> dict:
    """任务行委托用的颜色（深/浅两套）。"""
    if theme == "light":
        return {
            "text": QColor("#22262b"), "muted": QColor("#6b7280"),
            "track": QColor("#e2e5ea"), "name": QColor("#22262b"),
            "accent": QColor("#2f6be0"), "ok": QColor("#178a56"),
            "warn": QColor("#b4762a"), "err": QColor("#c93a3a"),
            "queue": QColor("#8a929c"), "pill_bg_alpha": 34,
        }
    return {
        "text": QColor("#e4e6eb"), "muted": QColor("#97a0ab"),
        "track": QColor("#2a2e37"), "name": QColor("#e9ebf0"),
        "accent": QColor("#5b9cf0"), "ok": QColor("#3ecf8e"),
        "warn": QColor("#f2b04c"), "err": QColor("#ef5f5f"),
        "queue": QColor("#8b93a1"), "pill_bg_alpha": 40,
    }


PILL_KIND_COLOR = {"run": "accent", "ok": "ok", "warn": "warn", "err": "err", "queue": "queue"}


class TaskItemDelegate(QStyledItemDelegate):
    """任务表绘制：col0 = 文件名+元信息两行；col1 = 状态胶囊；col2 = 迷你进度条。

    数据约定（存在 item 数据里，绘制无状态）：
      text(0)=文件名；data(0,UserRole+5)=第二行元信息
      text(1)=状态文案；data(1,UserRole+6)=胶囊类型 run/ok/warn/err/queue
      data(2,UserRole+4)=进度百分比 float（-1 表示不绘制条）
    """

    ROW_HEIGHT = 52

    def __init__(self, parent=None):
        super().__init__(parent)
        self.colors = task_palette("dark")

    def set_theme(self, theme: str):
        self.colors = task_palette(theme)

    def sizeHint(self, option, index):
        if index.column() == 0:
            return QSize(option.rect.width(), self.ROW_HEIGHT)
        return QSize(88 if index.column() == 1 else 150, self.ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect
        col = index.column()
        if col == 0:
            name = index.data(Qt.DisplayRole) or ""
            meta = index.data(Qt.UserRole + 5) or ""
            r = rect.adjusted(12, 0, -8, 0)
            f = painter.font()
            f.setPixelSize(13)
            painter.setFont(f)
            painter.setPen(self.colors["name"])
            name_rect = QRect(r.x(), r.y() + 8, r.width(), 18)
            painter.drawText(name_rect, Qt.AlignVCenter, painter.fontMetrics().elidedText(name, Qt.ElideMiddle, name_rect.width()))
            if meta:
                f.setPixelSize(11)
                painter.setFont(f)
                painter.setPen(self.colors["muted"])
                meta_rect = QRect(r.x(), r.y() + 28, r.width(), 15)
                painter.drawText(meta_rect, Qt.AlignVCenter, painter.fontMetrics().elidedText(meta, Qt.ElideMiddle, meta_rect.width()))
        elif col == 1:
            label = index.data(Qt.DisplayRole) or ""
            kind = index.data(Qt.UserRole + 6) or "queue"
            color = self.colors[PILL_KIND_COLOR.get(kind, "queue")]
            fm = painter.fontMetrics()
            w = max(fm.horizontalAdvance(label) + 22, 56)
            pill = QRect(rect.x() + (rect.width() - w) // 2, rect.y() + (rect.height() - 22) // 2, w, 22)
            bg = QColor(color)
            bg.setAlpha(self.colors["pill_bg_alpha"])
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(pill, 11, 11)
            f = painter.font()
            f.setPixelSize(11)
            painter.setFont(f)
            painter.setPen(color)
            painter.drawText(pill, Qt.AlignCenter, label)
        elif col == 2:
            pct = index.data(Qt.UserRole + 4)
            if pct is None or pct < 0:
                painter.restore()
                return
            track = QRect(rect.x() + 6, rect.y() + rect.height() // 2 - 4, rect.width() - 12, 8)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.colors["track"])
            painter.drawRoundedRect(track, 4, 4)
            if pct > 0:
                fill = QRect(track.x(), track.y(), int(track.width() * min(pct, 100.0) / 100.0), track.height())
                painter.setBrush(self.colors["accent"])
                painter.drawRoundedRect(fill, 4, 4)
        painter.restore()


class DownloadPage(QWidget):
    start_requested = Signal(BatchSpec)

    def __init__(self, settings_service, token_store, history, parent=None):
        super().__init__(parent)
        self.settings_service = settings_service
        self.token_store = token_store
        self.history = history
        self._parsed = None
        self._list_worker: _ListWorker | None = None
        self._search_worker: _SearchWorker | None = None
        self._search_dialog = None
        self._mirror_worker = None  # 复用 mirror_test.MirrorTestWorker
        self._batch_id: str | None = None
        self._pending_batches: list[dict] = []  # 待恢复队列（由 MainWindow 注入回调）
        self._on_restore_resume = None
        self._on_restore_clear = None
        self.on_mirror_status = None  # MainWindow 注入：镜像健康状态回调 (ok, latency)
        self.engine = None  # 由 MainWindow 注入

        self._build_ui()
        self._update_disk_info()
        self._check_target_dir_banner()
        self.task_delegate.set_theme(self.settings_service.settings.theme)

    # ---------------------------------------------------------------- 小部件工厂

    def _dismiss_button(self, frame) -> QPushButton:
        b = QPushButton("✕")
        b.setProperty("cssClass", "flat")
        b.setFixedWidth(32)
        b.clicked.connect(frame.hide)
        return b

    def _icon_btn(self, icon_name: str, tooltip: str) -> QPushButton:
        b = QPushButton()
        b.setIcon(icons.icon(icon_name))
        b.setFixedSize(30, 30)
        b.setProperty("cssClass", "flat")
        b.setToolTip(tooltip)
        return b

    def apply_theme(self, theme: str):
        self.task_delegate.set_theme(theme)

    # ---------------------------------------------------------------- UI 构建

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # ---- 横幅区（全宽）：恢复队列 / 镜像 / Token / 目录 ----
        self.restore_banner = QFrame()
        self.restore_banner.setObjectName("cardLight")
        self.restore_banner.hide()
        rb = QHBoxLayout(self.restore_banner)
        rb.setContentsMargins(10, 8, 10, 8)
        self.restore_label = QLabel()
        rb.addWidget(self.restore_label, 1)
        b_resume = QPushButton(" 全部续传")
        b_resume.setIcon(icons.icon(icons.PLAY, "#ffffff"))
        b_resume.setProperty("cssClass", "primary")
        b_resume.clicked.connect(self._restore_resume)
        rb.addWidget(b_resume)
        b_restore_clear = QPushButton("清除记录")
        b_restore_clear.clicked.connect(self._restore_clear)
        rb.addWidget(b_restore_clear)
        rb.addWidget(self._dismiss_button(self.restore_banner))
        root.addWidget(self.restore_banner)

        self.net_banner = QFrame()
        self.net_banner.setObjectName("cardLight")
        self.net_banner.hide()
        nb = QHBoxLayout(self.net_banner)
        nb.setContentsMargins(10, 8, 10, 8)
        self.net_label = QLabel()
        nb.addWidget(self.net_label, 1)
        b_switch = QPushButton(" 测试并切换镜像")
        b_switch.setIcon(icons.icon(icons.SWITCH))
        b_switch.clicked.connect(self._open_mirror_picker)
        nb.addWidget(b_switch)
        b_retry_net = QPushButton(" 重试失败项")
        b_retry_net.setIcon(icons.icon(icons.REFRESH))
        b_retry_net.clicked.connect(self._retry_failed)
        nb.addWidget(b_retry_net)
        nb.addWidget(self._dismiss_button(self.net_banner))
        root.addWidget(self.net_banner)

        self.auth_banner = QFrame()
        self.auth_banner.setObjectName("cardLight")
        self.auth_banner.hide()
        ab = QHBoxLayout(self.auth_banner)
        ab.setContentsMargins(10, 8, 10, 8)
        self.auth_label = QLabel()
        ab.addWidget(self.auth_label, 1)
        self.auth_token_edit = QLineEdit()
        self.auth_token_edit.setEchoMode(QLineEdit.Password)
        self.auth_token_edit.setPlaceholderText("hf_xxxxxxxx")
        self.auth_token_edit.setFixedWidth(220)
        ab.addWidget(self.auth_token_edit)
        b_save_retry = QPushButton(" 保存 Token 并重试")
        b_save_retry.setIcon(icons.icon(icons.KEY, "#ffffff"))
        b_save_retry.setProperty("cssClass", "primary")
        b_save_retry.clicked.connect(self._save_token_and_retry)
        ab.addWidget(b_save_retry)
        ab.addWidget(self._dismiss_button(self.auth_banner))
        root.addWidget(self.auth_banner)

        self.dir_banner = QFrame()
        self.dir_banner.setObjectName("cardLight")
        self.dir_banner.hide()
        db_ = QHBoxLayout(self.dir_banner)
        db_.setContentsMargins(10, 8, 10, 8)
        self.dir_label = QLabel()
        self.dir_label.setObjectName("warnLabel")
        db_.addWidget(self.dir_label, 1)
        b_pick_dir = QPushButton("选择目录")
        b_pick_dir.clicked.connect(self._pick_valid_dir)
        db_.addWidget(b_pick_dir)
        db_.addWidget(self._dismiss_button(self.dir_banner))
        root.addWidget(self.dir_banner)

        # ---- 双栏：左=仓库与文件，右=传输 dock ----
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(10)

        # ============ 左：文件 ============
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(8)

        top = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("粘贴仓库/文件链接；或直接输入关键词搜索模型，如 qwen3 4b（无需知道作者）")
        self.input_edit.returnPressed.connect(self.on_parse_clicked)
        top.addWidget(self.input_edit, 1)
        btn_parse = QPushButton(" 解析仓库")
        btn_parse.setIcon(icons.icon(icons.SEARCH, "#ffffff"))
        btn_parse.setProperty("cssClass", "primary")
        btn_parse.clicked.connect(self.on_parse_clicked)
        top.addWidget(btn_parse)
        btn_tool = QPushButton(" 链接转换")
        btn_tool.setIcon(icons.icon(icons.LINK))
        btn_tool.setToolTip("把官方链接转换为镜像直链（blob 自动转 resolve）")
        btn_tool.clicked.connect(self._open_convert_dialog)
        top.addWidget(btn_tool)
        left_l.addLayout(top)

        info_row = QHBoxLayout()
        self.repo_label = QLabel("尚未解析仓库")
        self.repo_label.setObjectName("mutedLabel")
        info_row.addWidget(self.repo_label, 1)
        self.btn_test_mirror = QPushButton(" 测试镜像")
        self.btn_test_mirror.setIcon(icons.icon(icons.SPEED))
        self.btn_test_mirror.setToolTip("检测 DNS / 延迟 / HTTP 状态")
        self.btn_test_mirror.clicked.connect(self.on_test_mirror)
        info_row.addWidget(self.btn_test_mirror)
        left_l.addLayout(info_row)
        self.mirror_result = QLabel("")
        self.mirror_result.setObjectName("mutedLabel")
        self.mirror_result.hide()
        left_l.addWidget(self.mirror_result)

        self.file_panel = FileListPanel()
        self.file_panel.selection_changed.connect(self._on_selection_changed)
        left_l.addWidget(self.file_panel, 1)

        stat_row = QHBoxLayout()
        self.selection_label = QLabel("已选 0 个文件 · 0 B")
        stat_row.addWidget(self.selection_label, 1)
        self.disk_label = QLabel("")
        self.disk_label.setObjectName("mutedLabel")
        self.disk_label.setAlignment(Qt.AlignRight)
        stat_row.addWidget(self.disk_label)
        left_l.addLayout(stat_row)

        self.adv_toggle = QPushButton("高级 ▸ 等价 hf 命令")
        self.adv_toggle.setProperty("cssClass", "flat")
        self.adv_toggle.setCheckable(True)
        self.adv_toggle.toggled.connect(self._toggle_advanced)
        left_l.addWidget(self.adv_toggle)
        self.adv_frame = QFrame()
        self.adv_frame.setObjectName("cardLight")
        adv_lay = QHBoxLayout(self.adv_frame)
        adv_lay.setContentsMargins(10, 8, 10, 8)
        self.command_preview = QLineEdit()
        self.command_preview.setReadOnly(True)
        self.command_preview.setPlaceholderText("解析仓库并选择文件后，这里显示等价的 hf download 命令")
        adv_lay.addWidget(self.command_preview, 1)
        btn_copy_cmd = QPushButton(" 复制命令")
        btn_copy_cmd.setIcon(icons.icon(icons.CLIPBOARD))
        btn_copy_cmd.clicked.connect(lambda: self._copy(self.command_preview.text()))
        adv_lay.addWidget(btn_copy_cmd)
        self.adv_frame.hide()
        left_l.addWidget(self.adv_frame)

        bottom = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("指定目录", "local_dir")
        self.mode_combo.addItem("HF 缓存", "cache")
        s: AppSettings = self.settings_service.settings
        _idx = self.mode_combo.findData(s.download_mode)
        self.mode_combo.setCurrentIndex(_idx if _idx >= 0 else 0)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.mode_combo.setToolTip("指定目录：文件直接放在该目录下；HF 缓存：兼容官方缓存结构")
        bottom.addWidget(self.mode_combo)
        self.dir_edit = QLineEdit(s.local_dir if s.download_mode == "local_dir" else s.cache_dir)
        bottom.addWidget(self.dir_edit, 1)
        self.btn_browse = QPushButton()
        self.btn_browse.setIcon(icons.icon(icons.FOLDER))
        self.btn_browse.setFixedWidth(36)
        self.btn_browse.setToolTip("浏览…")
        self.btn_browse.clicked.connect(self._browse_dir)
        bottom.addWidget(self.btn_browse)
        self.btn_start = QPushButton(" 开始下载")
        self.btn_start.setIcon(icons.icon(icons.DOWNLOAD, "#ffffff"))
        self.btn_start.setProperty("cssClass", "primary")
        self.btn_start.clicked.connect(self.on_start)
        bottom.addWidget(self.btn_start)
        left_l.addLayout(bottom)
        self.splitter.addWidget(left)

        # ============ 右：传输 dock ============
        dock = QWidget()
        dock_l = QVBoxLayout(dock)
        dock_l.setContentsMargins(0, 0, 0, 0)
        dock_l.setSpacing(8)

        dock_head = QHBoxLayout()
        dock_title = QLabel("传输")
        dock_title.setObjectName("sidebarTitle")
        dock_head.addWidget(dock_title)
        self.overall_label = QLabel("暂无任务")
        self.overall_label.setObjectName("mutedLabel")
        self.overall_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        dock_head.addWidget(self.overall_label, 1)
        self.btn_pause_all = self._icon_btn(icons.PAUSE, "全部暂停")
        self.btn_pause_all.clicked.connect(lambda: self.engine and self.engine.pause_all())
        dock_head.addWidget(self.btn_pause_all)
        self.btn_resume_all = self._icon_btn(icons.PLAY, "全部恢复")
        self.btn_resume_all.clicked.connect(lambda: self.engine and self.engine.resume_all())
        dock_head.addWidget(self.btn_resume_all)
        self.btn_cancel_all = self._icon_btn(icons.CLOSE_SM, "取消全部")
        self.btn_cancel_all.clicked.connect(lambda: self.engine and self.engine.cancel_all())
        dock_head.addWidget(self.btn_cancel_all)
        self.btn_retry_failed = self._icon_btn(icons.REFRESH, "重试失败项")
        self.btn_retry_failed.clicked.connect(lambda: self.engine and self.engine.retry_failed())
        dock_head.addWidget(self.btn_retry_failed)
        dock_l.addLayout(dock_head)

        self.overall_bar = QProgressBar()
        self.overall_bar.setObjectName("overallBar")
        self.overall_bar.setRange(0, 1000)
        self.overall_bar.setFixedHeight(10)
        self.overall_bar.setTextVisible(False)
        dock_l.addWidget(self.overall_bar)

        self.task_delegate = TaskItemDelegate(self)
        self.task_tree = QTreeWidget()
        self.task_tree.setColumnCount(3)
        self.task_tree.setHeaderLabels(["任务", "状态", "进度"])
        self.task_tree.setRootIsDecorated(False)
        self.task_tree.setUniformRowHeights(True)
        self.task_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.task_tree.setItemDelegate(self.task_delegate)
        self.task_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.task_tree.header().setSectionResizeMode(1, QHeaderView.Fixed)
        self.task_tree.header().setSectionResizeMode(2, QHeaderView.Fixed)
        self.task_tree.header().resizeSection(1, 84)
        self.task_tree.header().resizeSection(2, 120)
        self.task_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.task_tree.customContextMenuRequested.connect(self._task_context_menu)
        self.task_tree.itemDoubleClicked.connect(self._open_task_path)
        dock_l.addWidget(self.task_tree, 1)

        done_title = QLabel("已完成")
        done_title.setObjectName("mutedLabel")
        dock_l.addWidget(done_title)
        self.done_list = QListWidget()
        self.done_list.setAlternatingRowColors(True)
        self.done_list.setMaximumHeight(120)
        dock_l.addWidget(self.done_list, 0)
        done_btns = QGridLayout()
        done_btns.setSpacing(6)
        for i, (key, label, ic) in enumerate([
            ("open_file", "打开文件", icons.CHECK),
            ("open_folder", "打开文件夹", icons.FOLDER),
            ("copy_path", "复制文件路径", icons.CLIPBOARD),
            ("copy_repo", "复制仓库地址", icons.LINK),
        ]):
            b = QPushButton(" " + label)
            b.setIcon(icons.icon(ic))
            b.clicked.connect(lambda _=False, k=key: self._done_action(k))
            done_btns.addWidget(b, i // 2, i % 2)
        dock_l.addLayout(done_btns)
        self.splitter.addWidget(dock)

        self.splitter.setStretchFactor(0, 62)
        self.splitter.setStretchFactor(1, 38)
        self.splitter.setSizes([800, 460])
        root.addWidget(self.splitter, 1)

        self._update_mirror_label()

    # ---------------------------------------------------------------- 解析

    def on_parse_clicked(self):
        text = self.input_edit.text().strip()
        try:
            parsed = parse_input(text)
        except ParseError as exc:
            if "/" not in text and "\\" not in text and len(text) >= 2:
                # 不是仓库格式 → 当作关键词模糊搜索
                self._start_model_search(text)
                return
            QMessageBox.warning(self, "无法解析", str(exc))
            return
        self._parsed = parsed
        token = self.token_store.load()
        self.repo_label.setText(f"正在获取 {parsed.repo_id} @ {parsed.revision} 的文件列表…")
        self.file_panel.clear_files()
        self._list_worker = _ListWorker(
            self.settings_service.settings.endpoint, parsed.repo_id, parsed.revision, token
        )
        self._list_worker.done.connect(self._on_files_loaded)
        self._list_worker.failed.connect(self._on_files_failed)
        self._list_worker.start()

    def _on_files_loaded(self, repo_id: str, revision: str, files):
        # 携带请求标识：迟到的旧响应直接丢弃，防止快速连续解析时互相覆盖
        if not self._parsed or self._parsed.repo_id != repo_id or self._parsed.revision != revision:
            return
        total = sum(f.size or 0 for f in files)
        shown = repo_id if len(repo_id) <= 30 else repo_id[:27] + "…"
        self.repo_label.setText(
            f"✅ {shown} @ {revision} · {len(files)} 个文件 · 共 {human_size(total)}"
        )
        self.repo_label.setObjectName("")
        style = self.repo_label.style()
        style.unpolish(self.repo_label)
        style.polish(self.repo_label)
        downloaded = self._collect_downloaded(repo_id, files)
        self.file_panel.set_files(files, preselect=self._parsed.filename if self._parsed.is_file else "",
                                  downloaded=downloaded)
        if self._parsed.is_file:
            self.file_panel.filter_edit.setText(self._parsed.filename.rsplit("/", 1)[-1])
        self._update_command_preview()

    def _collect_downloaded(self, repo_id: str, files) -> dict[str, str]:
        """已下载标记 = 历史成功记录 ∪ 当前目标目录中已存在的文件。"""
        downloaded: dict[str, str] = dict(self.history.completed_for_repo(repo_id)) if self.history else {}
        target = self.dir_edit.text().strip()
        if target:
            for f in files:
                if f.path in downloaded:
                    continue
                p = os.path.join(target, *f.path.split("/"))
                if os.path.isfile(p):
                    downloaded[f.path] = p
        return downloaded

    def _on_files_failed(self, repo_id: str, revision: str, message: str, kind: str):
        if not self._parsed or self._parsed.repo_id != repo_id:
            return
        self.repo_label.setText("❌ 获取文件列表失败")
        self.repo_label.setObjectName("errLabel")
        style = self.repo_label.style()
        style.unpolish(self.repo_label)
        style.polish(self.repo_label)
        if kind == KIND_AUTH:
            self._show_auth_banner(1, from_listing=True)
            return
        QMessageBox.warning(self, "获取文件列表失败", message)

    # ---------------------------------------------------------------- 镜像

    def on_test_mirror(self, quiet: bool = False):
        if not quiet:
            self.mirror_result.show()
            self.mirror_result.setText("正在测试镜像…")
        from .mirror_test import MirrorTestWorker

        self._mirror_worker = MirrorTestWorker(self.settings_service.settings.endpoint, self)
        self._mirror_worker.done.connect(self._on_mirror_tested)
        self._mirror_worker.start()

    def _on_mirror_tested(self, result: dict):
        ok = result["ok"]
        if self.on_mirror_status:
            self.on_mirror_status(ok, result.get("latency_ms"))
        if self.mirror_result.isHidden():
            return  # 静默模式：只更新侧栏健康状态
        self.mirror_result.setText(
            f"{'✅' if ok else '❌'} {result['endpoint']} · HTTP {result['status'] or '—'} · {result['message']}"
        )
        self.mirror_result.setObjectName("okLabel" if ok else "errLabel")
        style = self.mirror_result.style()
        style.unpolish(self.mirror_result)
        style.polish(self.mirror_result)

    def _update_mirror_label(self):
        if self.on_mirror_status:
            self.on_mirror_status(None, None)

    def refresh_from_settings(self):
        self._update_mirror_label()
        self._update_disk_info()
        self._update_command_preview()

    # ---------------------------------------------------------------- 模式 / 目录

    def _on_mode_changed(self):
        s = self.settings_service.settings
        mode = self.mode_combo.currentData()
        if mode == "local_dir":
            self.dir_edit.setText(s.local_dir)
        else:
            self.dir_edit.setText(cache_utils.resolve_hub_cache_dir(s.cache_dir))
        self._update_disk_info()
        self._update_command_preview()

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择目录", self.dir_edit.text() or "")
        if d:
            self.dir_edit.setText(d)
            self._update_disk_info()

    def _on_selection_changed(self):
        count, total = self.file_panel.checked_stats()
        self.selection_label.setText(f"已选 {count} 个文件 · {human_size(total)}")
        self.btn_start.setText(
            f" 开始下载 · {count} 个文件 · {human_size(total)}" if count else " 开始下载"
        )
        self._update_disk_info()
        self._update_command_preview()

    def _update_disk_info(self):
        count, total = self.file_panel.checked_stats() if hasattr(self, "file_panel") else (0, 0)
        target = self.dir_edit.text() if hasattr(self, "dir_edit") else ""
        free = disk_free(target) if target else None
        if free is None:
            self.disk_label.setText("⚠ 无法获取目标磁盘剩余空间")
            return
        if total > 0:
            self.disk_label.setText(
                f"总下载 {human_size(total)} · 目标磁盘剩余 {human_size(free)}"
                + (" · ⚠ 空间不足！" if free < total else " · ✅")
            )
            self.disk_label.setObjectName("okLabel" if free >= total else "errLabel")
        else:
            self.disk_label.setText(f"目标磁盘剩余 {human_size(free)}")
            self.disk_label.setObjectName("mutedLabel")
        style = self.disk_label.style()
        style.unpolish(self.disk_label)
        style.polish(self.disk_label)

    # ---------------------------------------------------------------- 开始下载

    def on_start(self):
        if not self._parsed:
            QMessageBox.information(self, "提示", "请先粘贴链接并点击「解析仓库」。")
            return
        files = self.file_panel.checked_files()
        if not files:
            QMessageBox.information(self, "提示", "请先勾选要下载的文件。")
            return

        mode = self.mode_combo.currentData()
        target = self.dir_edit.text().strip()
        if mode == "local_dir":
            if not target:
                QMessageBox.warning(self, "提示", "请填写下载目录。")
                return
        else:
            target = cache_utils.resolve_hub_cache_dir(target or self.settings_service.settings.cache_dir)
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as exc:
            # 目录非法（盘符不存在 / 非法字符 / 无权限）：给中文提示并引导选择
            from ..core.errors import translate_exception

            fe = translate_exception(exc)
            ret = QMessageBox.question(
                self, "下载目录不可用",
                f"{fe.message}\n\n当前目录：{target}\n\n是否现在选择其他目录？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ret == QMessageBox.Yes:
                self._pick_valid_dir()
            return

        # 磁盘空间检查
        total = sum(f.size or 0 for f in files)
        free = disk_free(target)
        if free is not None and free < total:
            ret = QMessageBox.question(
                self, "磁盘空间不足",
                f"总下载 {human_size(total)}，目标磁盘仅剩 {human_size(free)}。\n确定仍要继续吗？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return

        spec = BatchSpec(
            repo_id=self._parsed.repo_id,
            revision=self._parsed.revision,
            files=files,
            mode=mode,
            local_dir=target if mode == "local_dir" else None,
            cache_dir=target if mode == "cache" else None,
            endpoint=self.settings_service.settings.endpoint,
        )
        self.start_requested.emit(spec)
        QMessageBox.information(
            self, "已开始下载",
            f"已提交 {len(files)} 个文件（{human_size(total)}）。\n下方任务列表会实时显示进度。",
        )

    # ---------------------------------------------------------------- 引擎刷新（由 MainWindow 定时调用）

    def refresh_tasks(self):
        if self.engine is None:
            return
        snaps = self.engine.snapshot()
        seen = set()
        kind_map = {
            TaskState.DOWNLOADING: "run", TaskState.COMPLETED: "ok",
            TaskState.PAUSED: "warn", TaskState.FAILED: "err",
            TaskState.CANCELLED: "queue", TaskState.PENDING: "queue",
        }
        for s in snaps:
            tid = s["task_id"]
            seen.add(tid)
            item = self._task_item(tid)
            if item is None:
                item = QTreeWidgetItem(["", "", ""])
                item.setData(0, Qt.UserRole, tid)
                self.task_tree.addTopLevelItem(item)
            state: TaskState = s["state"]
            item.setText(0, s["filename"])
            label = state.value + (f" · {s['note']}" if s["note"] and state in (TaskState.PENDING, TaskState.PAUSED) else "")
            item.setText(1, label)
            item.setData(1, Qt.UserRole + 6, kind_map[state])
            if state is TaskState.COMPLETED:
                pct = 100.0
            elif state is TaskState.DOWNLOADING and s["size"]:
                pct = min(100.0, s["bytes_done"] / s["size"] * 100)
            else:
                pct = 0.0
            item.setData(2, Qt.UserRole + 4, pct)
            if state is TaskState.DOWNLOADING:
                eta = format_eta((s["size"] - s["bytes_done"]) / s["speed"]) if s["speed"] > 0 and s["size"] else "—"
                meta = f"{human_size(s['bytes_done'])} / {human_size(s['size'])} · {human_speed(s['speed'])} · {eta}"
            elif state is TaskState.COMPLETED:
                meta = f"{human_size(s['size'])} · {s['local_path']}" if s["local_path"] else human_size(s["size"])
            elif state is TaskState.FAILED:
                meta = s["error"].split(chr(10))[0] if s["error"] else "下载失败，可重试"
            elif state is TaskState.PAUSED:
                meta = f"{human_size(s['size'])} · 已暂停，可从断点继续"
            else:
                meta = f"{human_size(s['size'])} · " + (s["note"] or "等待中")
            item.setData(0, Qt.UserRole + 5, meta)
            item.setToolTip(0, s["error"] or s["local_path"] or "")

        # 移除已不存在的任务行
        for i in range(self.task_tree.topLevelItemCount() - 1, -1, -1):
            it = self.task_tree.topLevelItem(i)
            if it.data(0, Qt.UserRole) not in seen:
                self.task_tree.takeTopLevelItem(i)

        # 完成列表
        for s in snaps:
            if s["state"] is TaskState.COMPLETED and s["task_id"] not in getattr(self, "_done_seen", set()):
                if not hasattr(self, "_done_seen"):
                    self._done_seen = set()
                self._done_seen.add(s["task_id"])
                self._add_done_item(s)

        # 整体进度
        if self._batch_id and not self.engine.all_tasks_finished(self._batch_id):
            summ = self.engine.batch_summary(self._batch_id)
            if summ["total"] > 0:
                pct = summ["done"] / summ["total"] * 100
                self.overall_bar.setValue(int(pct * 10))
            else:
                self.overall_bar.setValue(0)
            eta_text = format_eta(summ["eta"])
            self.overall_label.setText(
                f"整体 {summ['done'] / summ['total'] * 100 if summ['total'] else 0:.1f}% · "
                f"{human_speed(summ['speed'])} · 完成 {summ['done_count']}/{summ['count']}"
                + (f" · 失败 {summ['failed_count']}" if summ["failed_count"] else "")
                + f" · 剩余 {eta_text}"
            )
        elif self._batch_id:
            summ = self.engine.batch_summary(self._batch_id)
            self.overall_bar.setValue(int((summ["done"] / summ["total"] * 10) if summ["total"] else 0))
            state_text = "全部完成 ✅" if summ["failed_count"] == 0 else f"结束（失败 {summ['failed_count']} 个，可重试）"
            self.overall_label.setText(f"{state_text} · 共 {human_size(summ['done'])} · {summ['count']} 个文件")
            self._update_failure_banners(snaps)

    # ---------------------------------------------------------------- 横幅（恢复 / 镜像 / Token / 目录）

    def _update_failure_banners(self, snaps):
        failed = [s for s in snaps if s["state"] is TaskState.FAILED]
        auth = [s for s in failed if s["error_kind"] == KIND_AUTH]
        net = [s for s in failed if s["error_kind"] in (KIND_NETWORK, KIND_UNKNOWN)]
        if auth:
            self._show_auth_banner(len(auth))
        else:
            self.auth_banner.hide()
        if net and not auth:
            self._show_net_banner(len(net))
        else:
            self.net_banner.hide()

    def _show_auth_banner(self, count: int, from_listing: bool = False):
        saved = self.token_store.load()
        if saved:
            self.auth_token_edit.setPlaceholderText(f"已保存（{saved[:5]}***），直接点击重试")
        else:
            self.auth_token_edit.setPlaceholderText("hf_xxxxxxxx")
        if from_listing:
            self.auth_label.setText("该模型需要登录 Hugging Face 或接受模型许可（HTTP 401/403）。填写 HF Token 后重新解析：")
        else:
            self.auth_label.setText(
                f"⚠ {count} 个文件需要登录 Hugging Face 或接受模型许可。填写 HF Token 后一键重试："
            )
        self.net_banner.hide()
        self.auth_banner.show()

    def _show_net_banner(self, count: int):
        self.net_label.setText(
            f"⚠ {count} 个任务因网络原因失败。镜像可能不稳定或已限速，可测试并切换下载源。"
        )
        self.auth_banner.hide()
        self.net_banner.show()

    def _save_token_and_retry(self):
        token = self.auth_token_edit.text().strip()
        if token:
            self.token_store.save(token)
            self.auth_token_edit.clear()
        elif not self.token_store.load():
            QMessageBox.information(self, "提示", "请先粘贴 HF Token（hf_ 开头）。")
            return
        self.auth_banner.hide()
        if self.engine:
            self.engine.retry_failed()
        self._refresh_listing_if_auth_failed()

    def _refresh_listing_if_auth_failed(self):
        """若失败发生在“解析列表”阶段，保存 Token 后自动重新解析。"""
        if self._parsed and not self.file_panel.tree.topLevelItemCount():
            self.on_parse_clicked()

    def _retry_failed(self):
        self.net_banner.hide()
        if self.engine:
            self.engine.retry_failed()

    def _open_mirror_picker(self):
        from .mirror_picker import MirrorPickerDialog

        dlg = MirrorPickerDialog(self.settings_service.settings.endpoint, self)
        if dlg.exec() != MirrorPickerDialog.Accepted or not dlg.chosen_endpoint:
            return
        self._apply_endpoint(dlg.chosen_endpoint)
        if dlg.auto_retry.isChecked() and self.engine:
            self.engine.retry_failed()

    def _apply_endpoint(self, endpoint: str):
        s = self.settings_service.settings
        s.endpoint = endpoint
        try:
            self.settings_service.save()
        except OSError:
            logger.warning("保存设置失败（endpoint 已在本次会话生效）")
        if self.engine:
            self.engine.update_endpoint(endpoint)
        self._update_mirror_label()
        if self.on_mirror_status:
            self.on_mirror_status(None, None)
        self.mirror_result.hide()

    # ---- 恢复队列横幅（由 MainWindow 注入回调） ----

    def show_restore_banner(self, batches: list[dict], on_resume, on_clear):
        self._pending_batches = batches
        self._on_restore_resume = on_resume
        self._on_restore_clear = on_clear
        total = sum(f.get("size") or 0 for b in batches for f in b["files"] if not f.get("done"))
        done_bytes = sum(f.get("bytes_done") or 0 for b in batches for f in b["files"] if not f.get("done"))
        n_files = sum(1 for b in batches for f in b["files"] if not f.get("done"))
        self.restore_label.setText(
            f"⏳ 上次有未完成的下载：{len(batches)} 个批次 / {n_files} 个文件 · "
            f"共 {human_size(total)}，已下载 {human_size(done_bytes)}。续传将从断点继续。"
        )
        self.restore_banner.show()

    def _restore_resume(self):
        cb = self._on_restore_resume
        batches = self._pending_batches
        self.restore_banner.hide()
        self._pending_batches = []
        if cb:
            cb(batches)

    def _restore_clear(self):
        cb = self._on_restore_clear
        self.restore_banner.hide()
        self._pending_batches = []
        if cb:
            cb()

    # ---- 目录校验横幅 ----

    def _check_target_dir_banner(self):
        """默认下载目录所在盘不可用时给出引导（例如 E 盘不存在的机器）。"""
        if not hasattr(self, "dir_edit"):
            return
        target = self.dir_edit.text().strip()
        mode = self.mode_combo.currentData() if hasattr(self, "mode_combo") else "local_dir"
        if not target or disk_free(target) is not None and os.path.isdir(target) or mode == "cache":
            self.dir_banner.hide()
            return
        # 逐级找存在的父目录来判断盘符
        probe = os.path.abspath(target)
        drive = os.path.splitdrive(probe)[0]
        if drive and not os.path.exists(drive + os.sep):
            self.dir_label.setText(
                f"⚠ 默认下载目录“{target}”所在的磁盘 {drive}\\ 不存在，下载会失败。请选择其他目录。"
            )
            self.dir_banner.show()
        else:
            self.dir_banner.hide()

    def _pick_valid_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择下载目录", "")
        if not d:
            return
        s = self.settings_service.settings
        if self.mode_combo.currentData() == "local_dir":
            s.local_dir = d
        else:
            s.cache_dir = d
        try:
            self.settings_service.save()
        except OSError:
            logger.warning("保存设置失败（目录已在本次会话生效）")
        self.dir_edit.setText(d)
        self.dir_banner.hide()
        self._update_disk_info()

    def _task_item(self, task_id: str) -> QTreeWidgetItem | None:
        for i in range(self.task_tree.topLevelItemCount()):
            it = self.task_tree.topLevelItem(i)
            if it.data(0, Qt.UserRole) == task_id:
                return it
        return None

    def set_batch(self, batch_id: str):
        self._batch_id = batch_id
        self.overall_bar.setValue(0)
        self.overall_label.setText("准备中…")

    # ---------------------------------------------------------------- 任务右键菜单

    def _task_context_menu(self, pos):
        menu = QMenu(self)
        item = self.task_tree.itemAt(pos)
        if item is not None and self.engine:
            tid = item.data(0, Qt.UserRole)
            s = next((x for x in self.engine.snapshot() if x["task_id"] == tid), None)
            if s:
                state = s["state"]
                if state is TaskState.DOWNLOADING:
                    menu.addAction("暂停", lambda: self.engine.pause(tid))
                    menu.addAction("取消", lambda: self.engine.cancel(tid))
                elif state in (TaskState.PAUSED, TaskState.FAILED, TaskState.CANCELLED):
                    menu.addAction("继续 / 重试", lambda: self.engine.retry(tid))
                    menu.addAction("取消任务", lambda: self.engine.cancel(tid))
                if s["local_path"]:
                    menu.addSeparator()
                    menu.addAction("打开所在文件夹", lambda: open_in_explorer(s["local_path"]))
                    menu.addAction("复制文件路径", lambda: self._copy(s["local_path"]))
                menu.exec(self.task_tree.viewport().mapToGlobal(pos))

    def _open_task_path(self, item, _col):
        if self.engine:
            tid = item.data(0, Qt.UserRole)
            s = next((x for x in self.engine.snapshot() if x["task_id"] == tid), None)
            if s and s["local_path"]:
                open_in_explorer(s["local_path"])

    # ---------------------------------------------------------------- 完成区

    def _add_done_item(self, s: dict):
        size_text = human_size(s["size"]) if s["size"] else "未知大小"
        extra = ""
        if s["filename"].lower().endswith(".gguf"):
            extra = f" · 🧠 模型文件：{s['filename'].rsplit('/', 1)[-1]}（{size_text}）"
        item = QListWidgetItem(f"{s['filename']} · {size_text}{extra}\n{os.path.abspath(s['local_path'])}")
        item.setData(Qt.UserRole, s)
        self.done_list.insertItem(0, item)
        if self.done_list.count() > 100:
            self.done_list.takeItem(self.done_list.count() - 1)

    def _done_action(self, action: str):
        item = self.done_list.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "请先在列表中选择一个已完成的文件。")
            return
        s = item.data(Qt.UserRole)
        path = os.path.abspath(s["local_path"])
        if action == "open_file":
            open_path(path)
        elif action == "open_folder":
            open_in_explorer(path)
        elif action == "copy_path":
            self._copy(path)
        elif action == "copy_repo":
            self._copy(f"{self.settings_service.settings.endpoint}/{s['repo_id']}")

    # ---------------------------------------------------------------- 高级 / 命令预览

    def _toggle_advanced(self, on: bool):
        self.adv_frame.setVisible(on)
        self.adv_toggle.setText("高级 ▾ 等价 hf 命令" if on else "高级 ▸ 等价 hf 命令")

    def _update_command_preview(self):
        if not self._parsed:
            return
        files = self.file_panel.checked_files()
        if not files:
            self.command_preview.setText("")
            return
        mode = self.mode_combo.currentData()
        target = self.dir_edit.text().strip()
        patterns = sorted({f.path for f in files})
        cmd = cache_utils.build_hf_command(
            self._parsed.repo_id,
            revision=self._parsed.revision,
            patterns=patterns,
            mode=mode,
            local_dir=target if mode == "local_dir" else None,
            cache_dir=target if mode == "cache" else None,
        )
        self.command_preview.setText(cmd)

    # ---------------------------------------------------------------- 模型搜索（无作者）

    def _start_model_search(self, query: str):
        from .search_dialog import ModelSearchDialog

        if self._search_dialog is None:
            self._search_dialog = ModelSearchDialog(self)
            self._search_dialog.repo_picked.connect(self._on_search_picked)
        self._search_dialog.begin(query)
        token = self.token_store.load()
        self._search_worker = _SearchWorker(self.settings_service.settings.endpoint, query, token)
        self._search_worker.done.connect(self._on_search_done)
        self._search_worker.failed.connect(self._on_search_failed)
        self._search_worker.start()

    def _on_search_done(self, query: str, results: list):
        if self._search_dialog and self._search_dialog.isVisible() and self._search_dialog.query_label.text().endswith(query):
            self._search_dialog.set_results(results)

    def _on_search_failed(self, query: str, message: str):
        if self._search_dialog and self._search_dialog.isVisible():
            self._search_dialog.set_failed(message)

    def _on_search_picked(self, repo_id: str):
        self.input_edit.setText(repo_id)
        self.on_parse_clicked()

    # ---------------------------------------------------------------- 链接转换

    def _open_convert_dialog(self):
        from .convert_dialog import ConvertDialog

        ConvertDialog(self.settings_service.settings.endpoint, self).exec()

    # ---------------------------------------------------------------- 小工具

    def _copy(self, text: str):
        if text:
            QGuiApplication.clipboard().setText(text)

    def load_demo_data(self):
        """HFM_DEMO=1 时填充演示数据（无网络）。"""
        files = [
            RepoFile(path="Ternary-Bonsai-PTQ1_0-Q4_K_M.gguf", size=15 * 1024**3, is_lfs=True),
            RepoFile(path="Ternary-Bonsai-PTQ1_0-Q8_0.gguf", size=28 * 1024**3, is_lfs=True),
            RepoFile(path="Ternary-Bonsai-BF16.gguf", size=54 * 1024**3, is_lfs=True),
            RepoFile(path="model.safetensors", size=6 * 1024**3, is_lfs=True),
            RepoFile(path="model.litertlm", size=4 * 1024**3, is_lfs=True),
            RepoFile(path="bundle.zip", size=700 * 1024**2, is_lfs=True),
            RepoFile(path="config.json", size=812),
            RepoFile(path="tokenizer.json", size=7_000_000),
            RepoFile(path="README.md", size=1200),
        ]
        from ..core.url_parser import ParsedInput

        self._parsed = ParsedInput(repo_id="OS-Software/Ternary-Bonsai-2-27B-Uncensored-Heretic-GGUF")
        self.input_edit.setText(self._parsed.repo_id)
        shown = self._parsed.repo_id[:27] + "…"
        self.repo_label.setText(f"✅ {shown} @ main · {len(files)} 个文件 · 共 {human_size(sum(f.size or 0 for f in files))}")
        self.file_panel.set_files(files)
