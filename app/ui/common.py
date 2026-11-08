"""常用 GUI 工具：打开文件夹/文件、复制、磁盘空间。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def open_in_explorer(path: str) -> None:
    """在资源管理器中打开文件所在目录并选中该文件；目录则直接打开。"""
    path = os.path.normpath(path)
    if os.path.isdir(path):
        os.startfile(path)  # noqa: S606 - Windows 资源管理器
    elif os.path.isfile(path):
        subprocess.Popen(["explorer", "/select,", path])
    else:
        # 文件不存在 → 打开其父目录
        parent = os.path.dirname(path)
        if os.path.isdir(parent):
            os.startfile(parent)  # noqa: S606


def open_path(path: str) -> None:
    path = os.path.normpath(path)
    if os.path.isfile(path) or os.path.isdir(path):
        os.startfile(path)  # noqa: S606


def disk_free(path: str) -> int | None:
    """返回 path 所在盘的剩余字节数；路径无效返回 None。"""
    try:
        drive = path or "C:\\"
        if not os.path.exists(drive):
            # 逐级向上找存在的父目录
            probe = os.path.abspath(drive)
            while probe and not os.path.exists(probe):
                parent = os.path.dirname(probe)
                if parent == probe:
                    return None
                probe = parent
            drive = probe
        return shutil.disk_usage(drive).free
    except OSError:
        return None


def app_icon():
    """程序化绘制应用图标（深蓝圆角方块 + 下箭头），托盘/窗口共用，无需资源文件。"""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = 1.0
    path = QPainterPath()
    path.addRoundedRect(2 * s, 2 * s, 60 * s, 60 * s, 14 * s, 14 * s)
    p.fillPath(path, QColor("#3b6fd4"))
    pen = QPen(QColor("white"), 7 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.drawLine(QPointF(32 * s, 16 * s), QPointF(32 * s, 40 * s))
    p.drawLine(QPointF(32 * s, 40 * s), QPointF(20 * s, 28 * s))
    p.drawLine(QPointF(32 * s, 40 * s), QPointF(44 * s, 28 * s))
    p.drawLine(QPointF(18 * s, 48 * s), QPointF(46 * s, 48 * s))
    p.end()
    return QIcon(pm)
