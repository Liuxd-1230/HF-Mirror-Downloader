"""日志初始化：滚动文件 + 控制台，Token 一律脱敏。"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler

from .paths import logs_file

_TOKEN_RE = re.compile(r"\bhf_[A-Za-z0-9_\-]{8,}")


class TokenRedactFilter(logging.Filter):
    """把日志文本中的 hf_ 开头令牌及用户配置的 Token 值替换为 ***。"""

    def __init__(self, token_provider=None):
        super().__init__()
        self._token_provider = token_provider

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            changed = False
            if "hf_" in msg:
                new = _TOKEN_RE.sub("hf_***", msg)
                if new != msg:
                    msg, changed = new, True
            if self._token_provider is not None:
                token = self._token_provider()
                if token and len(token) >= 8 and token in msg:
                    msg = msg.replace(token, "***")
                    changed = True
            if changed:
                record.msg = msg
                record.args = ()
        except Exception:  # noqa: BLE001 - 日志脱敏绝不抛异常
            pass
        return True


def setup_logging(level: int = logging.INFO, token_provider=None) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    redact = TokenRedactFilter(token_provider)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    log_path = logs_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.addFilter(redact)
    root.addHandler(fh)

    # 打包成 windowed exe 时 sys.stderr 为 None
    if sys.stderr is not None:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(fmt)
        ch.addFilter(redact)
        root.addHandler(ch)

    # 降噪
    for noisy in ("httpx", "httpcore", "urllib3", "filelock", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
