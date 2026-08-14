from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from . import markdown_v5 as md
from .export_options import ExportLayout, ExportOptions, options_provenance
from .models import Archive, ContentState, Post, RangeMode, Termination


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
        "source": post.source,
        "location": post.location,
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


def _inject_ai_provenance(
    text: str,
    archive: Archive,
    options: ExportOptions | None = None,
    selection_notice: str | None = None,
) -> str:
    lines = text.splitlines()
    if not lines:
        return text
    meta = (
        f"导出：{archive.fetch_range.label()}｜终止={_termination_label(archive)}"
        "｜快照（导出机器本地）="
        + archive.fetched_at.isoformat(sep=" ", timespec="minutes")
    )
    inserted = [meta]
    if options is not None:
        inserted.append("输出配置：" + options_provenance(options))
    if selection_notice:
        inserted.append("自定义筛选：" + selection_notice)
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
) -> None:
    """Replace final_path only after a unique task-owned temp is complete."""
    fd, temp_name = tempfile.mkstemp(
        prefix=".weibo-export-",
        suffix=".tmp",
        dir=str(final_path.parent),
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(text, encoding="utf-8")
        if before_commit is not None:
            before_commit()
        temp_path.replace(final_path)
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
) -> tuple[Path, dict]:
    if filename_suffix not in _ALLOWED_FILENAME_SUFFIXES:
        raise ValueError("未知 Markdown 文件名类型。")
    if options.layout is ExportLayout.FULL and "AI" in filename_suffix:
        raise ValueError("完整排版与 Markdown 文件名类型不一致。")
    if options.layout is ExportLayout.AI and "AI" not in filename_suffix:
        raise ValueError("AI 排版与 Markdown 文件名类型不一致。")

    data = archive_to_legacy_data(archive)
    uid = archive.profile.id

    if options.layout is ExportLayout.FULL:
        text, username, count = md.build_markdown(data, uid, options)
        renderer_stats: dict = {}
    else:
        text, username, renderer_stats = md.build_ai_markdown(data, uid, options)
        count = renderer_stats["count"]

    if count != len(archive.posts):
        raise RuntimeError(
            f"导出前后条数不一致：模型 {len(archive.posts)} 条，渲染器 {count} 条。"
        )

    if options.layout is ExportLayout.FULL:
        text = _inject_full_provenance(text, archive, options, selection_notice)
    else:
        text = _inject_ai_provenance(text, archive, options, selection_notice)

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = md.safe_filename(username)
    range_label = _range_filename_label(archive)

    output_path = output_dir / f"{safe_name}_{uid}_{range_label}_{filename_suffix}.md"
    _atomic_write_text(output_path, text, before_commit)

    stats = dict(renderer_stats)
    stats.update(
        {
            "count": count,
            "output_bytes": output_path.stat().st_size,
            "output_chars": len(text),
            "layout": options.layout.value,
        }
    )
    return output_path, stats
