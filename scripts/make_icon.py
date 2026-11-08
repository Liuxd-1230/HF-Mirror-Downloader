"""生成应用图标（assets/icon.png 与 assets/app.ico）。

使用 PySide6 离屏绘制：深蓝圆角方块 + 向下箭头。
用法：python scripts/make_icon.py
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap

_app = QGuiApplication(sys.argv)  # noqa: F841 - QPixmap 需要先有 QGuiApplication


def draw_pixmap(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0

    # 圆角背景
    path = QPainterPath()
    path.addRoundedRect(2 * s, 2 * s, 60 * s, 60 * s, 14 * s, 14 * s)
    grad = QColor("#3b6fd4")
    p.fillPath(path, grad)

    # 下箭头
    pen = QPen(QColor("white"), 7 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.drawLine(QPointF(32 * s, 16 * s), QPointF(32 * s, 40 * s))
    p.drawLine(QPointF(32 * s, 40 * s), QPointF(20 * s, 28 * s))
    p.drawLine(QPointF(32 * s, 40 * s), QPointF(44 * s, 28 * s))
    # 底部托盘
    p.drawLine(QPointF(18 * s, 48 * s), QPointF(46 * s, 48 * s))
    p.end()
    return pm


def main() -> int:
    pm256 = draw_pixmap(256)
    pm256.save(str(ASSETS / "icon.png"), "PNG")

    # ICO：多尺寸
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(draw_pixmap(size))
    ok = icon.pixmap(256).save(str(ASSETS / "app.ico"))
    print("icon.ico written:", ok, "→", ASSETS / "app.ico")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
