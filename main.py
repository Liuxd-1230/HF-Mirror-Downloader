"""程序入口。

注意：环境变量必须在导入 huggingface_hub 之前设置（constants 在导入期读取）。
"""

import os
import sys


def _setup_env() -> None:
    # 镜像不支持 xet 存储；禁用后走可断点续传的普通 HTTP 下载
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")


def main() -> int:
    _setup_env()

    # faulthandler：windowed exe 崩溃也能留下线索
    try:
        import faulthandler

        from app.services.paths import logs_dir

        logs_dir().mkdir(parents=True, exist_ok=True)
        faulthandler.enable(open(logs_dir() / "crash.log", "a", encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass

    from app.services.logging_setup import setup_logging

    setup_logging(token_provider=None)

    try:
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QApplication

        from app import APP_VERSION
        from app.core.secure_store import TokenStore
        from app.services.paths import config_dir
        from app.ui.main_window import MainWindow
        from app.ui.theme import stylesheet
    except Exception:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        return 1

    from logging import getLogger

    logger = getLogger(__name__)
    logger.info("启动 %s v%s（Python %s）", "HF Mirror Downloader", APP_VERSION, sys.version.split()[0])

    app = QApplication(sys.argv)
    app.setApplicationName("HF Mirror Downloader")
    app.setFont(QFont("Microsoft YaHei UI", 9))

    window = MainWindow()
    token_provider = window.token_store.load
    # 补挂 Token 脱敏（此时才知道 TokenStore）
    from app.services.logging_setup import TokenRedactFilter

    for h in getLogger().handlers:
        h.addFilter(TokenRedactFilter(token_provider))

    theme = window.settings_service.settings.theme
    app.setStyleSheet(stylesheet(theme))
    window.show()

    # 演示模式（无网络时检查 UI）：HFM_DEMO=1
    if os.environ.get("HFM_DEMO") == "1":
        window.download_page.load_demo_data()

    for arg in sys.argv[1:]:
        if arg.startswith("http") or ("/" in arg and " " not in arg):
            window.download_page.input_edit.setText(arg)
            window.download_page.on_parse_clicked()
            break

    logger.info("进入主循环")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
