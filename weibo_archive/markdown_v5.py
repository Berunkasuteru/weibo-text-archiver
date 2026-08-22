from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .export_options import DateFormat, ExportLayout, ExportOptions
from .models import normalize_optional_uid

# Frozen V5-compatible renderer.
# V7 deliberately keeps this rendering surface stable while replacing the crawler beneath it.


_VISIBILITY_LABELS = {
    "public": "公开",
    "followers": "粉丝可见",
    "friends": "好友圈",
    "private": "仅自己可见",
    "unknown": "未知",
}


def visibility_token(item: dict) -> str:
    value = str(item.get("visibility") or "unknown").strip().lower()
    return value.upper() if value in _VISIBILITY_LABELS else "UNKNOWN"


def visibility_label(item: dict) -> str:
    value = str(item.get("visibility") or "unknown").strip().lower()
    return _VISIBILITY_LABELS.get(value, "未知")

def normalize_text(value) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "")
    # 压缩过多空行，但保留正文分段
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_incomplete(item: dict) -> bool:
    return item.get("content_state") == "incomplete"


def incomplete_preview(item: dict) -> str:
    return normalize_text(item.get("text_preview"))


def require_incomplete_id(item: dict) -> None:
    if is_incomplete(item) and not str(item.get("id") or "").strip():
        raise ValueError("incomplete post requires a non-empty id")


def display_time(item: dict) -> str:
    value = item.get("full_created_at") or item.get("created_at") or ""
    value = str(value).strip().replace("T", " ")
    return value


def _display_datetime(item: dict) -> datetime | None:
    value = display_time(item)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _offset_text(value: datetime) -> str:
    raw = value.strftime("%z")
    return f"{raw[:3]}:{raw[3:]}" if raw else ""


def _render_time(
    item: dict,
    *,
    date_only: bool,
    ai_layout: bool,
) -> str:
    value = display_time(item)
    if not value:
        return "时间未知"

    parsed = _display_datetime(item)
    provenance = str(item.get("created_at_provenance") or "")
    if parsed is None:
        rendered = value
    elif date_only:
        rendered = parsed.strftime("%Y-%m-%d")
        if provenance == "source_offset":
            offset = _offset_text(parsed)
            if offset:
                rendered += (
                    f" TZ={offset}"
                    if ai_layout
                    else f"（来源偏移 {offset}）"
                )
    else:
        rendered = parsed.isoformat(sep=" ", timespec="minutes")

    if provenance == "relative_unverified":
        rendered += (
            " TIME_BASIS=RELATIVE_UNVERIFIED"
            if ai_layout
            else "（由相对时间解析；时区依据未验证）"
        )
    return rendered


def sort_key(item: dict):
    parsed = _display_datetime(item)
    if parsed is not None:
        wall = parsed.replace(tzinfo=None) if parsed.utcoffset() is not None else parsed
        try:
            identity = (1, int(item.get("id", 0)))
        except (TypeError, ValueError):
            identity = (0, str(item.get("id") or item.get("bid") or ""))
        return (2, wall, identity)

    try:
        return (1, int(item.get("id", 0)))
    except (TypeError, ValueError):
        return (0, display_time(item))


def quote_markdown(text: str) -> str:
    if not text:
        return ""
    return "\n".join("> " + line if line else ">" for line in text.splitlines())


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip(" .")
    return name or "微博用户"


def safe_count(value, *, unknown: str = "未知") -> str:
    """把转发/评论/点赞数统一转成适合 Markdown 显示的文本。"""
    if value is None or value == "":
        return unknown
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value).strip() or unknown


def engagement_line(item: dict) -> str:
    """紧凑显示：转 12 · 评 34 · 赞 56"""
    reposts = safe_count(item.get("reposts_count"))
    comments = safe_count(item.get("comments_count"))
    likes = safe_count(item.get("attitudes_count"))
    return f"转 {reposts} · 评 {comments} · 赞 {likes}"


