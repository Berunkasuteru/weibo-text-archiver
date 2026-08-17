from __future__ import annotations

import os
import sys
from pathlib import Path


def resource_path(relative_path: str | Path) -> Path:
    """Resolve a tracked asset in source and PyInstaller bundle modes."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    root = Path(bundle_root) if bundle_root else Path(__file__).resolve().parents[1]
    return root / relative_path


def application_dir(
    *,
    frozen: bool | None = None,
    executable: str | Path | None = None,
    source_root: str | Path | None = None,
) -> Path:
    """Return the portable application directory without creating anything."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if is_frozen:
        return Path(executable or sys.executable).resolve().parent
    return Path(source_root or Path(__file__).resolve().parents[1]).resolve()


def default_output_dir(**application_dir_options) -> Path:
    return application_dir(**application_dir_options) / "Archives"


def fallback_output_dir(*, home: str | Path | None = None) -> Path:
    return Path(home or Path.home()).resolve() / "Documents" / "WeiboTextArchiver" / "Archives"


def prepare_output_dir(requested: str | Path, *, fallback: str | Path | None = None) -> Path:
    """Create the requested directory, using a supplied fallback only on creation failure."""
    requested_path = Path(requested).expanduser()
    try:
        requested_path.mkdir(parents=True, exist_ok=True)
    except OSError as requested_error:
        if fallback is None:
            raise OSError(f"无法创建保存位置“{requested_path}”：{requested_error}") from requested_error

        fallback_path = Path(fallback).expanduser()
        try:
            fallback_path.mkdir(parents=True, exist_ok=True)
        except OSError as fallback_error:
            raise OSError(
                f"默认保存位置“{requested_path}”不可用；"
                f"备用保存位置“{fallback_path}”也不可用：{fallback_error}"
            ) from fallback_error
        return fallback_path
    return requested_path


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

APP_ICON_PNG = resource_path("assets/app_icon.png")
