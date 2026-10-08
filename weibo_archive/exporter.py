from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from . import markdown_v5 as md
from .ai_format import (
    _default_offset,
    estimate_tokens,
    part_period,
    render_ai_markdown,
    split_archive_for_parts,
)
from .export_options import (
    DateFormat,
    ExportLayout,
    ExportOptions,
    options_provenance,
)
from .models import Archive, ContentState, Post, RangeMode, Termination


# WEIBO_AI_1 stays renderable for comparison; new exports use WEIBO_AI_2.
AI_FORMAT_CURRENT = 2

_ALLOWED_FILENAME_SUFFIXES = {
    "完整",
    "AI分析版",
    "自定义_完整",
    "自定义_AI分析",
}


def _post_to_legacy(post: Post) -> dict:
    when = (
        post.created_at.isoformat(sep=" ", timespec="seconds")
        if post.created_at
        else ""
    )
    result = {
        "id": post.id,
        "bid": post.bid,
        "screen_name": post.author,
        "author_id": post.author_id,
        "text": post.text,
        "created_at": when.replace(" ", "T") if when else "",
        "full_created_at": when,
        "created_at_provenance": post.created_at_provenance.value,
        "visibility": post.visibility.state.value,
        "source": post.source,
        "location": post.location,
        "checkin": post.checkin,
        "reposts_count": post.engagement.reposts,
        "comments_count": post.engagement.comments,
        "attitudes_count": post.engagement.likes,
        # V5 renderer accepts lists and counts them without persisting media URLs.
        "pics": [f"image:{i+1}" for i in range(post.media.images)],
        "video_url": [f"video:{i+1}" for i in range(post.media.videos)],
        "article_url": "article:present" if post.media.article else "",
        "edited": post.edited,
        "edit_count": post.edit_count,
    }
    if post.retweet is not None:
        result["retweet"] = _post_to_legacy(post.retweet)
    if post.content_state is ContentState.INCOMPLETE:
        result.update(
            {
                "content_state": post.content_state.value,
                "text_preview": post.text_preview,
                "incomplete_reason": post.incomplete_reason.value,
            }
        )
    return result


def archive_to_legacy_data(archive: Archive) -> dict:
    p = archive.profile
    return {
        "user": {
            "id": p.id,
            "screen_name": p.screen_name,
            "description": p.description,
            "followers_count": p.followers_count,
            "follow_count": p.follow_count,
            "statuses_count": p.statuses_count,
            "location": p.location,
            "verified": p.verified,
            "verified_reason": p.verified_reason,
            "registration_time": p.registration_time,
        },
        "weibo": [_post_to_legacy(x) for x in archive.posts],
    }


def _termination_label(archive: Archive) -> str:
    t = archive.report.termination
    if t is Termination.NATURAL:
        return "自然结束"
    if t is Termination.TARGET_COUNT:
        return "达到指定条数"
    if t is Termination.SINCE_REACHED:
        return "达到起始日期"
    return t.value


def _range_filename_label(archive: Archive) -> str:
    r = archive.fetch_range
    if r.mode is RangeMode.ALL:
        end = archive.report.newest_reached or archive.fetched_at
        return f"全量快照_截至{end:%Y%m%d}"
    if r.mode is RangeMode.TRIAL:
        return f"测试导出{r.limit or 20}条"
    if r.mode is RangeMode.RECENT:
        return f"近{r.limit or len(archive.posts)}条"
    if r.mode is RangeMode.SINCE and r.since:
        return f"{r.since:%Y%m%d}起"
    return "自定义范围"


def _inject_full_provenance(
    text: str,
    archive: Archive,
    options: ExportOptions | None = None,
    selection_notice: str | None = None,
    visibility_scope: str | None = None,
) -> str:
    lines = text.splitlines()
    if not lines:
        return text
    meta = [
        "",
        f"> 导出范围：{archive.fetch_range.label()}",
        f"> 抓取终止：{_termination_label(archive)}",
        "> 快照时间（导出机器本地）："
        + archive.fetched_at.isoformat(sep=" ", timespec="minutes"),
        (
            "> 可见范围：未筛选。"
            if not visibility_scope or visibility_scope == "UNFILTERED"
            else f"> 可见范围筛选：{visibility_scope}"
        ),
        "> 可见范围语义：表示归档抓取时来源响应中观察到的元数据；"
        "不证明最初设置或此后未变化。嵌套转发的可见范围语义不作解释。",
    ]
    if options is not None:
        meta.append(f"> 输出配置：{options_provenance(options)}")
    if selection_notice:
        meta.append(f"> 自定义筛选：{selection_notice}")
    integrity = archive.integrity
    if integrity.incomplete_records:
        meta.append(
            "> 内容完整性："
            f"共 {integrity.total_posts:,} 条；"
            f"完整记录 {integrity.complete_records:,}；"
            f"不完整记录 {integrity.incomplete_records:,}"
            f"（顶层正文 {integrity.incomplete_top_level:,}，"
            f"转发原文 {integrity.incomplete_retweets:,}）"
        )
    return "\n".join(lines[:1] + meta + lines[1:]).rstrip() + "\n"


