"""主窗口：自定义无边框标题栏 + 左侧导航 + 页面栈，持有引擎与全局服务。

B 方向 UI：
- 无边框窗口 + 自绘标题栏（拖动/双击最大化/边缘缩放/Win11 贴边布局/DWM 圆角）
- 侧栏真图标 + 底部镜像健康状态（自动静默测速）
- 托盘、关闭保护、队列持久化（沿用 P0 行为）
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

from PySide6.QtCore import QEvent, QPointF, QSize, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, APP_VERSION
from ..core.download_engine import DownloadEngine
from ..core.history import HistoryStore
from ..core.queue_store import QueueStore
from ..core.secure_store import TokenStore
from ..models import BatchSpec, RepoFile, TaskState
from ..services.paths import config_dir
from ..services.settings_service import SettingsService
from . import icons
from .about_page import AboutPage
from .common import app_icon
from .download_page import DownloadPage
from .history_page import HistoryPage
from .settings_page import SettingsPage
from .theme import stylesheet

logger = logging.getLogger(__name__)

# Win32 常量
_HTCLIENT, _HTCAPTION = 1, 2
_HTLEFT, _HTRIGHT, _HTTOP, _HTTOPLEFT, _HTTOPRIGHT = 10, 11, 12, 13, 14
_HTBOTTOM, _HTBOTTOMLEFT, _HTBOTTOMRIGHT = 15, 16, 17
_WM_NCHITTEST = 0x0084
_WM_NCLBUTTONUP = 0x00A2


class TitleBar(QWidget):
    """自绘标题栏：拖动、双击最大化、窗口按钮。"""

    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self.window_ref = window
        self.setObjectName("titlebar")
        self.setFixedHeight(40)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 4, 0)
        lay.setSpacing(9)

        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(QSize(20, 20)))
        lay.addWidget(logo)
        name = QLabel(APP_NAME)
        name.setObjectName("titlebarName")
        lay.addWidget(name)
        lay.addStretch(1)

        self.btn_min = self._mk(icons.MINIMIZE, "最小化", window.showMinimized)
        self.btn_max = self._mk(icons.MAXIMIZE, "最大化 / 还原", window.toggle_max)
        self.btn_close = self._mk(icons.CLOSE, "关闭", window.close)
        self.btn_close.setObjectName("btnClose")
        lay.addWidget(self.btn_min)
        lay.addWidget(self.btn_max)
        lay.addWidget(self.btn_close)

    def _mk(self, icon_name: str, tip: str, slot) -> QPushButton:
        b = QPushButton()
        b.setIcon(icons.icon(icon_name))
        b.setFixedSize(40, 28)
        b.setProperty("cssClass", "winbtn")
        b.setToolTip(tip)
        b.clicked.connect(slot)
        return b

    def set_maximized(self, maximized: bool):
        self.btn_max.setIcon(icons.icon(icons.RESTORE if maximized else icons.MAXIMIZE))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.window_ref.toggle_max()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1280, 800)
        self.setMinimumSize(1000, 660)
        self.setWindowFlag(Qt.FramelessWindowHint)

        # 全局服务
        self.settings_service = SettingsService()
        self.token_store = TokenStore(config_dir())
        self.history_store = HistoryStore(config_dir() / "history.db")
        self.queue_store = QueueStore(config_dir() / "queue.json")
        self.engine = DownloadEngine(
            token_provider=self.token_store.load,
            history=self.history_store,
            max_workers=self.settings_service.settings.max_workers,
            max_retries=self.settings_service.settings.max_retries,
        )

        self._build_ui()
        self._build_tray()

        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._last_state_fp = ""
        self._last_save_time = 0.0
        self._had_active = False

        # 启动后静默测一次镜像（更新侧栏健康状态）
        QTimer.singleShot(500, lambda: self.download_page.on_test_mirror(quiet=True))

    # ---------------------------------------------------------------- UI

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._root_layout = root

        self.titlebar = TitleBar(self)
        root.addWidget(self.titlebar)

        body = QWidget()
        body_l = QHBoxLayout(body)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(0)

        # 侧边栏
        sidebar = QWidget()
        sidebar.setObjectName("sidebarWidget")
        sidebar.setFixedWidth(188)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(10, 12, 10, 10)
        sl.setSpacing(4)
        self.nav = QListWidget()
        self.nav.setObjectName("sidebar")
        self.nav.setIconSize(QSize(18, 18))
        for text, ic in [("下载", icons.NAV_DOWNLOAD), ("历史", icons.NAV_HISTORY),
                         ("设置", icons.NAV_SETTINGS), ("关于", icons.NAV_ABOUT)]:
            item = QListWidgetItem(text)
            item.setIcon(icons.icon(ic))
            self.nav.addItem(item)
        self.nav.setCurrentRow(0)
        sl.addWidget(self.nav, 1)
        self.mirror_status = QLabel("● —")
        self.mirror_status.setObjectName("mirrorStatus")
        self.mirror_status.setAlignment(Qt.AlignCenter)
        sl.addWidget(self.mirror_status)
        ver = QLabel(f"v{APP_VERSION}")
        ver.setObjectName("mutedLabel")
        ver.setAlignment(Qt.AlignCenter)
        sl.addWidget(ver)
        body_l.addWidget(sidebar)

        line = QFrame()
        line.setObjectName("line")
        line.setFixedWidth(1)
        body_l.addWidget(line)

        # 页面栈
        self.stack = QStackedWidget()
        self.download_page = DownloadPage(self.settings_service, self.token_store, self.history_store)
        self.download_page.engine = self.engine
        self.download_page.start_requested.connect(self._start_batch)
        self.download_page.on_mirror_status = self._on_mirror_status
        self.history_page = HistoryPage(self.history_store, self.settings_service)
        self.history_page.re_download.connect(self._start_batch)
        self.settings_page = SettingsPage(self.settings_service, self.token_store)
        self.settings_page.settings_saved.connect(self._apply_settings)
        self.about_page = AboutPage()
        for w in (self.download_page, self.history_page, self.settings_page, self.about_page):
            self.stack.addWidget(w)
        self.nav.currentRowChanged.connect(self._on_nav)
        body_l.addWidget(self.stack, 1)
        root.addWidget(body, 1)
        self.setCentralWidget(central)

        self._restore_queue_banner()

    # ---------------------------------------------------------------- 导航

    def _on_nav(self, index: int):
        self.stack.setCurrentIndex(index)
        if index == 1:
            self.history_page.refresh()

    # ---------------------------------------------------------------- 镜像健康

    def _on_mirror_status(self, ok, latency):
        from .mirror_test import mirror_label

        name = mirror_label(self.settings_service.settings.endpoint)
        if ok is None:
            self.mirror_status.setText(f"● {name}")
        elif ok:
            lat = f" · {latency:.0f}ms" if latency else ""
            self.mirror_status.setText(
                f"<span style='color:#3ecf8e'>●</span> {name}{lat}"
            )
        else:
            self.mirror_status.setText(f"<span style='color:#ef5f5f'>●</span> {name}")

    # ---------------------------------------------------------------- 窗口控制

    def toggle_max(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def showEvent(self, event):
        super().showEvent(event)
        self._enable_round_corners()

    def _enable_round_corners(self):
        """Win11 无边框窗口的圆角（失败则静默直角）。"""
        try:
            hwnd = int(self.winId())
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        except Exception:  # noqa: BLE001
            pass

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.titlebar.set_maximized(self.isMaximized())
            # 最大化时留出系统缩放边距，避免内容顶到屏幕边缘
            m = 8 if self.isMaximized() else 0
            self._root_layout.setContentsMargins(m, m, m, m)

    def nativeEvent(self, eventType, message):
        """边缘缩放 + 标题栏拖动 + Win11 贴边布局（HTMAXBUTTON）。"""
        if eventType == b"windows_generic_MSG":
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == _WM_NCHITTEST:
                    return self._hit_test(msg)
            except Exception:  # noqa: BLE001
                pass
        return False, 0

    def _hit_test(self, msg):
        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        dpr = self.devicePixelRatioF() or 1.0
        # 屏幕物理坐标 → 全局逻辑坐标 → 窗口本地坐标
        global_pos = QPointF(x / dpr, y / dpr).toPoint()
        pos = self.mapFromGlobal(global_pos)

        child = self.childAt(pos)
        if child in (self.titlebar.btn_min, self.titlebar.btn_max, self.titlebar.btn_close):
            return False, 0  # HTCLIENT，让按钮正常接收 Qt 点击
        # 在标题栏内（非按钮）→ 拖动
        w = child
        while w is not None:
            if w is self.titlebar:
                if not self.isMaximized():
                    return True, _HTCAPTION
                return False, 0
            w = w.parentWidget()

        if self.isMaximized():
            return False, 0
        m = 6
        on_left = pos.x() <= m
        on_right = pos.x() >= self.width() - m
        on_top = pos.y() <= m
        on_bottom = pos.y() >= self.height() - m
        if on_top and on_left:
            return True, _HTTOPLEFT
        if on_top and on_right:
            return True, _HTTOPRIGHT
        if on_bottom and on_left:
            return True, _HTBOTTOMLEFT
        if on_bottom and on_right:
            return True, _HTBOTTOMRIGHT
        if on_left:
            return True, _HTLEFT
        if on_right:
            return True, _HTRIGHT
        if on_top:
            return True, _HTTOP
        if on_bottom:
            return True, _HTBOTTOM
        return False, 0

    # ---------------------------------------------------------------- 下载

    def _start_batch(self, spec: BatchSpec):
        batch_id = self.engine.submit(spec)
        self.download_page.set_batch(batch_id)
        self.nav.setCurrentRow(0)
        logger.info("批次 %s 已提交", batch_id)

    # ---------------------------------------------------------------- 托盘

    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu(self)
        act_show = QAction("显示主窗口", self)
        act_show.triggered.connect(self._restore_window)
        menu.addAction(act_show)
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.close)
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self._restore_window()

    def _restore_window(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()

    # ---------------------------------------------------------------- 队列持久化与恢复

    def _tick(self):
        self.download_page.refresh_tasks()
        self._persist_queue_if_changed()
        self._notify_all_finished()

    def _persist_queue_if_changed(self):
        fp = self.engine.fingerprint()
        now = time.monotonic()
        states_changed = fp != self._last_state_fp
        if not states_changed and now - self._last_save_time < 5.0:
            return
        self._last_state_fp = fp
        self._last_save_time = now
        self.queue_store.save(self.engine.export_incomplete_batches())

    def _notify_all_finished(self):
        active = any(
            t.state in (TaskState.DOWNLOADING, TaskState.PENDING) for t in self.engine._tasks.values()
        )
        if self._had_active and not active and self.tray is not None and not self.isVisible():
            self.tray.showMessage(
                APP_NAME,
                "全部下载任务已结束，点击查看。",
                QSystemTrayIcon.Information,
                5000,
            )
        self._had_active = active

    def _restore_queue_banner(self):
        batches = self.queue_store.load()
        if not batches:
            return
        self.download_page.show_restore_banner(
            batches,
            on_resume=self._resume_saved_batches,
            on_clear=self.queue_store.clear,
        )

    def _resume_saved_batches(self, batches: list[dict]):
        last_batch_id = None
        for b in batches:
            files = [
                RepoFile(
                    path=f["path"],
                    size=f.get("size"),
                    lfs_sha256=f.get("lfs_sha256"),
                    oid=f.get("oid"),
                )
                for f in b["files"]
                if not f.get("done")
            ]
            if not files:
                continue
            spec = BatchSpec(
                repo_id=b["repo_id"],
                revision=b.get("revision") or "main",
                files=files,
                mode=b.get("mode") or "local_dir",
                local_dir=b.get("local_dir"),
                cache_dir=b.get("cache_dir"),
                endpoint=self.settings_service.settings.endpoint,
            )
            last_batch_id = self.engine.submit(spec)
        self.queue_store.clear()
        if last_batch_id:
            self.download_page.set_batch(last_batch_id)
        self.nav.setCurrentRow(0)

    # ---------------------------------------------------------------- 设置

    def _apply_settings(self):
        s = self.settings_service.settings
        self.engine.set_max_workers(s.max_workers)
        self.engine.set_max_retries(s.max_retries)
        self.download_page.refresh_from_settings()
        self.download_page.apply_theme(s.theme)
        QApplication.instance().setStyleSheet(stylesheet(s.theme))

    # ---------------------------------------------------------------- 关闭保护

    def closeEvent(self, event):
        has_active = any(
            t.state in (TaskState.DOWNLOADING, TaskState.PENDING) for t in self.engine._tasks.values()
        )
        if has_active:
            choice = self._ask_on_close()
            if choice is None:
                event.ignore()
                return
            if choice == "tray":
                self.hide()
                if self.tray is not None:
                    self.tray.showMessage(
                        APP_NAME,
                        "已最小化到托盘，下载继续进行。",
                        QSystemTrayIcon.Information,
                        3000,
                    )
                event.ignore()
                return
        self.engine.pause_all()
        self.queue_store.save(self.engine.export_incomplete_batches())
        event.accept()

    def _ask_on_close(self) -> str | None:
        action = self.settings_service.settings.close_action
        if action == "tray":
            return "tray"
        if action == "exit":
            return "exit"

        box = QMessageBox(self)
        box.setWindowTitle("正在下载")
        box.setText("还有下载任务正在进行。关闭窗口要怎么处理？")
        box.setInformativeText("选择“最小化到托盘”可继续下载；退出会暂停任务，下次启动可一键续传。")
        tray_btn = box.addButton("最小化到托盘", QMessageBox.YesRole)
        exit_btn = box.addButton("退出并暂停", QMessageBox.DestructiveRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(tray_btn)
        remember = QCheckBox("记住我的选择（可在设置中更改）")
        box.setCheckBox(remember)
        box.exec()
        clicked = box.clickedButton()
        if clicked is cancel_btn:
            return None
        choice = "tray" if clicked is tray_btn else "exit"
        if remember.isChecked():
            self.settings_service.settings.close_action = choice
            try:
                self.settings_service.save()
            except OSError:
                logger.warning("保存设置失败（关闭行为仅在本次会话生效）")
        return choice
