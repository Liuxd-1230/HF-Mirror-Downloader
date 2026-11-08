"""设置持久化（settings.json）与运行期共享设置。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from ..models import AppSettings
from .paths import config_dir

logger = logging.getLogger(__name__)


class SettingsService:
    def __init__(self):
        self.path = Path(config_dir()) / "settings.json"
        self.settings = AppSettings()
        self.load()

    def load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            data = {}
        valid = {f.name for f in fields(AppSettings)}
        for k, v in data.items():
            if k in valid and v is not None:
                try:
                    setattr(self.settings, k, type(getattr(self.settings, k))(v))
                except (TypeError, ValueError):
                    logger.warning("忽略非法设置项 %s=%r", k, v)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(asdict(self.settings), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("设置已保存：%s", self.path)
        except OSError as exc:
            logger.error("保存设置失败：%s", exc)
            raise
