from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from enum import Enum
from pathlib import Path

from .models import Archive
from .paths import CACHE_DIR


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(type(value).__name__)


def save_normalized_archive(archive: Archive) -> Path:
    """
    Save only the normalized text archive model.

    No cookie, media URL, browser data, or raw response headers are stored here.
    """
    folder = CACHE_DIR / archive.profile.id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "last_success.json"
    temp = folder / "last_success.json.tmp"

    payload = {
        "schema_version": 2,
        **asdict(archive),
        "integrity": asdict(archive.integrity),
    }
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    temp.replace(path)
    return path