def clean_profile_value(value) -> str:
    """清理用户资料字段；空值不输出。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        parts = [normalize_text(x) for x in value if normalize_text(x)]
        return "；".join(parts)
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            v = normalize_text(v)
            if v:
                parts.append(f"{k}：{v}")
        return "；".join(parts)
    return normalize_text(value)


def append_profile_line(out: list[str], label: str, value) -> None:
    value = clean_profile_value(value)
    if value:
        out.append(f"- {label}：{value}")


def post_meta_line(item: dict, options: ExportOptions | None = None) -> str:
    """生成每条微博的来源/位置行；无值则不显示。"""
    source = normalize_text(item.get("source"))
    location = normalize_text(item.get("location"))

    parts = []
    if source and (options is None or options.include_source):
        parts.append(f"来源：{source}")
    if location and (options is None or options.include_location):
        parts.append(f"位置：{location}")
    return " · ".join(parts)


def compact_time(item: dict) -> str:
    """AI timestamp: source wall clock to minutes, preserving a known offset."""
    return _render_time(item, date_only=False, ai_layout=True)


def option_time(item: dict, options: ExportOptions) -> str:
    """Render a post timestamp without changing its model value or sort key."""
    return _render_time(
        item,
        date_only=options.date_format is DateFormat.DATE_ONLY,
        ai_layout=options.layout is ExportLayout.AI,
    )


def count_media_values(value, separator=None) -> int:
    """
    统计媒体字段数量。
    兼容 weibo-crawler 常见的字符串格式，也兼容未来可能出现的 list/tuple/set。
    """
    if value is None:
        return 0

    if isinstance(value, (list, tuple, set)):
        return sum(1 for x in value if str(x).strip())

    if isinstance(value, dict):
        return sum(1 for _, x in value.items() if x)

    s = str(value).strip()
    if not s:
        return 0

    if separator:
        return sum(1 for x in s.split(separator) if x.strip())

    return 1


def media_counts(item: dict) -> tuple[int, int, int]:
    """
    返回 (图片数, 视频媒体数, 头条文章数)。

    weibo-crawler 当前 JSON 中：
    - pics: 多图通常用英文逗号分隔
    - video_url: 多个视频 URL 通常用英文分号分隔；
      Live Photo 的视频部分也归入该字段，因此这里不强行区分普通视频和 Live Photo
    - article_url: 有值时视为 1 篇头条文章
    """
    pics = count_media_values(item.get("pics"), ",")
    videos = count_media_values(item.get("video_url"), ";")
    articles = 1 if normalize_text(item.get("article_url")) else 0
    return pics, videos, articles


def compact_media(item: dict) -> str:
    """
    AI 版媒体标签：
      I=图片（Image）
      V=视频媒体（Video，可能含 Live Photo 视频部分）
      A=头条文章（Article）
    没有媒体时返回空字符串。
    """
    pics, videos, articles = media_counts(item)
    parts = []
    if pics:
        parts.append(f"I{pics}")
    if videos:
        parts.append(f"V{videos}")
    if articles:
        parts.append(f"A{articles}")
    return " ".join(parts)


def full_media_line(item: dict) -> str:
    """完整版的人类可读媒体提示，不写任何媒体 URL。"""
    pics, videos, articles = media_counts(item)
    parts = []
    if pics:
        parts.append(f"图片×{pics}")
    if videos:
        parts.append(f"视频媒体×{videos}")
    if articles:
        parts.append("头条文章")
    return " · ".join(parts)


def media_summary(items: list[dict]) -> dict:
    """
    统计整个账号媒体分布。
    同时统计“无文字但有媒体”的微博，提醒 AI 不要把空正文误解为无内容。
    """
    result = {
        "posts_with_images": 0,
        "total_images": 0,
        "posts_with_video": 0,
        "total_video_media": 0,
        "posts_with_article": 0,
        "media_only_posts": 0,
    }

    for item in items:
        pics, videos, articles = media_counts(item)

        if pics:
            result["posts_with_images"] += 1
            result["total_images"] += pics
        if videos:
            result["posts_with_video"] += 1
            result["total_video_media"] += videos
        if articles:
            result["posts_with_article"] += 1

        if (
            (pics or videos or articles)
            and not is_incomplete(item)
            and not normalize_text(item.get("text"))
        ):
            result["media_only_posts"] += 1

    return result


def compact_engagement(item: dict) -> str:
    """AI 版互动数字：R=转发 C=评论 L=点赞。"""
    return (
        f"R={safe_count(item.get('reposts_count'), unknown='UNKNOWN')} "
        f"C={safe_count(item.get('comments_count'), unknown='UNKNOWN')} "
        f"L={safe_count(item.get('attitudes_count'), unknown='UNKNOWN')}"
    )


def prepare_items(data: dict) -> list[dict]:
    """微博按 ID 去重，并按新 -> 旧排序。"""
    weibos = data.get("weibo") or []
    unique = {}
    for item in weibos:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or item.get("bid") or id(item))
        unique[key] = item
    return sorted(unique.values(), key=sort_key, reverse=True)


def _posts_for_document_policy(items: list[dict]):
    for item in items:
        yield item
        retweet = item.get("retweet")
        if isinstance(retweet, dict):
            yield retweet


def build_source_dictionary(
    items: list[dict],
    *,
    include_retweets: bool = False,
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """
    高频来源优先分配短代码 S1/S2...
    返回：
      source_to_code: {"iPhone客户端": "S1", ...}
      rows: [("S1", "iPhone客户端"), ...]
    """
    counts = {}
    first_seen = {}

    source_items = _posts_for_document_policy(items) if include_retweets else items
    for idx, item in enumerate(source_items):
        source = normalize_text(item.get("source"))
        if not source:
            continue
        counts[source] = counts.get(source, 0) + 1
        first_seen.setdefault(source, idx)

    ordered = sorted(
        counts,
        key=lambda s: (-counts[s], first_seen[s], s)
    )

    source_to_code = {}
    rows = []
    for i, source in enumerate(ordered, start=1):
        code = f"S{i}"
        source_to_code[source] = code
        rows.append((code, source))

    return source_to_code, rows


def retweet_key(retweet: dict) -> str:
    """
    优先用原微博 ID/BID 去重；
    若接口没有 ID，再退回“作者 + 正文”。
    """
    rid = retweet.get("id") or retweet.get("bid")
    if rid:
        return f"id:{rid}"

    require_incomplete_id(retweet)

    author = normalize_text(retweet.get("screen_name"))
    body = normalize_text(retweet.get("text"))
    return f"text:{author}\n{body}"


def retweet_content_key(retweet: dict) -> tuple[str, str, str, str]:
    """Identify the body snapshot that a lossless =RT reference may reuse."""
    require_incomplete_id(retweet)
    if is_incomplete(retweet):
        return (
            "incomplete",
            "",
            incomplete_preview(retweet),
            normalize_text(retweet.get("incomplete_reason")),
        )
    return ("complete", normalize_text(retweet.get("text")), "", "")


def is_verified_self_retweet(retweet: dict, target_uid: str) -> bool:
    author_id = normalize_optional_uid(retweet.get("author_id"))
    normalized_target = normalize_optional_uid(target_uid)
    return bool(
        author_id is not None
        and normalized_target is not None
        and author_id == normalized_target
    )


def get_date_range(
    items: list[dict],
    options: ExportOptions | None = None,
) -> tuple[str, str]:
    """返回最早和最晚日期（AI 版格式）。"""
    time_renderer = compact_time if options is None else lambda item: option_time(item, options)
    valid = [time_renderer(x) for x in items if display_time(x)]
    if not valid:
        return "", ""
    # items 已按新 -> 旧
    return valid[-1], valid[0]


def build_ai_markdown(
    data: dict,
    uid: str,
    options: ExportOptions | None = None,
) -> tuple[str, str, dict]:
    """
    生成 AI 分析版：
    - 用户资料只出现一次
    - 时间去秒
    - 发布来源字典化
    - 位置仅存在时输出
    - 互动数使用 R/C/L
    - 相同转发原文只完整保存一次，之后引用 RT 编号
    """
    if options is not None and options.layout is not ExportLayout.AI:
        raise ValueError("AI renderer requires AI layout options")

    user = data.get("user") or {}
    username = normalize_text(user.get("screen_name")) or uid
    items = prepare_items(data)

    if options is None or options.include_source:
        source_to_code, source_rows = build_source_dictionary(
            items,
            include_retweets=options is not None,
        )
    else:
        source_to_code, source_rows = {}, []
    media_stats = media_summary(items)

    original_count = 0
    retweet_post_count = 0
    for item in items:
        if isinstance(item.get("retweet"), dict):
            retweet_post_count += 1
        else:
            original_count += 1

    oldest, newest = get_date_range(items, options)

    # 预扫描转发原文，统计“唯一原文”和“重复引用”数量
    seen_content_by_key = {}
    unique_retweets = 0
    duplicate_retweets = 0
    for item in items:
        rt = item.get("retweet")
        if not isinstance(rt, dict):
            continue
        key = retweet_key(rt)
        content_key = retweet_content_key(rt)
        if key not in seen_content_by_key:
            seen_content_by_key[key] = content_key
            unique_retweets += 1
        elif content_key == seen_content_by_key[key]:
            duplicate_retweets += 1

    out = []
    out.append(f"# {username}｜AI 分析版")
    out.append("FORMAT=WEIBO_AI_1")
    out.append("")

    description = clean_profile_value(user.get("description"))
    if description:
        out.append(f"简介：{description}")

    profile_parts = [f"UID={uid}"]
    for label, key in [
        ("粉丝", "followers_count"),
        ("关注", "follow_count"),
        ("微博", "statuses_count"),
        ("所在地", "location"),
        ("注册", "registration_time"),
    ]:
        value = clean_profile_value(user.get(key))
        if value:
            profile_parts.append(f"{label}={value}")

    verified_reason = clean_profile_value(user.get("verified_reason"))
    if verified_reason:
        profile_parts.append(f"认证={verified_reason}")
    elif user.get("verified") is True:
        profile_parts.append("认证=已认证")

    out.append("资料：" + "｜".join(profile_parts))

    summary_parts = [
        f"共{len(items)}条",
        f"原创{original_count}",
        f"转发{retweet_post_count}",
    ]
    if oldest and newest:
        summary_parts.append(f"范围={oldest}~{newest}")
    if media_stats["posts_with_images"]:
        summary_parts.append(
            f"含图{media_stats['posts_with_images']}条/{media_stats['total_images']}图"
        )
    if media_stats["posts_with_video"]:
        summary_parts.append(
            f"含视频媒体{media_stats['posts_with_video']}条/{media_stats['total_video_media']}项"
        )
    if media_stats["posts_with_article"]:
        summary_parts.append(f"含文章{media_stats['posts_with_article']}条")
    if media_stats["media_only_posts"]:
        summary_parts.append(f"仅媒体{media_stats['media_only_posts']}条")

    summary_parts.append(f"唯一转发原文{unique_retweets}")
    summary_parts.append(f"重复原文省略{duplicate_retweets}次")
    out.append("摘要：" + "｜".join(summary_parts))

    format_parts = ["W=顶层记录", "RT*=转发原文编号"]
    format_parts.append("VIS=抓取时可见范围")
    if options is None or options.include_source:
        format_parts.append("S*=发布来源")
    if options is None or options.include_location:
        format_parts.append("P=发布位置")
    format_parts.append("I=图片数 V=视频媒体数 A=头条文章")
    if options is None or options.include_engagement:
        format_parts.append("R=转发 C=评论 L=点赞")
    out.append("格式：" + "；".join(format_parts) + "。")
    out.append(
        "ATTRIBUTION: RT is a nested repost-source record attributed to its recorded author "
        "metadata; SELF requires exact non-empty UID equality. W may contain preserved quoted text."
    )
    out.append(
        "VISIBILITY: VIS on W is visibility metadata observed from the source response at "
        "archive fetch time; it does not prove the original or unchanged audience. Nested "
        "RT visibility semantics are not interpreted."
    )
    out.append(
        "TEXT_CHAIN: //@ text is preserved but unparsed and cannot verify identity or attribution."
    )
    out.append(
        "MEDIA: I/V/A belong to their containing record; referenced media is not included, "
        "so text may not be complete context."
    )
    out.append(
        "CONTENT: PREVIEW_ONLY is INCOMPLETE and not full text; TEXT=EMPTY is a verified "
        "complete empty body, not unavailable content."
    )
    out.append(
        "TIME: known source offset is preserved; no offset means timezone provenance is unknown; "
        "TIME is not necessarily the target account's local civil time."
    )
    out.append(
        "P: Weibo-displayed publication-location metadata for its containing record; P alone "
        "does not prove event location, residence, a specific visit, or timezone."
    )
    out.append(
        "STRUCTURE: W is a top-level rendered record; ORIGINAL/REPOST means absence/presence "
        "of a structural nested RT, not character-level authorship."
    )
    out.append(
        "ENGAGEMENT: R/C/L belong to their containing W or RT; UNKNOWN is not zero."
    )
    out.append(
        "ABSENT: Missing I/V/A means canonical zero/false. Missing S/P means unavailable or "
        "not emitted by this export configuration, not \"no source/location\"."
    )
    out.append("SOURCE_IDS=file-local: S* and RT* identifiers apply only within this file.")
    out.append(
        "REFERENCE: RT* identifies a repost source and emits that occurrence's body; =RT* "
        "omits only a body identical to the first RT* body; RT* may repeat when a body snapshot "
        "differs. Metadata on every RT*/=RT* line belongs to that occurrence and must not be "
        "inherited from another."
    )
    out.append(
        "AGGREGATES: total/original/repost/range/media describe top-level W records; unique RT "
        "counts nested identities, while duplicate RT counts body-identical =RT references."
    )

    if source_rows:
        source_text = "；".join(
            f"{code}={source}"
            for code, source in source_rows
        )
        out.append("来源字典：" + source_text)

    out.append("")

    rt_key_to_code = {}
    rt_key_to_content = {}
    rt_counter = 0

    def retweet_label_parts(rt: dict, rt_code: str, *, reference: bool) -> list[str]:
        parts = [f"={rt_code}" if reference else rt_code]
        if is_verified_self_retweet(rt, uid):
            parts.append("SELF")
        rt_name = normalize_text(rt.get("screen_name"))
        if rt_name:
            parts.append(f"@{rt_name}")
        if options is not None:
            parts.append(option_time(rt, options))
            rt_source = normalize_text(rt.get("source"))
            rt_source_code = source_to_code.get(rt_source, "")
            if options.include_source and rt_source_code:
                parts.append(rt_source_code)
            rt_location = normalize_text(rt.get("location"))
            if options.include_location and rt_location:
                parts.append(f"P={rt_location}")
        rt_media = compact_media(rt)
        if rt_media:
            parts.append(rt_media)
        if options is None or options.include_engagement:
            parts.append(compact_engagement(rt))
        if is_incomplete(rt):
            parts.append("CONTENT=INCOMPLETE")
        elif not normalize_text(rt.get("text")):
            parts.append("TEXT=EMPTY")
        return parts

    for item in items:
        when = compact_time(item) if options is None else option_time(item, options)
        source = normalize_text(item.get("source"))
        source_code = source_to_code.get(source, "")
        location = normalize_text(item.get("location"))

        require_incomplete_id(item)
        item_incomplete = is_incomplete(item)
        meta = ["W", when, f"VIS={visibility_token(item)}"]
        if source_code and (options is None or options.include_source):
            meta.append(source_code)
        if location and (options is None or options.include_location):
            meta.append(f"P={location}")
        media = compact_media(item)
        if media:
            meta.append(media)
        if options is None or options.include_engagement:
            meta.append(compact_engagement(item))
        if item_incomplete:
            meta.append("CONTENT=INCOMPLETE")
        elif not normalize_text(item.get("text")):
            meta.append("TEXT=EMPTY")

        out.append("[" + "｜".join(meta) + "]")

        if item_incomplete:
            out.append("[PREVIEW_ONLY｜全文无法验证]")
            preview = incomplete_preview(item)
            out.append(preview if preview else "当前没有可保存的列表预览。")
        else:
            body = normalize_text(item.get("text"))
            if body:
                out.append(body)

        rt = item.get("retweet")
        if isinstance(rt, dict):
            require_incomplete_id(rt)
            rt_incomplete = is_incomplete(rt)
            rt_text = normalize_text(rt.get("text"))
            key = retweet_key(rt)
            content_key = retweet_content_key(rt)

            if key not in rt_key_to_code:
                rt_counter += 1
                rt_code = f"RT{rt_counter}"
                rt_key_to_code[key] = rt_code
                rt_key_to_content[key] = content_key
                reference = False
            else:
                rt_code = rt_key_to_code[key]
                reference = content_key == rt_key_to_content[key]

            rt_label_parts = retweet_label_parts(rt, rt_code, reference=reference)
            out.append(">" + "[" + "｜".join(rt_label_parts) + "]")
            if not reference:
                if rt_incomplete:
                    out.append(">[PREVIEW_ONLY｜全文无法验证]")
                    preview = incomplete_preview(rt)
                    out.append(
                        quote_markdown(preview)
                        if preview
                        else "> 当前没有可保存的列表预览。"
                    )
                elif rt_text:
                    out.append(quote_markdown(rt_text))

        out.append("")

    stats = {
        "count": len(items),
        "original_count": original_count,
        "retweet_post_count": retweet_post_count,
        "unique_retweets": unique_retweets,
        "duplicate_retweets": duplicate_retweets,
        "oldest": oldest,
        "newest": newest,
        **media_stats,
    }

    return "\n".join(out).rstrip() + "\n", username, stats


def file_metrics(path: Path) -> tuple[int, int]:
    """返回 UTF-8 文件字节数、字符数。"""
    content = path.read_text(encoding="utf-8")
    return path.stat().st_size, len(content)


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def build_markdown(
    data: dict,
    uid: str,
    options: ExportOptions | None = None,
) -> tuple[str, str, int]:
    if options is not None and options.layout is not ExportLayout.FULL:
        raise ValueError("Full renderer requires FULL layout options")

    user = data.get("user") or {}
    username = normalize_text(user.get("screen_name")) or uid
    items = prepare_items(data)

    out = []
    out.append(f"# {username}")
    out.append("")

    description = clean_profile_value(user.get("description"))
    if description:
        out.append(f"> {description}")
        out.append("")

    out.append("## 个人资料")
    out.append("")
    out.append(f"- UID：`{uid}`")
    append_profile_line(out, "粉丝", user.get("followers_count"))
    append_profile_line(out, "关注", user.get("follow_count"))
    append_profile_line(out, "微博", user.get("statuses_count"))
    append_profile_line(out, "所在地", user.get("location"))

    verified_reason = clean_profile_value(user.get("verified_reason"))
    if verified_reason:
        out.append(f"- 认证：{verified_reason}")
    elif user.get("verified") is True:
        out.append("- 认证：已认证")

    append_profile_line(out, "注册时间", user.get("registration_time"))
    out.append(f"- 本次保存：{len(items)} 条")
    out.append("")
    out.append("> 注：媒体文件未导出；“图片/视频媒体/头条文章”仅记录存在性与数量。分析带媒体微博时，应保留“文字可能依赖未见媒体语境”的不确定性。")
    out.append("")
    out.append("---")
    out.append("")

    for item in items:
        require_incomplete_id(item)
        item_incomplete = is_incomplete(item)
        when = (
            display_time(item) or "时间未知"
            if options is None
            else option_time(item, options)
        )
        text = normalize_text(item.get("text"))

        out.append(f"## {when}")
        meta = post_meta_line(item, options)
        if meta:
            out.append(meta)
        out.append(f"可见范围（抓取时）：{visibility_label(item)}")
        media = full_media_line(item)
        if media:
            out.append(f"媒体：{media}")
        if options is None or options.include_engagement:
            out.append(engagement_line(item))
        out.append("")

        if item_incomplete:
            out.append("【正文不完整 · 全文无法验证】")
            out.append("")
            out.append("当前无法取得并验证完整正文。")
            preview = incomplete_preview(item)
            if preview:
                out.append("以下仅为列表中仍可见的预览，不是完整正文：")
                out.append("")
                out.append(quote_markdown(preview))
            else:
                out.append("当前没有可保存的列表预览。")
            out.append("")
        elif text:
            out.append(text)
            out.append("")

        retweet = item.get("retweet")
        if isinstance(retweet, dict):
            require_incomplete_id(retweet)
            rt_incomplete = is_incomplete(retweet)
            rt_name = normalize_text(retweet.get("screen_name"))
            rt_text = normalize_text(retweet.get("text"))
            rt_media = full_media_line(retweet)
            label = f"转发原文 · @{rt_name}" if rt_name else "转发原文"
            label_parts = [label]
            if is_verified_self_retweet(retweet, uid):
                label_parts.append("已验证为目标账号自转发")
            if options is not None:
                label_parts.append(f"日期：{option_time(retweet, options)}")
                rt_meta = post_meta_line(retweet, options)
                if rt_meta:
                    label_parts.append(rt_meta)
            if rt_media:
                label_parts.append(rt_media)
            if options is None or options.include_engagement:
                label_parts.append(engagement_line(retweet))
            if rt_incomplete:
                label_parts.append("【全文无法验证】")
            elif not rt_text:
                label_parts.append("【已验证正文为空】")
            out.append("**" + " · ".join(label_parts) + "**")
            out.append("")
            if rt_incomplete:
                preview = incomplete_preview(retweet)
                notice = "当前无法取得并验证完整转发原文。"
                if preview:
                    notice += "\n\n以下仅为列表中仍可见的预览，不是完整正文：\n\n" + preview
                else:
                    notice += "\n\n当前没有可保存的列表预览。"
                out.append(quote_markdown(notice))
            elif rt_text:
                out.append(quote_markdown(rt_text))
            out.append("")

        out.append("---")
        out.append("")

    return "\n".join(out).rstrip() + "\n", username, len(items)
