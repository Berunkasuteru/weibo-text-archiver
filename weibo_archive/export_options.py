from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExportLayout(str, Enum):
    FULL = "full"
    AI = "ai"


class DateFormat(str, Enum):
    DATE_TIME_MINUTE = "date_time_minute"
    DATE_ONLY = "date_only"


class ExportPreset(str, Enum):
    FULL_ARCHIVE = "full_archive"
    AI_COMPACT = "ai_compact"
    CUSTOM = "custom"


@dataclass(frozen=True)
class ExportOptions:
    layout: ExportLayout = ExportLayout.FULL
    include_source: bool = True
    include_location: bool = True
    include_engagement: bool = True
    date_format: DateFormat = DateFormat.DATE_TIME_MINUTE

    def __post_init__(self) -> None:
        if not isinstance(self.layout, ExportLayout):
            raise TypeError("layout must be an ExportLayout")
        if not isinstance(self.date_format, DateFormat):
            raise TypeError("date_format must be a DateFormat")
        for name in ("include_source", "include_location", "include_engagement"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a bool")


FULL_ARCHIVE_OPTIONS = ExportOptions(layout=ExportLayout.FULL)
AI_COMPACT_OPTIONS = ExportOptions(layout=ExportLayout.AI)


def options_for_preset(
    preset: ExportPreset,
    custom_options: ExportOptions | None = None,
) -> ExportOptions:
    """Resolve a GUI choice before a task starts."""
    if preset is ExportPreset.FULL_ARCHIVE:
        return FULL_ARCHIVE_OPTIONS
    if preset is ExportPreset.AI_COMPACT:
        return AI_COMPACT_OPTIONS
    if preset is ExportPreset.CUSTOM:
        if custom_options is None:
            raise ValueError("自定义导出尚未设置。")
        return custom_options
    raise ValueError("未知导出内容预设。")


def filename_suffix_for_selection(
    preset: ExportPreset,
    options: ExportOptions,
) -> str:
    """Resolve the user-facing filename before entering the worker."""
    if preset is ExportPreset.FULL_ARCHIVE:
        if options != FULL_ARCHIVE_OPTIONS:
            raise ValueError("完整归档预设与输出配置不一致。")
        return "完整"
    if preset is ExportPreset.AI_COMPACT:
        if options != AI_COMPACT_OPTIONS:
            raise ValueError("AI 精简预设与输出配置不一致。")
        return "AI精简版"
    if preset is ExportPreset.CUSTOM:
        return "自定义_完整" if options.layout is ExportLayout.FULL else "自定义_AI精简"
    raise ValueError("未知导出内容预设。")


def options_summary(options: ExportOptions) -> str:
    layout = "完整排版" if options.layout is ExportLayout.FULL else "AI 精简排版"
    included = []
    if options.include_source:
        included.append("来源")
    if options.include_location:
        included.append("位置")
    if options.include_engagement:
        included.append("转评赞")
    fields = "、".join(included) if included else "仅核心正文与日期"
    date_label = (
        "日期+时间" if options.date_format is DateFormat.DATE_TIME_MINUTE else "仅日期"
    )
    return f"{layout} · {fields} · {date_label}"


def options_provenance(options: ExportOptions) -> str:
    layout = "完整" if options.layout is ExportLayout.FULL else "AI精简"
    source = "包含" if options.include_source else "省略"
    location = "包含" if options.include_location else "省略"
    engagement = "包含" if options.include_engagement else "省略"
    date_label = (
        "YYYY-MM-DD HH:mm"
        if options.date_format is DateFormat.DATE_TIME_MINUTE
        else "YYYY-MM-DD"
    )
    return (
        f"排版={layout}｜来源/设备={source}｜发布位置={location}"
        f"｜转评赞={engagement}｜日期={date_label}"
    )
