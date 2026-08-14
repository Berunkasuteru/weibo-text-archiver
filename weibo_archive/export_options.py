from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum

from .models import Archive, Post, TimestampProvenance


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


@dataclass(frozen=True)
class CustomFilterOptions:
    include_original: bool = True
    include_reposts: bool = True
    keywords: tuple[str, ...] = ()
    start_date: date | None = None
    end_date: date | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.include_original, bool):
            raise TypeError("include_original must be a bool")
        if not isinstance(self.include_reposts, bool):
            raise TypeError("include_reposts must be a bool")
        if not self.include_original and not self.include_reposts:
            raise ValueError("原创和转发至少选择一种。")
        if not isinstance(self.keywords, tuple) or any(
            not isinstance(keyword, str) or not keyword for keyword in self.keywords
        ):
            raise TypeError("keywords must be a tuple of non-empty strings")
        if self.start_date is not None and not isinstance(self.start_date, date):
            raise TypeError("start_date must be a date or None")
        if self.end_date is not None and not isinstance(self.end_date, date):
            raise TypeError("end_date must be a date or None")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("自定义开始日期不能晚于结束日期。")

    @property
    def time_filter_active(self) -> bool:
        return self.start_date is not None or self.end_date is not None


@dataclass(frozen=True)
class CustomFilterReport:
    fetched_count: int
    matched_count: int
    unknown_timestamp_count: int


@dataclass(frozen=True)
class ExportSelection:
    preset: ExportPreset
    options: ExportOptions
    filename_suffix: str
    custom_filter: CustomFilterOptions | None = None

    @property
    def label(self) -> str:
        if self.preset is ExportPreset.FULL_ARCHIVE:
            return "完整归档"
        if self.preset is ExportPreset.AI_COMPACT:
            return "AI 分析版"
        return "自定义筛选"


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
            raise ValueError("AI 分析版预设与输出配置不一致。")
        return "AI分析版"
    if preset is ExportPreset.CUSTOM:
        return "自定义_完整" if options.layout is ExportLayout.FULL else "自定义_AI分析"
    raise ValueError("未知导出内容预设。")


def build_export_selections(
    *,
    include_full: bool,
    include_ai: bool,
    include_custom: bool,
    custom_options: ExportOptions,
    custom_filter: CustomFilterOptions,
) -> tuple[ExportSelection, ...]:
    selections = []
    for enabled, preset, options, filter_options in (
        (include_full, ExportPreset.FULL_ARCHIVE, FULL_ARCHIVE_OPTIONS, None),
        (include_ai, ExportPreset.AI_COMPACT, AI_COMPACT_OPTIONS, None),
        (include_custom, ExportPreset.CUSTOM, custom_options, custom_filter),
    ):
        if enabled:
            selections.append(
                ExportSelection(
                    preset=preset,
                    options=options,
                    filename_suffix=filename_suffix_for_selection(preset, options),
                    custom_filter=filter_options,
                )
            )
    if not selections:
        raise ValueError("至少选择一种输出。")
    return tuple(selections)


def parse_filter_terms(value: str) -> tuple[str, ...]:
    terms = []
    seen = set()
    for candidate in re.split(r"[,，\r\n]+", value or ""):
        term = candidate.strip()
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            terms.append(term)
    return tuple(terms)


def _post_search_text(post: Post) -> str:
    parts = [post.text if post.text is not None else post.text_preview or ""]
    if post.retweet is not None:
        retweet = post.retweet
        parts.append(
            retweet.text if retweet.text is not None else retweet.text_preview or ""
        )
    return "\n".join(parts).casefold()


def filter_archive(
    archive: Archive,
    options: CustomFilterOptions,
) -> tuple[Archive, CustomFilterReport]:
    matched = []
    unknown_timestamp_count = 0
    keyword_keys = tuple(keyword.casefold() for keyword in options.keywords)

    for post in archive.posts:
        is_repost = post.retweet is not None
        if is_repost and not options.include_reposts:
            continue
        if not is_repost and not options.include_original:
            continue
        if keyword_keys and not any(
            keyword in _post_search_text(post) for keyword in keyword_keys
        ):
            continue

        if options.time_filter_active:
            if (
                post.created_at is None
                or post.created_at_provenance
                not in {
                    TimestampProvenance.SOURCE_OFFSET,
                    TimestampProvenance.SOURCE_WALL,
                }
            ):
                unknown_timestamp_count += 1
                continue
            post_date = post.created_at.date()
            if options.start_date is not None and post_date < options.start_date:
                continue
            if options.end_date is not None and post_date > options.end_date:
                continue

        matched.append(post)

    report = CustomFilterReport(
        fetched_count=len(archive.posts),
        matched_count=len(matched),
        unknown_timestamp_count=unknown_timestamp_count,
    )
    return replace(archive, posts=tuple(matched)), report


def filter_summary(options: CustomFilterOptions) -> str:
    if options.include_original and options.include_reposts:
        post_type = "原创+转发"
    elif options.include_original:
        post_type = "仅原创"
    else:
        post_type = "仅转发"
    parts = [post_type]
    if options.keywords:
        parts.append("关键词：" + "、".join(options.keywords))
    if options.time_filter_active:
        start = options.start_date.isoformat() if options.start_date else "不限"
        end = options.end_date.isoformat() if options.end_date else "不限"
        parts.append(f"日期：{start}～{end}")
    return " · ".join(parts)


def filter_report_notice(report: CustomFilterReport) -> str:
    notice = f"匹配 {report.matched_count} / 本次抓取 {report.fetched_count}"
    if report.unknown_timestamp_count:
        notice += (
            f"；{report.unknown_timestamp_count} 条记录时间未知或依据未验证，"
            "无法用于时间筛选"
        )
    return notice


def options_summary(options: ExportOptions) -> str:
    layout = "完整排版" if options.layout is ExportLayout.FULL else "AI 分析排版"
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
    layout = "完整" if options.layout is ExportLayout.FULL else "AI分析"
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
