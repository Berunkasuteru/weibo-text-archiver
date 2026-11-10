from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from datetime import date, datetime
from enum import Enum
from pathlib import Path

from .models import Archive
from .paths import CACHE_DIR


SCHEMA_VERSION = 5


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
    fd, raw_temp = tempfile.mkstemp(
        prefix=".last_success-", suffix=".json.tmp", dir=folder
    )
    temp = Path(raw_temp)
    try:
        payload = {
            "schema_version": SCHEMA_VERSION,
            **asdict(archive),
            "integrity": asdict(archive.integrity),
        }
        text = json.dumps(
            payload, ensure_ascii=False, indent=2, default=_json_default
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = -1
            handle.write(text)
        temp.replace(path)
        return path
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