def _ai_provenance_lines(
    archive: Archive,
    options: ExportOptions | None = None,
    selection_notice: str | None = None,
    visibility_scope: str = "UNFILTERED",
    unknown_visibility_excluded_count: int = 0,
) -> list[str]:
    meta = (
        f"导出：{archive.fetch_range.label()}｜终止={_termination_label(archive)}"
        "｜快照（导出机器本地）="
        + archive.fetched_at.isoformat(sep=" ", timespec="minutes")
    )
    inserted = [meta, f"VISIBILITY_SCOPE={visibility_scope}"]
    if options is not None:
        inserted.append("输出配置：" + options_provenance(options))
    if selection_notice:
        inserted.append("自定义筛选：" + selection_notice)
    if unknown_visibility_excluded_count:
        inserted.append(
            f"{unknown_visibility_excluded_count} 条记录的可见范围无法确认，"
            "未纳入 AI 分析版。"
        )
    integrity = archive.integrity
    if integrity.incomplete_records:
        inserted.append(
            "完整性："
            f"TOTAL={integrity.total_posts}｜"
            f"COMPLETE_RECORDS={integrity.complete_records}｜"
            f"INCOMPLETE_RECORDS={integrity.incomplete_records}｜"
            f"TOP_INCOMPLETE={integrity.incomplete_top_level}｜"
            f"RETWEET_INCOMPLETE={integrity.incomplete_retweets}"
        )
    return inserted


def _ai2_provenance_lines(
    archive: Archive,
    options: ExportOptions,
    selection_notice: str | None = None,
    visibility_scope: str = "UNFILTERED",
    unknown_visibility_excluded_count: int = 0,
) -> list[str]:
    """Header facts for WEIBO_AI_2; keys are English, values are data."""
    fetch_range = archive.fetch_range
    if fetch_range.mode is RangeMode.SINCE and fetch_range.since:
        range_text = f"SINCE {fetch_range.since:%Y-%m-%d}"
    elif fetch_range.mode in (RangeMode.RECENT, RangeMode.TRIAL) and fetch_range.limit:
        range_text = f"{fetch_range.mode.name} {fetch_range.limit}"
    else:
        range_text = fetch_range.mode.name
    included = lambda flag: "included" if flag else "omitted"
    lines = [
        f"EXPORT: range={range_text}｜termination={archive.report.termination.name}"
        "｜snapshot (exporting machine local time)="
        + archive.fetched_at.isoformat(sep=" ", timespec="minutes"),
        f"VISIBILITY_SCOPE={visibility_scope}",
        f"OPTIONS: source={included(options.include_source)}"
        f"｜location={included(options.include_location)}"
        f"｜engagement={included(options.include_engagement)}"
        "｜date="
        + (
            "YYYY-MM-DD HH:mm"
            if options.date_format is DateFormat.DATE_TIME_MINUTE
            else "YYYY-MM-DD"
        ),
    ]
    if selection_notice:
        lines.append("CUSTOM_FILTER: " + selection_notice)
    if unknown_visibility_excluded_count:
        lines.append(
            f"VISIBILITY_UNKNOWN_EXCLUDED: {unknown_visibility_excluded_count} records "
            "had unconfirmed visibility and are not included."
        )
    integrity = archive.integrity
    if integrity.incomplete_records:
        lines.append(
            "INTEGRITY: "
            f"TOTAL={integrity.total_posts}｜"
            f"COMPLETE_RECORDS={integrity.complete_records}｜"
            f"INCOMPLETE_RECORDS={integrity.incomplete_records}｜"
            f"TOP_INCOMPLETE={integrity.incomplete_top_level}｜"
            f"RETWEET_INCOMPLETE={integrity.incomplete_retweets}"
        )
    return lines


def _inject_ai_provenance(
    text: str,
    archive: Archive,
    options: ExportOptions | None = None,
    selection_notice: str | None = None,
    visibility_scope: str = "UNFILTERED",
    unknown_visibility_excluded_count: int = 0,
) -> str:
    lines = text.splitlines()
    if not lines:
        return text
    inserted = _ai_provenance_lines(
        archive,
        options,
        selection_notice,
        visibility_scope,
        unknown_visibility_excluded_count,
    )
    return "\n".join(lines[:2] + inserted + lines[2:]).rstrip() + "\n"


