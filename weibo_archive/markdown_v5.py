from __future__ import annotations

import re
from pathlib import Path

from .export_options import DateFormat, ExportLayout, ExportOptions

# Frozen V5-compatible renderer.
# V7 deliberately keeps this rendering surface stable while replacing the crawler beneath it.

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


def sort_key(item: dict):
    # 微博数字 id 基本按时间递增；拿不到时退回时间字符串
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


def safe_count(value) -> str:
    """把转发/评论/点赞数统一转成适合 Markdown 显示的文本。"""
    if value is None or value == "":
        return "0"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value).strip() or "0"


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
    """AI 版时间保留到分钟，去掉秒；无法识别的格式原样保留。"""
    value = display_time(item)
    if not value:
        return "时间未知"

    # 常见格式：2026-08-12 18:30:25 / 2026-08-12T18:30:25
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2})?", value)
    if m:
        return f"{m.group(1)} {m.group(2)}"

    # 某些情况下可能已经只有分钟
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", value)
    if m:
        return f"{m.group(1)} {m.group(2)}"

    return value


def option_time(item: dict, options: ExportOptions) -> str:
    """Render a post timestamp without changing its model value or sort key."""
    value = display_time(item)
    if not value:
        return "时间未知"

    match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2})(?::\d{2})?)?", value)
    if not match:
        return value
    if options.date_format is DateFormat.DATE_ONLY:
        return match.group(1)
    if match.group(2):
        return f"{match.group(1)} {match.group(2)}"
    return match.group(1)


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
        f"R{safe_count(item.get('reposts_count'))} "
        f"C{safe_count(item.get('comments_count'))} "
        f"L{safe_count(item.get('attitudes_count'))}"
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
) -> tuple[dict[str, str], list[tuple[str, str, int]]]:
    """
    高频来源优先分配短代码 S1/S2...
    返回：
      source_to_code: {"iPhone客户端": "S1", ...}
      rows: [("S1", "iPhone客户端", 123), ...]
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
        rows.append((code, source, counts[source]))

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
    生成 AI 精简版：
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
    seen_keys = set()
    unique_retweets = 0
    duplicate_retweets = 0
    for item in items:
        rt = item.get("retweet")
        if not isinstance(rt, dict):
            continue
        rt_text = normalize_text(rt.get("text"))
        if not rt_text and not is_incomplete(rt):
            continue
        key = retweet_key(rt)
        if key in seen_keys:
            duplicate_retweets += 1
        else:
            seen_keys.add(key)
            unique_retweets += 1

    out = []
    out.append(f"# {username}｜AI精简版")
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
    if options is None or options.include_source:
        summary_parts.append(f"来源{len(source_rows)}种")

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

    if options is None:
        out.append("格式：R=转发 C=评论 L=点赞；S*=发布来源；I=图片数 V=视频媒体数 A=头条文章；地=发布位置；RT*=已保存的转发原文编号。")
    else:
        format_parts = []
        if options.include_engagement:
            format_parts.append("R=转发 C=评论 L=点赞")
        if options.include_source:
            format_parts.append("S*=发布来源")
        format_parts.append("I=图片数 V=视频媒体数 A=头条文章")
        if options.include_location:
            format_parts.append("地=发布位置")
        format_parts.append("RT*=已保存的转发原文编号")
        out.append("格式：" + "；".join(format_parts) + "。")
    out.append("语境提醒：I/V/A 只表示媒体存在，媒体内容未导出；分析带媒体微博时，不要把文字当作完整语境。")

    if source_rows:
        source_text = "；".join(
            f"{code}={source}({count})"
            for code, source, count in source_rows
        )
        out.append("来源字典：" + source_text)

    out.append("")

    rt_key_to_code = {}
    rt_counter = 0

    for item in items:
        when = compact_time(item) if options is None else option_time(item, options)
        source = normalize_text(item.get("source"))
        source_code = source_to_code.get(source, "")
        location = normalize_text(item.get("location"))

        require_incomplete_id(item)
        item_incomplete = is_incomplete(item)
        meta = [when]
        if source_code and (options is None or options.include_source):
            meta.append(source_code)
        if location and (options is None or options.include_location):
            meta.append(f"地={location}")
        media = compact_media(item)
        if media:
            meta.append(media)
        if options is None or options.include_engagement:
            meta.append(compact_engagement(item))
        if item_incomplete:
            meta.append("CONTENT=INCOMPLETE")

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
            if rt_text or rt_incomplete:
                key = retweet_key(rt)
                rt_name = normalize_text(rt.get("screen_name"))

                if key not in rt_key_to_code:
                    rt_counter += 1
                    rt_code = f"RT{rt_counter}"
                    rt_key_to_code[key] = rt_code

                    rt_label_parts = [rt_code]
                    if rt_name:
                        rt_label_parts.append(f"@{rt_name}")
                    if options is not None:
                        rt_label_parts.append(option_time(rt, options))
                        rt_source = normalize_text(rt.get("source"))
                        rt_source_code = source_to_code.get(rt_source, "")
                        if options.include_source and rt_source_code:
                            rt_label_parts.append(rt_source_code)
                        rt_location = normalize_text(rt.get("location"))
                        if options.include_location and rt_location:
                            rt_label_parts.append(f"地={rt_location}")
                    rt_media = compact_media(rt)
                    if rt_media:
                        rt_label_parts.append(rt_media)
                    if options is None or options.include_engagement:
                        rt_label_parts.append(compact_engagement(rt))
                    if rt_incomplete:
                        rt_label_parts.append("CONTENT=INCOMPLETE")
                    out.append(">" + "[" + "｜".join(rt_label_parts) + "]")
                    if rt_incomplete:
                        out.append(">[PREVIEW_ONLY｜全文无法验证]")
                        preview = incomplete_preview(rt)
                        out.append(
                            quote_markdown(preview)
                            if preview
                            else "> 当前没有可保存的列表预览。"
                        )
                    else:
                        out.append(quote_markdown(rt_text))
                else:
                    rt_code = rt_key_to_code[key]
                    out.append(f">[={rt_code}]")

        out.append("")

    stats = {
        "count": len(items),
        "original_count": original_count,
        "retweet_post_count": retweet_post_count,
        "unique_retweets": unique_retweets,
        "duplicate_retweets": duplicate_retweets,
        "source_count": len(source_rows),
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
            if rt_text or rt_incomplete:
                rt_media = full_media_line(retweet)
                label = f"转发原文 · @{rt_name}" if rt_name else "转发原文"
                label_parts = [label]
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
                else:
                    out.append(quote_markdown(rt_text))
                out.append("")

        out.append("---")
        out.append("")

    return "\n".join(out).rstrip() + "\n", username, len(items)
