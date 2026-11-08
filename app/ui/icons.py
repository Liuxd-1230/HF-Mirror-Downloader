"""图标助手：基于 qtawesome 的统一图标入口。

颜色约定：普通图标用灰（深浅主题都可读），强调色/白色由调用方指定。
主题切换后调用 refresh 钩子重建受影响的图标。
"""

from __future__ import annotations

import qtawesome as qta

MUTED = "#97a0ab"       # 深浅主题都可读的中性灰
WHITE = "#ffffff"
ACCENT_FALLBACK = "#4f8cff"


def icon(name: str, color: str | None = None) -> object:
    """mdi 图标名（如 'mdi.download'）→ QIcon。"""
    try:
        return qta.icon(name, color=color or MUTED)
    except Exception:  # noqa: BLE001 - 图标失败不阻断启动
        return None


# 常用图标名集中管理
NAV_DOWNLOAD = "mdi.download"
NAV_HISTORY = "mdi.history"
NAV_SETTINGS = "mdi.cog"
NAV_ABOUT = "mdi.information-outline"
MINIMIZE = "mdi.minus"
MAXIMIZE = "mdi.window-maximize"
RESTORE = "mdi.window-restore"
CLOSE = "mdi.close"
SEARCH = "mdi.magnify"
LINK = "mdi.link-variant"
CLIPBOARD = "mdi.clipboard-text-outline"
FOLDER = "mdi.folder-open-outline"
DOWNLOAD = "mdi.download"
PAUSE = "mdi.pause"
PLAY = "mdi.play"
CLOSE_SM = "mdi.close-thick"
REFRESH = "mdi.refresh"
SPEED = "mdi.speedometer"
HARD_DRIVE = "mdi.harddisk"
CHECK = "mdi.check"
KEY = "mdi.key-variant"
ALERT = "mdi.alert-circle-outline"
SWITCH = "mdi.swap-horizontal"
