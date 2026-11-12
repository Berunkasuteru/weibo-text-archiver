"""Persistent storage for non-sensitive UI preferences."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from . import paths
from .export_options import CustomFilterOptions, DateFormat, ExportLayout, ExportOptions
from .models import RangeMode, VisibilityState

SCHEMA_VERSION = 1
_ALLOWED_RANGE_MODES = frozenset(
    {RangeMode.ALL.value, RangeMode.RECENT.value, RangeMode.SINCE.value}
)


@dataclass(frozen=True)
class UiPreferences:
    """Validated non-sensitive UI defaults restored across launches."""

    output_dir: str | None = None
    range_mode: str = RangeMode.ALL.value
    recent_count: str = "1000"
    since_date: str = ""
    full_output: bool = True
    ai_output: bool = True
    custom_output: bool = False
    ai_followers: bool = False
    ai_friends: bool = False
    ai_private: bool = False
    custom_options: ExportOptions = field(default_factory=ExportOptions)
    custom_filter: CustomFilterOptions = field(default_factory=CustomFilterOptions)


def _valid_output_dir(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or "\0" in stripped:
        return None
    try:
        candidate = Path(stripped).expanduser()
        if not candidate.is_absolute():
            return None
        anchor = Path(candidate.anchor) if candidate.anchor else candidate
        if not candidate.exists() and not anchor.exists():
            return None
    except (OSError, ValueError):
        return None
    return stripped


def _optional_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _optional_iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_custom_options(raw: Any) -> ExportOptions:
    default = ExportOptions()
    if not isinstance(raw, dict):
        return default
    layout = default.layout
    try:
        if isinstance(raw.get("layout"), str):
            layout = ExportLayout(raw["layout"])
    except ValueError:
        layout = default.layout
    date_format = default.date_format
    try:
        if isinstance(raw.get("date_format"), str):
            date_format = DateFormat(raw["date_format"])
    except ValueError:
        date_format = default.date_format
    return ExportOptions(
        layout=layout,
        include_source=_optional_bool(raw.get("include_source"), default.include_source),
        include_location=_optional_bool(
            raw.get("include_location"), default.include_location
        ),
        include_engagement=_optional_bool(
            raw.get("include_engagement"), default.include_engagement
        ),
        date_format=date_format,
    )


def _parse_custom_filter(raw: Any) -> CustomFilterOptions:
    default = CustomFilterOptions()
    if not isinstance(raw, dict):
        return default
    keywords_raw = raw.get("keywords")
    if isinstance(keywords_raw, list):
        keywords = tuple(
            item.strip()
            for item in keywords_raw
            if isinstance(item, str) and item.strip()
        )
    else:
        keywords = default.keywords
    vis_raw = raw.get("visibilities")
    if isinstance(vis_raw, list):
        parsed_vis = []
        for item in vis_raw:
            if isinstance(item, str):
                try:
                    parsed_vis.append(VisibilityState(item))
                except ValueError:
                    pass
        visibilities = frozenset(parsed_vis) if parsed_vis else default.visibilities
    else:
        visibilities = default.visibilities
    try:
        return CustomFilterOptions(
            include_original=_optional_bool(
                raw.get("include_original"), default.include_original
            ),
            include_reposts=_optional_bool(
                raw.get("include_reposts"), default.include_reposts
            ),
            keywords=keywords,
            start_date=_optional_iso_date(raw.get("start_date")),
            end_date=_optional_iso_date(raw.get("end_date")),
            visibility_filter_enabled=_optional_bool(
                raw.get("visibility_filter_enabled"),
                default.visibility_filter_enabled,
            ),
            visibilities=visibilities,
        )
    except ValueError:
        return default


def load_ui_settings(path: Path | None = None) -> UiPreferences:
    """Load persisted UI settings, falling back safely on missing or corrupt files."""
    target = paths.SETTINGS_FILE if path is None else Path(path)
    default = UiPreferences()
    try:
        if not target.is_file():
            return default
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return default
    if not isinstance(raw, dict):
        return default

    range_mode = raw.get("range_mode")
    if not isinstance(range_mode, str) or range_mode not in _ALLOWED_RANGE_MODES:
        range_mode = default.range_mode

    recent_raw = raw.get("recent_count")
    if (
        isinstance(recent_raw, str)
        and recent_raw.strip().isdigit()
        and int(recent_raw.strip()) > 0
    ):
        recent_count = recent_raw.strip()
    else:
        recent_count = default.recent_count

    since_parsed = _optional_iso_date(raw.get("since_date"))
    since_date = since_parsed.isoformat() if since_parsed is not None else default.since_date

    return UiPreferences(
        output_dir=_valid_output_dir(raw.get("output_dir")),
        range_mode=range_mode,
        recent_count=recent_count,
        since_date=since_date,
        full_output=_optional_bool(raw.get("full_output"), default.full_output),
        ai_output=_optional_bool(raw.get("ai_output"), default.ai_output),
        custom_output=_optional_bool(raw.get("custom_output"), default.custom_output),
        ai_followers=_optional_bool(raw.get("ai_followers"), default.ai_followers),
        ai_friends=_optional_bool(raw.get("ai_friends"), default.ai_friends),
        ai_private=_optional_bool(raw.get("ai_private"), default.ai_private),
        custom_options=_parse_custom_options(raw.get("custom_options")),
        custom_filter=_parse_custom_filter(raw.get("custom_filter")),
    )


def save_ui_settings(prefs: UiPreferences, path: Path | None = None) -> None:
    """Atomically persist non-sensitive UI preferences without failing the UI."""
    target = paths.SETTINGS_FILE if path is None else Path(path)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "output_dir": prefs.output_dir,
        "range_mode": prefs.range_mode,
        "recent_count": prefs.recent_count,
        "since_date": prefs.since_date,
        "full_output": prefs.full_output,
        "ai_output": prefs.ai_output,
        "custom_output": prefs.custom_output,
        "ai_followers": prefs.ai_followers,
        "ai_friends": prefs.ai_friends,
        "ai_private": prefs.ai_private,
        "custom_options": {
            "layout": prefs.custom_options.layout.value,
            "include_source": prefs.custom_options.include_source,
            "include_location": prefs.custom_options.include_location,
            "include_engagement": prefs.custom_options.include_engagement,
            "date_format": prefs.custom_options.date_format.value,
        },
        "custom_filter": {
            "include_original": prefs.custom_filter.include_original,
            "include_reposts": prefs.custom_filter.include_reposts,
            "keywords": list(prefs.custom_filter.keywords),
            "start_date": (
                prefs.custom_filter.start_date.isoformat()
                if prefs.custom_filter.start_date is not None
                else None
            ),
            "end_date": (
                prefs.custom_filter.end_date.isoformat()
                if prefs.custom_filter.end_date is not None
                else None
            ),
            "visibility_filter_enabled": prefs.custom_filter.visibility_filter_enabled,
            "visibilities": sorted(v.value for v in prefs.custom_filter.visibilities),
        },
    }
    temp_path: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            prefix=".settings-", suffix=".tmp", dir=target.parent
        )
        temp_path = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        temp_path = None
    except OSError:
        pass
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
