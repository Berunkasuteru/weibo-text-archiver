from __future__ import annotations

import os
from pathlib import Path


def app_data_dir() -> Path:
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "WeiboTextExporter"
        return Path.home() / "AppData" / "Local" / "WeiboTextExporter"
    return Path.home() / ".weibo_text_exporter"


APP_DATA_DIR = app_data_dir()
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

COOKIE_FILE = APP_DATA_DIR / "cookie.txt"
ERROR_FILE = APP_DATA_DIR / "last_error.txt"
CACHE_DIR = APP_DATA_DIR / "v7_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DESKTOP = Path.home() / "Desktop"
DEFAULT_OUTPUT_DIR = (DESKTOP if DESKTOP.exists() else Path.home()) / "微博文字备份"