def render_legacy_markdown(archive: Archive) -> tuple[str, str]:
    """Keep the accepted Alpha2/V5 renderer baseline directly testable."""
    data = archive_to_legacy_data(archive)
    uid = archive.profile.id

    full_text, _, count = md.build_markdown(data, uid)
    ai_text, _, ai_stats = md.build_ai_markdown(data, uid)

    if count != len(archive.posts):
        raise RuntimeError(
            f"导出前后条数不一致：模型 {len(archive.posts)} 条，渲染器 {count} 条。"
        )

    if ai_stats["count"] != count:
        raise RuntimeError("完整与 AI 渲染器的条数不一致。")

    return (
        _inject_full_provenance(full_text, archive),
        _inject_ai_provenance(ai_text, archive),
    )


def _atomic_write_text(
    final_path: Path,
    text: str,
    before_commit: Callable[[], None] | None = None,
) -> Path:
    """Atomically commit to the first available collision-safe final path."""
    fd, temp_name = tempfile.mkstemp(
        prefix=".weibo-export-",
        suffix=".tmp",
        dir=str(final_path.parent),
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(text, encoding="utf-8", newline="\n")
        if before_commit is not None:
            before_commit()
        candidate = final_path
        index = 2
        while True:
            try:
                if os.name == "nt":
                    temp_path.rename(candidate)
                else:
                    os.link(temp_path, candidate)
                    temp_path.unlink()
                return candidate
            except FileExistsError:
                candidate = final_path.with_name(
                    f"{final_path.stem}_{index}{final_path.suffix}"
                )
                index += 1
    finally:
        temp_path.unlink(missing_ok=True)


def export_markdown(
    archive: Archive,
    output_dir: Path,
    options: ExportOptions,
    filename_suffix: str,
    *,
    before_commit: Callable[[], None] | None = None,
    selection_notice: str | None = None,
    visibility_scope: str | None = None,
    unknown_visibility_excluded_count: int = 0,
    ai_format: int = AI_FORMAT_CURRENT,
) -> tuple[Path, dict]:
    if filename_suffix not in _ALLOWED_FILENAME_SUFFIXES:
        raise ValueError("未知 Markdown 文件名类型。")
    if options.layout is ExportLayout.FULL and "AI" in filename_suffix:
        raise ValueError("完整排版与 Markdown 文件名类型不一致。")
    if options.layout is ExportLayout.AI and "AI" not in filename_suffix:
        raise ValueError("AI 排版与 Markdown 文件名类型不一致。")

    if ai_format not in (1, AI_FORMAT_CURRENT):
        raise ValueError("未知 AI 分析版格式版本。")
    uid = archive.profile.id
    legacy_ai = options.layout is ExportLayout.AI and ai_format == 1

    if options.layout is ExportLayout.FULL:
        text, username, count = md.build_markdown(
            archive_to_legacy_data(archive), uid, options
        )
        renderer_stats: dict = {}
    elif legacy_ai:
        text, username, renderer_stats = md.build_ai_markdown(
            archive_to_legacy_data(archive), uid, options
        )
        count = renderer_stats["count"]
    else:
        text, username, renderer_stats = render_ai_markdown(
            archive,
            options,
            provenance_lines=_ai2_provenance_lines(
                archive,
                options,
                selection_notice,
                visibility_scope or "UNFILTERED",
                unknown_visibility_excluded_count,
            ),
        )
        count = renderer_stats["count"]

    if count != len(archive.posts):
        raise RuntimeError(
            f"导出前后条数不一致：模型 {len(archive.posts)} 条，渲染器 {count} 条。"
        )

    if options.layout is ExportLayout.FULL:
        text = _inject_full_provenance(
            text,
            archive,
            options,
            selection_notice,
            visibility_scope,
        )
    elif legacy_ai:
        text = _inject_ai_provenance(
            text,
            archive,
            options,
            selection_notice,
            visibility_scope=visibility_scope or "UNFILTERED",
            unknown_visibility_excluded_count=unknown_visibility_excluded_count,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = md.safe_filename(username)
    range_label = _range_filename_label(archive)

    desired_path = output_dir / f"{safe_name}_{uid}_{range_label}_{filename_suffix}.md"
    output_path = _atomic_write_text(desired_path, text, before_commit)

    stats = dict(renderer_stats)
    stats.update(
        {
            "count": count,
            "output_bytes": output_path.stat().st_size,
            "output_chars": len(text),
            "layout": options.layout.value,
        }
    )
    if options.layout is ExportLayout.AI:
        stats["estimated_tokens"] = estimate_tokens(text)
    return output_path, stats


def export_ai_variant(
    archive: Archive,
    output_dir: Path,
    options: ExportOptions,
    filename_suffix: str,
    *,
    source_body_limit: int | None = None,
    max_part_chars: int | None = None,
    selection_notice: str | None = None,
    visibility_scope: str | None = None,
    unknown_visibility_excluded_count: int = 0,
    before_commit: Callable[[], None] | None = None,
) -> list[tuple[Path, dict]]:
    """Write a smaller AI rendering of an already fetched archive.

    The reposted source bodies can be limited and the archive can be cut into
    parts; each part is a complete WEIBO_AI_2 file with its own numbering.
    """
    if options.layout is not ExportLayout.AI or "AI" not in filename_suffix:
        raise ValueError("AI 精简输出需要 AI 排版。")
    if filename_suffix not in _ALLOWED_FILENAME_SUFFIXES:
        raise ValueError("未知 Markdown 文件名类型。")

    if source_body_limit is not None and source_body_limit < 0:
        raise ValueError("source_body_limit must not be negative")
    default_offset = _default_offset(archive.posts)

    def render_part(part, extra=()):
        if before_commit is not None:
            before_commit()
        provenance = _ai2_provenance_lines(
            part, options, selection_notice, visibility_scope or "UNFILTERED",
            unknown_visibility_excluded_count,
        )
        return render_ai_markdown(
            part, options, provenance_lines=[*provenance, *extra],
            source_body_limit=source_body_limit, default_offset=default_offset,
        )

    parts = (
        split_archive_for_parts(
            archive, max_part_chars, source_body_limit, default_offset=default_offset,
            measure=lambda part: len(render_part(part)[0]),
        )
        if max_part_chars is not None else [archive]
    )

    def part_headers():
        if len(parts) == 1:
            return [()]
        part_list = "; ".join(
            f"{index}) {part_period(part, default_offset)} ({len(part.posts)} records)"
            for index, part in enumerate(parts, 1)
        )
        return [
            (
                f"PART {index}/{len(parts)}: {len(part.posts)} records in this part, "
                f"{len(archive.posts)} across all parts; W numbers and statistics cover "
                "this part only.",
                "PARTS: " + part_list,
            )
            for index, part in enumerate(parts, 1)
        ]

    # Final part metadata also takes space. Refine until each rendered file fits,
    # except an indivisible single record with its required header. Nothing is written yet.
    while True:
        headers = part_headers()
        refined = []
        for part, extra in zip(parts, headers):
            if (max_part_chars is not None and len(part.posts) > 1
                    and len(render_part(part, extra)[0]) > max_part_chars):
                refined.extend(split_archive_for_parts(
                    part, max_part_chars, source_body_limit, default_offset=default_offset,
                    measure=lambda candidate: len(render_part(candidate, extra)[0]),
                ))
            else:
                refined.append(part)
        if len(refined) == len(parts):
            break
        parts = refined
    if source_body_limit is None:
        variant = ""
    elif source_body_limit == 0:
        variant = "_不含原文正文"
    else:
        variant = f"_原文前{source_body_limit}字"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = md.safe_filename(md.normalize_text(archive.profile.screen_name) or archive.profile.id)
    range_label = _range_filename_label(archive)
    results = []
    for index, (part, extra) in enumerate(zip(parts, headers), 1):
        part_label = ""
        if len(parts) > 1:
            part_label = f"_第{index}卷共{len(parts)}卷"
        text, _, stats = render_part(part, extra)
        if max_part_chars is not None and len(text) > max_part_chars:
            if len(part.posts) > 1:
                raise RuntimeError("分卷大小校验失败。")
            # Do not silently truncate an oversized post to meet the requested size.
            text, _, stats = render_part(part, (*extra,
                f"SIZE_TARGET: {max_part_chars} characters; exceeded by the required header or an indivisible record.",
            ))
        if stats["count"] != len(part.posts):
            raise RuntimeError("导出前后条数不一致。")
        desired = output_dir / (
            f"{safe_name}_{archive.profile.id}_{range_label}_"
            f"{filename_suffix}{variant}{part_label}.md"
        )
        path = _atomic_write_text(desired, text, before_commit)
        stats.update(
            {
                "output_bytes": path.stat().st_size,
                "output_chars": len(text),
                "estimated_tokens": estimate_tokens(text),
                "layout": options.layout.value,
            }
        )
        results.append((path, stats))
    return results
