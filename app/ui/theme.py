"""深色 / 浅色主题（QSS）。设计基调：扁平、深灰、蓝色强调。"""

from __future__ import annotations

_DARK = """
* { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 13px; }
QMainWindow, QDialog { background: #1e1f22; }
QWidget { color: #e4e6eb; }
QLabel { background: transparent; }
QLabel#sidebarTitle { color: #9aa0a8; font-size: 15px; font-weight: 600; }
QLabel#mutedLabel { color: #9aa0a8; }
QLabel#okLabel { color: #3fbf7f; }
QLabel#warnLabel { color: #e5a23c; }
QLabel#errLabel { color: #ef5b5b; }

QListWidget#sidebar {
    background: #17181a; border: none; padding: 12px 8px; outline: 0;
    font-size: 14px;
}
QListWidget#sidebar::item { padding: 12px 14px; margin: 3px 6px; border-radius: 8px; color: #c8ccd2; }
QListWidget#sidebar::item:hover { background: #22242a; }
QListWidget#sidebar::item:selected { background: #2f4a7a; color: #ffffff; }

QPushButton {
    background: #2a2d33; border: 1px solid #3a3e46; border-radius: 6px;
    padding: 7px 16px; color: #e4e6eb;
}
QPushButton:hover { background: #33373f; border-color: #4a4f59; }
QPushButton:pressed { background: #272a30; }
QPushButton:disabled { color: #6b7078; background: #232529; border-color: #2e3138; }
QPushButton[cssClass="primary"] {
    background: #3b6fd4; border: 1px solid #3b6fd4; color: white; font-weight: 600;
}
QPushButton[cssClass="primary"]:hover { background: #4a7de0; }
QPushButton[cssClass="primary"]:pressed { background: #3363c2; }
QPushButton[cssClass="primary"]:disabled { background: #2a3550; border-color: #2a3550; color: #7c86a0; }
QPushButton[cssClass="danger"] { background: transparent; border-color: #5a3238; color: #ef5b5b; }
QPushButton[cssClass="danger"]:hover { background: #3a2428; }
QPushButton[cssClass="flat"] { background: transparent; border-color: transparent; }
QPushButton[cssClass="flat"]:hover { background: #2a2d33; border-color: #3a3e46; }
QPushButton[cssClass="chip"] { padding: 4px 12px; border-radius: 12px; }
QPushButton[cssClass="chip"]:checked { background: #2f4a7a; border-color: #3f63a8; color: white; }

QLineEdit, QComboBox, QSpinBox {
    background: #26282e; border: 1px solid #3a3e46; border-radius: 6px; padding: 6px 10px;
    selection-background-color: #3b6fd4;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #4f7fe0; }
QLineEdit:disabled { color: #6b7078; background: #22242a; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #26282e; border: 1px solid #3a3e46; selection-background-color: #2f4a7a; }

QTreeWidget, QTreeView, QTableView, QListWidget {
    background: #202226; alternate-background-color: #24262b; border: 1px solid #33363c;
    border-radius: 8px; padding: 2px;
    outline: 0;
}
QTreeWidget::item, QTreeView::item { padding: 5px 4px; }
QTreeWidget::item:selected, QTreeView::item:selected, QListWidget::item:selected { background: #2f4a7a; }
QHeaderView::section {
    background: #26282e; color: #aab0b8; border: none; border-bottom: 1px solid #33363c;
    padding: 6px 8px; font-weight: 600;
}

QProgressBar {
    background: #2a2d33; border: none; border-radius: 6px; height: 12px; text-align: center;
    color: #cfd3da; font-size: 10px;
}
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #3f7de0, stop:1 #5b9cf0); border-radius: 6px; }
QProgressBar#overallBar { height: 18px; border-radius: 9px; font-size: 11px; }
QProgressBar#overallBar::chunk { border-radius: 9px; }

QFrame#card { background: #232529; border: 1px solid #33363c; border-radius: 10px; }
QFrame#cardLight { background: #26282e; border: 1px solid #33363c; border-radius: 10px; }
QFrame#line { background: #33363c; max-height: 1px; border: none; }

QGroupBox { border: 1px solid #33363c; border-radius: 8px; margin-top: 12px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #9aa0a8; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #3a3e46; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #4a4f59; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #3a3e46; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QMenu { background: #26282e; border: 1px solid #3a3e46; padding: 4px; }
QMenu::item { padding: 6px 22px; border-radius: 4px; }
QMenu::item:selected { background: #2f4a7a; }

QToolTip { background: #2a2d33; color: #e4e6eb; border: 1px solid #3a3e46; padding: 4px; }

QCheckBox::indicator, QTreeWidget::indicator { width: 16px; height: 16px; }
QToolButton { background: transparent; border: none; padding: 3px; }
QToolButton:hover { background: #2a2d33; border-radius: 4px; }

/* ---- B 方向：标题栏 / 窗口按钮 / 侧栏 / 分割条 ---- */
QMainWindow { border: 1px solid #2b2f38; }
#titlebar { background: #17181a; border-bottom: 1px solid #2b2f38; }
#titlebarName { color: #c8ccd2; font-size: 13px; font-weight: 600; }
QPushButton[cssClass="winbtn"] {
    background: transparent; border: none; border-radius: 7px; padding: 0;
}
QPushButton[cssClass="winbtn"]:hover { background: #2a2d33; }
QPushButton#btnClose:hover { background: #a83232; }
QPushButton#btnClose:hover { color: white; }
#sidebarWidget { background: #17181a; border-right: 1px solid #26282e; }
#mirrorStatus { color: #9aa0a8; font-size: 12px; padding: 6px 2px; border-top: 1px solid #26282e; }
QSplitter::handle { background: #1e1f22; }
QSplitter::handle:hover { background: #2f4a7a; }
QProgressBar#overallBar { background: #2a2d33; border-radius: 5px; }
"""

