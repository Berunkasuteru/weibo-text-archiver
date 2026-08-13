from __future__ import annotations

import os
import sys
from pathlib import Path


def resource_path(relative_path: str | Path) -> Path:
    """Resolve a tracked asset in source and PyInstaller bundle modes."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    root = Path(bundle_root) if bundle_root else Path(__file__).resolve().parents[1]
    return root / relative_path


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
APP_ICON_PNG = resource_path("assets/app_icon.png")
