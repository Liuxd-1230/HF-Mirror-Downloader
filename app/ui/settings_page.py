"""设置页：下载源、目录、并发、Token、主题、维护工具。"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core import cache_utils
from ..core.errors import FriendlyError
from ..models import AppSettings
from ..services.paths import config_dir, logs_dir

logger = logging.getLogger(__name__)

MIRROR_PRESETS = [
    "https://hf-mirror.net",
    "https://hf-mirror.com",
    "https://huggingface.co",
]


class SettingsPage(QWidget):
    settings_saved = Signal()  # 保存成功后通知 MainWindow 立即应用

    def __init__(self, settings_service, token_store, parent=None):
        super().__init__(parent)
        self.settings_service = settings_service
        self.token_store = token_store

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        # 下载源
        self.endpoint_edit = QComboBox()
        self.endpoint_edit.setEditable(True)
        for m in MIRROR_PRESETS:
            self.endpoint_edit.addItem(m)
        self.endpoint_edit.setCurrentText(self.settings_service.settings.endpoint)
        form.addRow("下载源（镜像 endpoint）", self.endpoint_edit)

        # 下载模式
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("指定目录（文件直接放在该目录下）", "local_dir")
        self.mode_combo.addItem("HF 缓存（兼容官方缓存结构）", "cache")
        idx = self.mode_combo.findData(self.settings_service.settings.download_mode)
        self.mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("默认下载模式", self.mode_combo)

        # 目录
        dir_row = QHBoxLayout()
        self.local_dir_edit = QLineEdit(self.settings_service.settings.local_dir)
        btn1 = QPushButton("浏览…")
        btn1.clicked.connect(lambda: self._pick(self.local_dir_edit, "选择默认下载目录"))
        dir_row.addWidget(self.local_dir_edit, 1)
        dir_row.addWidget(btn1)
        form.addRow("默认下载目录", dir_row)

        cache_row = QHBoxLayout()
        self.cache_dir_edit = QLineEdit(self.settings_service.settings.cache_dir)
        btn2 = QPushButton("浏览…")
        btn2.clicked.connect(lambda: self._pick(self.cache_dir_edit, "选择 HF 缓存目录"))
        cache_row.addWidget(self.cache_dir_edit, 1)
        cache_row.addWidget(btn2)
        form.addRow("HF 缓存目录", cache_row)
        cache_tip = QLabel("缓存目录指向 HF_HUB_CACHE（models--* 所在位置）。若其下已有 hub/models--*，将自动使用 hub 子目录。")
        cache_tip.setObjectName("mutedLabel")
        form.addRow("", cache_tip)

        # 并发
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 8)
        self.workers_spin.setValue(self.settings_service.settings.max_workers)
        self.workers_spin.setToolTip("同时下载的文件数；单文件内部始终为单连接续传")
        form.addRow("最大并发数", self.workers_spin)

        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 10)
        self.retries_spin.setValue(self.settings_service.settings.max_retries)
        form.addRow("失败自动重试次数", self.retries_spin)

        # 主题
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("深色", "dark")
        self.theme_combo.addItem("浅色", "light")
        tidx = self.theme_combo.findData(self.settings_service.settings.theme)
        self.theme_combo.setCurrentIndex(tidx if tidx >= 0 else 0)
        form.addRow("主题", self.theme_combo)

        # 关闭行为
        self.close_combo = QComboBox()
        self.close_combo.addItem("每次询问", "ask")
        self.close_combo.addItem("最小化到托盘（下载继续）", "tray")
        self.close_combo.addItem("直接退出（任务暂停，可续传）", "exit")
        cidx = self.close_combo.findData(self.settings_service.settings.close_action)
        self.close_combo.setCurrentIndex(cidx if cidx >= 0 else 0)
        form.addRow("关闭窗口时", self.close_combo)
        lay.addLayout(form)

        # Token
        token_label = QLabel("HF Token（可选，gated / 私有模型需要）")
        token_label.setObjectName("sidebarTitle")
        lay.addWidget(token_label)
        token_row = QHBoxLayout()
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("hf_xxxxxxxx（留空表示不使用）")
        saved = self.token_store.load()
        if saved:
            self.token_edit.setPlaceholderText(f"已保存（{saved[:5]}***）")
        token_row.addWidget(self.token_edit, 1)
        self.token_show = QCheckBox("显示")
        self.token_show.toggled.connect(
            lambda on: self.token_edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        )
        token_row.addWidget(self.token_show)
        b_save_token = QPushButton("保存 Token")
        b_save_token.clicked.connect(self._save_token)
        token_row.addWidget(b_save_token)
        b_clear_token = QPushButton("清除")
        b_clear_token.clicked.connect(self._clear_token)
        token_row.addWidget(b_clear_token)
        lay.addLayout(token_row)
        token_tip = QLabel("Token 使用 Windows DPAPI 按当前用户加密保存，不写入日志与历史。")
        token_tip.setObjectName("mutedLabel")
        lay.addWidget(token_tip)

        # 维护
        maint_row = QHBoxLayout()
        b_test = QPushButton("测试镜像")
        b_test.clicked.connect(self._test_mirror)
        maint_row.addWidget(b_test)
        b_log = QPushButton("打开日志")
        b_log.clicked.connect(lambda: self._open_dir(logs_dir()))
        maint_row.addWidget(b_log)
        b_cfg = QPushButton("打开配置目录")
        b_cfg.clicked.connect(lambda: self._open_dir(config_dir()))
        maint_row.addWidget(b_cfg)
        b_clean = QPushButton("清理未完成的临时文件")
        b_clean.setToolTip("删除所有 *.incomplete 断点临时文件（取消或暂停的任务产生的）")
        b_clean.clicked.connect(self._clean_incomplete)
        maint_row.addWidget(b_clean)
        maint_row.addStretch(1)
        lay.addLayout(maint_row)
        self.test_result = QLabel("")
        self.test_result.setObjectName("mutedLabel")
        lay.addWidget(self.test_result)

        lay.addStretch(1)

        # 保存
        save_row = QHBoxLayout()
        self.saved_hint = QLabel("")
        self.saved_hint.setObjectName("okLabel")
        save_row.addWidget(self.saved_hint, 1)
        b_save = QPushButton("保存设置")
        b_save.setProperty("cssClass", "primary")
        b_save.clicked.connect(self.save_settings)
        save_row.addWidget(b_save)
        lay.addLayout(save_row)

    def _pick(self, edit: QLineEdit, title: str):
        d = QFileDialog.getExistingDirectory(self, title, edit.text() or "")
        if d:
            edit.setText(d)

    def _open_dir(self, path):
        import os

        os.makedirs(path, exist_ok=True)
        os.startfile(path)  # noqa: S606

    def _test_mirror(self):
        # 后台线程测试，UI 不冻结（与下载页共用同一 worker）
        from .mirror_test import MirrorTestWorker

        self.test_result.setText("正在测试…")
        self._test_worker = MirrorTestWorker(self.endpoint_edit.currentText().strip(), self)
        self._test_worker.done.connect(self._on_mirror_tested)
        self._test_worker.start()

    def _on_mirror_tested(self, result: dict):
        icon = "✅" if result["ok"] else "❌"
        latency = f"{result['latency_ms']:.0f} ms · " if result.get("latency_ms") else ""
        self.test_result.setText(f"{icon} HTTP {result['status'] or '—'} · {latency}{result['message']}")

    def _save_token(self):
        self.token_store.save(self.token_edit.text())
        self.saved_hint.setText("Token 已保存。")
        self.token_edit.clear()

    def _clear_token(self):
        self.token_store.clear()
        self.token_edit.clear()
        self.token_edit.setPlaceholderText("hf_xxxxxxxx（留空表示不使用）")
        self.saved_hint.setText("Token 已清除。")

    def _clean_incomplete(self):
        s = self.settings_service.settings
        files = cache_utils.find_incomplete_files(s.cache_dir, [s.local_dir])
        total = cache_utils.incomplete_total_size(files)
        if not files:
            QMessageBox.information(self, "清理临时文件", "没有找到未完成的临时文件。")
            return
        ret = QMessageBox.question(
            self, "清理临时文件",
            f"找到 {len(files)} 个未完成的临时文件，共 {total / 1024**3:.2f} GB。\n"
            "删除后这些任务需要从头重新下载。确定删除吗？",
        )
        if ret == QMessageBox.Yes:
            removed = 0
            for f in files:
                try:
                    f.unlink()
                    removed += 1
                except OSError as exc:
                    logger.warning("删除临时文件失败 %s：%s", f, exc)
            QMessageBox.information(self, "清理临时文件", f"已删除 {removed} 个临时文件。")

    def save_settings(self):
        s: AppSettings = self.settings_service.settings
        s.endpoint = self.endpoint_edit.currentText().strip().rstrip("/")
        s.download_mode = self.mode_combo.currentData()
        s.local_dir = self.local_dir_edit.text().strip()
        s.cache_dir = self.cache_dir_edit.text().strip()
        s.max_workers = self.workers_spin.value()
        s.max_retries = self.retries_spin.value()
        s.theme = self.theme_combo.currentData()
        s.close_action = self.close_combo.currentData()
        try:
            self.settings_service.save()
        except OSError:
            QMessageBox.warning(self, "保存失败", "无法写入设置文件（磁盘或权限问题）。")
            return
        self.saved_hint.setText("设置已保存。")
        self.settings_saved.emit()