_LIGHT = """
* { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 13px; }
QMainWindow, QDialog { background: #f5f6f8; }
QWidget { color: #22262b; }
QLabel#sidebarTitle { color: #5a6068; font-size: 15px; font-weight: 600; }
QLabel#mutedLabel { color: #6b7280; }
QLabel#okLabel { color: #178a56; }
QLabel#warnLabel { color: #b4762a; }
QLabel#errLabel { color: #c93a3a; }

QListWidget#sidebar { background: #ffffff; border: none; padding: 12px 8px; outline: 0; font-size: 14px; }
QListWidget#sidebar::item { padding: 12px 14px; margin: 3px 6px; border-radius: 8px; color: #3c424a; }
QListWidget#sidebar::item:hover { background: #eef0f3; }
QListWidget#sidebar::item:selected { background: #dce7fb; color: #1c4db8; }

QPushButton { background: #ffffff; border: 1px solid #d3d7dd; border-radius: 6px; padding: 7px 16px; }
QPushButton:hover { background: #f0f2f5; }
QPushButton:disabled { color: #a8aeb6; }
QPushButton[cssClass="primary"] { background: #3b6fd4; border-color: #3b6fd4; color: white; font-weight: 600; }
QPushButton[cssClass="primary"]:hover { background: #4a7de0; }
QPushButton[cssClass="primary"]:disabled { background: #9db4e2; border-color: #9db4e2; color: #eef2fb; }
QPushButton[cssClass="chip"] { padding: 4px 12px; border-radius: 12px; }
QPushButton[cssClass="chip"]:checked { background: #dce7fb; border-color: #9db9e8; color: #1c4db8; }

QLineEdit, QComboBox, QSpinBox { background: #ffffff; border: 1px solid #d3d7dd; border-radius: 6px; padding: 6px 10px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #3b6fd4; }

QTreeWidget, QTreeView, QTableView, QListWidget { background: #ffffff; alternate-background-color: #f7f8fa; border: 1px solid #e2e5ea; border-radius: 8px; }
QTreeWidget::item:selected, QTreeView::item:selected, QListWidget::item:selected { background: #dce7fb; }
QHeaderView::section { background: #f0f2f5; color: #5a6068; border: none; border-bottom: 1px solid #e2e5ea; padding: 6px 8px; font-weight: 600; }

QProgressBar { background: #e8eaee; border: none; border-radius: 6px; height: 12px; text-align: center; color: #444; font-size: 10px; }
QProgressBar::chunk { background: #3b6fd4; border-radius: 6px; }
QProgressBar#overallBar { height: 18px; border-radius: 9px; }
QProgressBar#overallBar::chunk { border-radius: 9px; }

QFrame#card { background: #ffffff; border: 1px solid #e2e5ea; border-radius: 10px; }
QFrame#cardLight { background: #fafbfc; border: 1px solid #e2e5ea; border-radius: 10px; }
QFrame#line { background: #e2e5ea; max-height: 1px; border: none; }

QGroupBox { border: 1px solid #e2e5ea; border-radius: 8px; margin-top: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #6b7280; }

QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: #c8cdd4; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:horizontal { background: #c8cdd4; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QMenu { background: #ffffff; border: 1px solid #d3d7dd; padding: 4px; }
QMenu::item { padding: 6px 22px; border-radius: 4px; }
QMenu::item:selected { background: #dce7fb; }
QToolTip { background: #ffffff; color: #22262b; border: 1px solid #d3d7dd; padding: 4px; }
QToolButton { background: transparent; border: none; }

/* ---- B 方向：标题栏 / 窗口按钮 / 侧栏 / 分割条 ---- */
QMainWindow { border: 1px solid #d3d7dd; }
#titlebar { background: #ffffff; border-bottom: 1px solid #e2e5ea; }
#titlebarName { color: #22262b; font-size: 13px; font-weight: 600; }
QPushButton[cssClass="winbtn"] {
    background: transparent; border: none; border-radius: 7px; padding: 0;
}
QPushButton[cssClass="winbtn"]:hover { background: #eceff3; }
QPushButton#btnClose:hover { background: #d64545; color: white; }
#sidebarWidget { background: #fafbfc; border-right: 1px solid #e2e5ea; }
#mirrorStatus { color: #6b7280; font-size: 12px; padding: 6px 2px; border-top: 1px solid #e2e5ea; }
QSplitter::handle { background: #f5f6f8; }
QSplitter::handle:hover { background: #dce7fb; }
"""


def stylesheet(theme: str) -> str:
    return _DARK if theme == "dark" else _LIGHT
