"""Offline Schema-5 cache loading, incremental merge, and JSON/HTML bundle export."""
from __future__ import annotations

import html
import json
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from . import markdown_v5 as md
from . import paths
from .ai_format import _calendar_time, _default_offset
from .exporter import _atomic_write_text, _range_filename_label
from .image_manifest import MAX_MANIFEST_BYTES
from .models import (
    Archive,
    ContentState,
    Engagement,
    FetchRange,
    FetchReport,
    IncompleteReason,
    MediaInfo,
    Post,
    RangeMode,
    Termination,
    TimestampProvenance,
    UserProfile,
    VisibilityInfo,
    VisibilityState,
    normalize_optional_uid,
)
from .storage import SCHEMA_VERSION, _json_default


MAX_ARCHIVE_JSON_BYTES = 128 * 1024 * 1024
_DEFAULT_ALLOWED_BUNDLE_SUFFIXES = {"结构化备份", "图文浏览版"}


class OfflineArchiveError(ValueError):
    """Raised when a local Schema-5 archive cache cannot be loaded or merged."""


@dataclass(frozen=True)
class CachedArchiveInfo:
    uid: str
    screen_name: str
    path: Path
    post_count: int
    fetched_at: datetime
    fetch_range_label: str
    termination_label: str


def _parse_iso_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise OfflineArchiveError(f"缓存字段 {field_name} 缺少有效时间戳。")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise OfflineArchiveError(
            f"缓存字段 {field_name} 时间格式无效：{value!r}"
        ) from exc


def _parse_optional_iso_datetime(
    value: object, field_name: str
) -> datetime | None:
    if value is None or value == "":
        return None
    return _parse_iso_datetime(value, field_name)


def _parse_optional_iso_date(value: object, field_name: str) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise OfflineArchiveError(f"缓存字段 {field_name} 日期格式无效。")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise OfflineArchiveError(
            f"缓存字段 {field_name} 日期格式无效：{value!r}"
        ) from exc


def _opt_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise OfflineArchiveError(f"期望整数字段，实际为 {value!r}")


def _int_or_zero(value: object) -> int:
    parsed = _opt_int(value)
    return 0 if parsed is None else parsed


def _opt_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise OfflineArchiveError(f"期望布尔字段，实际为 {value!r}")


def _str_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _load_profile(raw: object) -> UserProfile:
    if not isinstance(raw, dict):
        raise OfflineArchiveError("缓存文件缺少 profile 对象。")
    uid = _str_or_empty(raw.get("id")).strip()
    if not uid or not uid.isdigit():
        raise OfflineArchiveError("缓存 profile.id 缺失或不是有效数字 UID。")
    screen_name = _str_or_empty(raw.get("screen_name")).strip() or uid
    return UserProfile(
        id=uid,
        screen_name=screen_name,
        description=_str_or_empty(raw.get("description")),
        followers_count=_opt_int(raw.get("followers_count")),
        follow_count=_opt_int(raw.get("follow_count")),
        statuses_count=_opt_int(raw.get("statuses_count")),
        location=_str_or_empty(raw.get("location")),
        verified=_opt_bool(raw.get("verified")),
        verified_reason=_str_or_empty(raw.get("verified_reason")),
        registration_time=_str_or_empty(raw.get("registration_time")),
        gender=_str_or_empty(raw.get("gender")),
        birthday=_str_or_empty(raw.get("birthday")),
        company=_str_or_empty(raw.get("company")),
        education=_str_or_empty(raw.get("education")),
        ip_location=_str_or_empty(raw.get("ip_location")),
    )


def _load_visibility(raw: object) -> VisibilityInfo:
    if raw is None:
        return VisibilityInfo()
    if not isinstance(raw, dict):
        raise OfflineArchiveError("缓存 visibility 字段格式无效。")
    state_raw = raw.get("state", VisibilityState.UNKNOWN.value)
    try:
        state = VisibilityState(state_raw)
    except ValueError:
        state = VisibilityState.UNKNOWN
    raw_list_idstr = raw.get("raw_list_idstr")
    if raw_list_idstr is not None and not isinstance(raw_list_idstr, str):
        raw_list_idstr = str(raw_list_idstr)
    return VisibilityInfo(
        state=state,
        raw_type=_opt_int(raw.get("raw_type")),
        raw_list_id=_opt_int(raw.get("raw_list_id")),
        raw_list_idstr=raw_list_idstr,
    )


def _load_post(raw: object, *, index_hint: str) -> Post:
    if not isinstance(raw, dict):
        raise OfflineArchiveError(f"缓存第 {index_hint} 条记录格式无效。")
    post_id = _str_or_empty(raw.get("id")).strip()
    if not post_id:
        raise OfflineArchiveError(f"缓存第 {index_hint} 条记录缺少 id。")

    prov_raw = raw.get("created_at_provenance", TimestampProvenance.UNKNOWN.value)
    try:
        provenance = TimestampProvenance(prov_raw)
    except ValueError as exc:
        raise OfflineArchiveError(
            f"缓存第 {index_hint} 条记录时间戳来源无效：{prov_raw!r}"
        ) from exc

    created_at = _parse_optional_iso_datetime(
        raw.get("created_at"), f"posts[{index_hint}].created_at"
    )
    if provenance is TimestampProvenance.UNKNOWN:
        created_at = None
    elif created_at is None:
        raise OfflineArchiveError(
            f"缓存第 {index_hint} 条记录缺少非未知时间戳。"
        )
    elif provenance is TimestampProvenance.SOURCE_OFFSET:
        if created_at.utcoffset() is None:
            raise OfflineArchiveError(
                f"缓存第 {index_hint} 条记录 SOURCE_OFFSET 缺少时区偏移。"
            )
    elif created_at.utcoffset() is not None:
        created_at = created_at.replace(tzinfo=None)

    eng_raw = raw.get("engagement") or {}
    if not isinstance(eng_raw, dict):
        raise OfflineArchiveError(f"缓存第 {index_hint} 条 engagement 格式无效。")
    engagement = Engagement(
        reposts=_opt_int(eng_raw.get("reposts")),
        comments=_opt_int(eng_raw.get("comments")),
        likes=_opt_int(eng_raw.get("likes")),
    )

    media_raw = raw.get("media") or {}
    if not isinstance(media_raw, dict):
        raise OfflineArchiveError(f"缓存第 {index_hint} 条 media 格式无效。")
    media = MediaInfo(
        images=_int_or_zero(media_raw.get("images")),
        videos=_int_or_zero(media_raw.get("videos")),
        article=bool(media_raw.get("article", False)),
    )

    state_raw = raw.get("content_state", ContentState.COMPLETE.value)
    try:
        content_state = ContentState(state_raw)
    except ValueError as exc:
        raise OfflineArchiveError(
            f"缓存第 {index_hint} 条 content_state 无效：{state_raw!r}"
        ) from exc

    text = raw.get("text")
    text_preview = raw.get("text_preview")
    incomplete_reason_raw = raw.get("incomplete_reason")
    incomplete_reason: IncompleteReason | None = None
    if content_state is ContentState.COMPLETE:
        text = "" if text is None else str(text)
        text_preview = None
    else:
        text = None
        if text_preview is not None and not isinstance(text_preview, str):
            text_preview = str(text_preview)
        try:
            incomplete_reason = IncompleteReason(incomplete_reason_raw)
        except ValueError as exc:
            raise OfflineArchiveError(
                f"缓存第 {index_hint} 条不完整记录缺少有效 incomplete_reason。"
            ) from exc

    retweet_raw = raw.get("retweet")
    retweet = (
        _load_post(retweet_raw, index_hint=f"{index_hint}.retweet")
        if retweet_raw is not None
        else None
    )

    try:
        return Post(
            id=post_id,
            bid=_str_or_empty(raw.get("bid")),
            created_at=created_at,
            created_at_provenance=provenance,
            text=text,
            source=_str_or_empty(raw.get("source")),
            location=_str_or_empty(raw.get("location")),
            author=_str_or_empty(raw.get("author")),
            author_id=normalize_optional_uid(raw.get("author_id")),
            engagement=engagement,
            media=media,
            retweet=retweet,
            edited=bool(raw.get("edited", False)),
            edit_count=_int_or_zero(raw.get("edit_count", 0)),
            content_state=content_state,
            text_preview=text_preview,
            incomplete_reason=incomplete_reason,
            visibility=_load_visibility(raw.get("visibility")),
            checkin=_str_or_empty(raw.get("checkin")),
        )
    except (TypeError, ValueError) as exc:
        raise OfflineArchiveError(
            f"缓存第 {index_hint} 条记录校验失败：{exc}"
        ) from exc


def _load_fetch_range(raw: object) -> FetchRange:
    if not isinstance(raw, dict):
        raise OfflineArchiveError("缓存文件缺少 fetch_range 对象。")
    try:
        mode = RangeMode(raw.get("mode", RangeMode.ALL.value))
    except ValueError as exc:
        raise OfflineArchiveError(
            f"缓存 fetch_range.mode 无效：{raw.get('mode')!r}"
        ) from exc
    return FetchRange(
        mode=mode,
        limit=_opt_int(raw.get("limit")),
        since=_parse_optional_iso_date(raw.get("since"), "fetch_range.since"),
    )


def _load_fetch_report(raw: object) -> FetchReport:
    if not isinstance(raw, dict):
        raise OfflineArchiveError("缓存文件缺少 report 对象。")
    try:
        termination = Termination(raw.get("termination", Termination.NATURAL.value))
    except ValueError as exc:
        raise OfflineArchiveError(
            f"缓存 report.termination 无效：{raw.get('termination')!r}"
        ) from exc
    return FetchReport(
        pages_fetched=_int_or_zero(raw.get("pages_fetched")),
        requests_made=_int_or_zero(raw.get("requests_made")),
        termination=termination,
        oldest_reached=_parse_optional_iso_datetime(
            raw.get("oldest_reached"), "report.oldest_reached"
        ),
        newest_reached=_parse_optional_iso_datetime(
            raw.get("newest_reached"), "report.newest_reached"
        ),
        profile_statuses_count=_opt_int(raw.get("profile_statuses_count")),
    )


def load_normalized_archive_file(path: Path) -> Archive:
    """Load and validate a Schema-5 cache JSON file into a normalized Archive."""
    candidate = Path(path)
    if not candidate.is_file():
        raise OfflineArchiveError(f"缓存文件不存在：{candidate}")
    try:
        size = candidate.stat().st_size
    except OSError as exc:
        raise OfflineArchiveError(f"无法读取缓存文件大小：{exc}") from exc
    if size > MAX_ARCHIVE_JSON_BYTES:
        raise OfflineArchiveError("缓存文件体积超出安全上限，已拒绝读取。")
    try:
        raw_text = candidate.read_text(encoding="utf-8")
        payload = json.loads(raw_text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineArchiveError(f"无法解析缓存 JSON：{exc}") from exc

    if not isinstance(payload, dict):
        raise OfflineArchiveError("缓存文件顶层不是 JSON 对象。")
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise OfflineArchiveError(
            f"不支持的缓存结构版本：{version!r}（仅支持 schema_version={SCHEMA_VERSION}）。"
        )

    profile = _load_profile(payload.get("profile"))
    posts_raw = payload.get("posts")
    if not isinstance(posts_raw, list):
        raise OfflineArchiveError("缓存文件缺少 posts 列表。")
    posts = tuple(
        _load_post(item, index_hint=str(idx))
        for idx, item in enumerate(posts_raw)
    )
    fetch_range = _load_fetch_range(payload.get("fetch_range"))
    report = _load_fetch_report(payload.get("report"))
    fetched_at = _parse_iso_datetime(payload.get("fetched_at"), "fetched_at")
    return Archive(
        profile=profile,
        posts=posts,
        fetch_range=fetch_range,
        report=report,
        fetched_at=fetched_at,
    )


def _sortable_timestamp(dt: datetime) -> float:
    if dt.utcoffset() is None:
        return dt.astimezone().timestamp()
    return dt.timestamp()


def list_cached_archives(cache_dir: Path | None = None) -> list[CachedArchiveInfo]:
    """List readable Schema-5 last_success.json snapshots newest first."""
    base_dir = Path(cache_dir) if cache_dir is not None else paths.CACHE_DIR
    if not base_dir.is_dir():
        return []
    entries: list[CachedArchiveInfo] = []
    try:
        children = sorted(base_dir.iterdir(), key=lambda item: item.name)
    except OSError:
        return []

    for folder in children:
        if not folder.is_dir():
            continue
        cache_path = folder / "last_success.json"
        if not cache_path.is_file():
            continue
        try:
            archive = load_normalized_archive_file(cache_path)
        except OfflineArchiveError:
            continue
        entries.append(
            CachedArchiveInfo(
                uid=archive.profile.id,
                screen_name=archive.profile.screen_name,
                path=cache_path,
                post_count=len(archive.posts),
                fetched_at=archive.fetched_at,
                fetch_range_label=archive.fetch_range.label(),
                termination_label=archive.report.termination.label(),
            )
        )
    entries.sort(
        key=lambda item: (_sortable_timestamp(item.fetched_at), item.uid),
        reverse=True,
    )
    return entries


def incremental_since_date(archive: Archive, *, overlap_days: int = 1) -> date:
    """Compute a conservative incremental start date with 1-day boundary overlap."""
    if overlap_days < 0:
        raise ValueError("overlap_days 不能为负数。")
    offset = _default_offset(archive.posts)
    newest_date: date | None = None
    for post in archive.posts:
        cal = _calendar_time(post, offset)
        if cal is not None:
            post_date = cal.date()
            if newest_date is None or post_date > newest_date:
                newest_date = post_date
    if newest_date is None:
        fallback_dt = archive.report.newest_reached or archive.fetched_at
        newest_date = fallback_dt.date()
    return newest_date - timedelta(days=overlap_days)


def _post_sort_key(post: Post, default_offset) -> tuple[int, float, int, str]:
    cal = _calendar_time(post, default_offset)
    ts = cal.timestamp() if cal is not None else float("-inf")
    numeric_id = int(post.id) if post.id.isdigit() else -1
    return (1 if cal is not None else 0, ts, numeric_id, post.id)


def _merge_profile(existing: UserProfile, delta: UserProfile) -> UserProfile:
    screen_name = delta.screen_name.strip() or existing.screen_name
    return UserProfile(
        id=delta.id,
        screen_name=screen_name,
        description=delta.description or existing.description,
        followers_count=(
            delta.followers_count
            if delta.followers_count is not None
            else existing.followers_count
        ),
        follow_count=(
            delta.follow_count
            if delta.follow_count is not None
            else existing.follow_count
        ),
        statuses_count=(
            delta.statuses_count
            if delta.statuses_count is not None
            else existing.statuses_count
        ),
        location=delta.location or existing.location,
        verified=delta.verified if delta.verified is not None else existing.verified,
        verified_reason=delta.verified_reason or existing.verified_reason,
        registration_time=delta.registration_time or existing.registration_time,
        gender=delta.gender or existing.gender,
        birthday=delta.birthday or existing.birthday,
        company=delta.company or existing.company,
        education=delta.education or existing.education,
        ip_location=delta.ip_location or existing.ip_location,
    )


def merge_incremental_archives(existing: Archive, delta: Archive) -> Archive:
    """Merge a newer incremental Archive into an existing Schema-5 Archive for the same UID."""
    if existing.profile.id != delta.profile.id:
        raise OfflineArchiveError(
            f"无法合并不同账号的缓存：{existing.profile.id} vs {delta.profile.id}"
        )

    by_id: dict[str, Post] = {}
    for post in existing.posts:
        by_id[post.id] = post
    for post in delta.posts:
        previous = by_id.get(post.id)
        if (
            previous is not None
            and previous.content_state is ContentState.COMPLETE
            and post.content_state is ContentState.INCOMPLETE
        ):
            # Keep previously captured complete text if a refreshed fetch sees degradation.
            continue
        by_id[post.id] = post

    merged_values = list(by_id.values())
    default_offset = _default_offset(merged_values)
    merged_posts = tuple(
        sorted(
            merged_values,
            key=lambda post: _post_sort_key(post, default_offset),
            reverse=True,
        )
    )

    if existing.fetch_range.mode is RangeMode.ALL:
        merged_range = FetchRange.all()
    elif existing.fetch_range.mode is RangeMode.SINCE and existing.fetch_range.since:
        since_val = existing.fetch_range.since
        if delta.fetch_range.since and delta.fetch_range.since < since_val:
            since_val = delta.fetch_range.since
        merged_range = FetchRange.since_date(since_val)
    elif existing.fetch_range.mode in (RangeMode.RECENT, RangeMode.TRIAL):
        merged_range = FetchRange.recent(len(merged_posts))
    else:
        merged_range = delta.fetch_range

    reached_candidates = [
        dt
        for dt in (
            existing.report.oldest_reached,
            existing.report.newest_reached,
            delta.report.oldest_reached,
            delta.report.newest_reached,
        )
        if dt is not None
    ]
    oldest_reached = (
        min(reached_candidates, key=_sortable_timestamp)
        if reached_candidates
        else None
    )
    newest_reached = (
        max(reached_candidates, key=_sortable_timestamp)
        if reached_candidates
        else None
    )
    merged_report = FetchReport(
        pages_fetched=existing.report.pages_fetched + delta.report.pages_fetched,
        requests_made=existing.report.requests_made + delta.report.requests_made,
        termination=delta.report.termination,
        oldest_reached=oldest_reached,
        newest_reached=newest_reached,
        profile_statuses_count=(
            delta.report.profile_statuses_count
            if delta.report.profile_statuses_count is not None
            else existing.report.profile_statuses_count
        ),
    )

    return Archive(
        profile=_merge_profile(existing.profile, delta.profile),
        posts=merged_posts,
        fetch_range=merged_range,
        report=merged_report,
        fetched_at=delta.fetched_at,
    )


def export_portable_json(
    archive: Archive,
    output_dir: Path,
    filename_suffix: str = "结构化备份",
    *,
    before_commit: Callable[[], None] | None = None,
) -> tuple[Path, dict]:
    """Write a normalized, URL/cookie-free portable JSON archive."""
    if filename_suffix not in _DEFAULT_ALLOWED_BUNDLE_SUFFIXES:
        filename_suffix = "结构化备份"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = md.safe_filename(archive.profile.screen_name)
    range_label = _range_filename_label(archive)
    desired_path = output_dir / f"{safe_name}_{archive.profile.id}_{range_label}_{filename_suffix}.json"
    payload = {
        "format": "weibo-archive-portable-json",
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now().astimezone().isoformat(),
        **asdict(archive),
        "integrity": asdict(archive.integrity),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n"
    output_path = _atomic_write_text(desired_path, text, before_commit)
    return output_path, {
        "count": len(archive.posts),
        "output_bytes": output_path.stat().st_size,
        "output_chars": len(text),
        "layout": "json",
    }


def _safe_relative_image_path(rel_value: object) -> PurePosixPath | None:
    if not isinstance(rel_value, str) or not rel_value.strip():
        return None
    normalized = rel_value.replace("\\", "/").strip()
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return None
    posix = PurePosixPath(normalized)
    if posix.is_absolute() or ".." in posix.parts:
        return None
    return posix


def _format_browser_src(target_file: Path, output_dir_resolved: Path) -> str:
    try:
        rel = os.path.relpath(target_file, output_dir_resolved)
    except ValueError:
        return target_file.as_uri()
    rel_posix = rel.replace("\\", "/")
    return "/".join(quote(segment) for segment in rel_posix.split("/"))


def collect_manifest_image_map(
    archive: Archive,
    output_dir: Path,
    image_root: Path | None = None,
) -> dict[tuple[str, str, str], list[dict]]:
    """Read WeiboImages_<UID>/manifest.json read-only and verify image files on disk."""
    uid = archive.profile.id
    candidate_root = (
        Path(image_root)
        if image_root is not None
        else Path(output_dir) / f"WeiboImages_{uid}"
    )
    manifest_path = candidate_root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return {}
    try:
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            return {}
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("schema_version") != 1 or data.get("target_identity") != uid:
        return {}
    assets = data.get("assets")
    if not isinstance(assets, dict):
        return {}

    try:
        root_resolved = candidate_root.resolve()
        output_resolved = Path(output_dir).resolve()
    except OSError:
        return {}

    grouped: dict[tuple[str, str, str], list[tuple[int, dict]]] = {}
    for entry in assets.values():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") not in ("saved", "already_present"):
            continue
        relation = entry.get("relation")
        containing = str(entry.get("containing_post_id") or "")
        source = str(entry.get("source_post_id") or "")
        if relation not in ("TOP_LEVEL", "RETWEET_SOURCE") or not containing or not source:
            continue
        posix_rel = _safe_relative_image_path(entry.get("relative_path"))
        if posix_rel is None:
            continue
        try:
            target_file = (root_resolved / Path(*posix_rel.parts)).resolve()
        except OSError:
            continue
        if not target_file.is_relative_to(root_resolved) or not target_file.is_file():
            continue
        slot_index = _int_or_zero(entry.get("slot_index"))
        grouped.setdefault((relation, containing, source), []).append(
            (
                slot_index,
                {
                    "slot": slot_index,
                    "src": _format_browser_src(target_file, output_resolved),
                    "subtype": _str_or_empty(entry.get("subtype")) or "still",
                    "media_type": _str_or_empty(entry.get("media_type")),
                },
            )
        )

    return {
        key: [item for _, item in sorted(items, key=lambda pair: pair[0])]
        for key, items in grouped.items()
    }


def _safe_json_for_script_tag(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (
        raw.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _visibility_badge(state: VisibilityState) -> str:
    return {
        VisibilityState.PUBLIC: "公开",
        VisibilityState.FOLLOWERS: "粉丝可见",
        VisibilityState.FRIENDS: "好友圈",
        VisibilityState.PRIVATE: "仅自己可见",
        VisibilityState.UNKNOWN: "可见范围未知",
    }.get(state, "可见范围未知")


def _serialize_post_for_html(
    post: Post,
    containing_id: str,
    relation: str,
    default_offset,
    image_map: dict[tuple[str, str, str], list[dict]],
) -> dict:
    cal = _calendar_time(post, default_offset)
    if post.created_at_provenance is TimestampProvenance.UNKNOWN or post.created_at is None:
        time_display = "时间未知"
    elif post.created_at_provenance is TimestampProvenance.RELATIVE_UNVERIFIED:
        time_display = f"{post.created_at:%Y-%m-%d %H:%M}（近似）"
    else:
        time_display = f"{(cal or post.created_at):%Y-%m-%d %H:%M}"

    matched_images = image_map.get((relation, containing_id, post.id), [])
    incomplete_note = ""
    if post.content_state is ContentState.INCOMPLETE:
        reason = (
            "原微博因平台状态不可见/已删除"
            if post.incomplete_reason is IncompleteReason.PLATFORM_TOMBSTONE
            else "完整正文抓取失败"
        )
        incomplete_note = reason

    item = {
        "id": post.id,
        "bid": post.bid,
        "author": post.author,
        "time": time_display,
        "sort_ts": cal.timestamp() if cal is not None else 0,
        "source": post.source,
        "location": post.location,
        "checkin": post.checkin,
        "edited": post.edited,
        "visibility": _visibility_badge(post.visibility.state),
        "visibility_key": post.visibility.state.value,
        "incomplete": post.content_state is ContentState.INCOMPLETE,
        "incomplete_note": incomplete_note,
        "text": (
            post.text
            if post.content_state is ContentState.COMPLETE
            else (post.text_preview or "")
        ),
        "engagement": {
            "reposts": post.engagement.reposts,
            "comments": post.engagement.comments,
            "likes": post.engagement.likes,
        },
        "media": {
            "declared_images": post.media.images,
            "videos": post.media.videos,
            "article": post.media.article,
        },
        "images": matched_images,
        "retweet": None,
    }
    if post.retweet is not None:
        item["retweet"] = _serialize_post_for_html(
            post.retweet,
            containing_id=containing_id,
            relation="RETWEET_SOURCE",
            default_offset=default_offset,
            image_map=image_map,
        )
    return item


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
<style>
:root {
  --bg: #f6f7f9;
  --card: #ffffff;
  --subcard: #f2f4f8;
  --text: #1c2430;
  --muted: #606c7a;
  --border: #dfe4ec;
  --accent: #d95d24;
  --badge: #edf2f7;
  --warn-bg: #fff5e6;
  --warn-text: #9c5300;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #12161c;
    --card: #1b212a;
    --subcard: #232a35;
    --text: #e6ebf2;
    --muted: #9aa6b4;
    --border: #2f3846;
    --accent: #ef7842;
    --badge: #28313e;
    --warn-bg: #352715;
    --warn-text: #f3c17a;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  line-height: 1.65;
}
.wrap { max-width: 920px; margin: 0 auto; padding: 20px 16px 48px; }
header.hero {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px 22px;
  margin-bottom: 16px;
}
.hero h1 { margin: 0 0 6px; font-size: 22px; }
.hero .bio { margin: 6px 0 10px; color: var(--muted); white-space: pre-wrap; }
.meta-row { display: flex; flex-wrap: wrap; gap: 8px 14px; font-size: 13px; color: var(--muted); }
.toolbar {
  position: sticky;
  top: 0;
  z-index: 20;
  background: var(--bg);
  padding: 10px 0 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.toolbar input, .toolbar select, .toolbar button {
  background: var(--card);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 7px 10px;
  font-size: 14px;
}
.toolbar input { flex: 1 1 220px; }
.toolbar button { cursor: pointer; }
.toolbar button:disabled { opacity: 0.45; cursor: default; }
.status-line { font-size: 13px; color: var(--muted); margin-bottom: 10px; }
article.post {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 18px;
  margin-bottom: 14px;
}
.post-head { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; margin-bottom: 8px; }
.post-time { font-weight: 600; font-size: 14px; }
.badge {
  display: inline-block;
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--badge);
  color: var(--muted);
}
.badge-warn { background: var(--warn-bg); color: var(--warn-text); }
.post-text { white-space: pre-wrap; word-break: break-word; font-size: 15px; }
.warn-box {
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--warn-bg);
  color: var(--warn-text);
  font-size: 13px;
}
.media-note { margin-top: 8px; font-size: 12px; color: var(--muted); }
.img-grid {
  margin-top: 10px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 8px;
}
.img-grid img {
  width: 100%;
  height: 150px;
  object-fit: cover;
  border-radius: 8px;
  border: 1px solid var(--border);
  cursor: zoom-in;
  background: var(--subcard);
}
.retweet {
  margin-top: 12px;
  padding: 12px 14px;
  background: var(--subcard);
  border-left: 3px solid var(--accent);
  border-radius: 8px;
}
.post-foot {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 12px;
  color: var(--muted);
}
.pager {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 10px;
  margin-top: 20px;
}
.pager button {
  background: var(--card);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 7px 14px;
  cursor: pointer;
}
.pager button:disabled { opacity: 0.45; cursor: default; }
.lightbox {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.86);
  display: none;
  align-items: center;
  justify-content: center;
  z-index: 99;
  padding: 20px;
}
.lightbox.open { display: flex; }
.lightbox img { max-width: 96vw; max-height: 92vh; border-radius: 6px; }
</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <h1>__HERO_TITLE__</h1>
    <div class="bio">__HERO_BIO__</div>
    <div class="meta-row">__HERO_META__</div>
  </header>

  <noscript><p>请启用浏览器 JavaScript 以浏览与搜索本离线归档。</p></noscript>

  <div class="toolbar">
    <input id="q" type="search" placeholder="搜索正文、转发、作者、地点、设备…">
    <select id="typeFilter">
      <option value="all">全部微博</option>
      <option value="orig">仅原创</option>
      <option value="rt">仅转发</option>
      <option value="img">含本地图片</option>
      <option value="incomplete">仅不完整记录</option>
    </select>
    <select id="sortOrder">
      <option value="desc">时间倒序（新 → 旧）</option>
      <option value="asc">时间正序（旧 → 新）</option>
    </select>
    <button id="clearBtn" type="button">重置</button>
  </div>

  <div id="status" class="status-line"></div>
  <main id="feed"></main>
  <nav class="pager">
    <button id="prevBtn" type="button">上一页</button>
    <span id="pageInfo"></span>
    <button id="nextBtn" type="button">下一页</button>
  </nav>
</div>

<div id="lightbox" class="lightbox" title="点击任意处关闭">
  <img id="lightboxImg" alt="">
</div>

<script id="archive-data" type="application/json">__ARCHIVE_JSON__</script>
<script>
(function () {
  var PAGE_SIZE = 50;
  var data = JSON.parse(document.getElementById("archive-data").textContent);
  var posts = data.posts || [];
  var qInput = document.getElementById("q");
  var typeSelect = document.getElementById("typeFilter");
  var sortSelect = document.getElementById("sortOrder");
  var clearBtn = document.getElementById("clearBtn");
  var statusEl = document.getElementById("status");
  var feedEl = document.getElementById("feed");
  var prevBtn = document.getElementById("prevBtn");
  var nextBtn = document.getElementById("nextBtn");
  var pageInfo = document.getElementById("pageInfo");
  var lightbox = document.getElementById("lightbox");
  var lightboxImg = document.getElementById("lightboxImg");
  var page = 1;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function formatCount(label, value) {
    if (value === null || value === undefined) return label + " -";
    return label + " " + value;
  }

  function openLightbox(src, alt) {
    lightboxImg.src = src;
    lightboxImg.alt = alt || "";
    lightbox.classList.add("open");
  }

  lightbox.addEventListener("click", function () {
    lightbox.classList.remove("open");
    lightboxImg.removeAttribute("src");
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") lightbox.click();
  });

  function renderImages(container, imgs, declaredCount) {
    if (imgs && imgs.length) {
      var grid = el("div", "img-grid");
      imgs.forEach(function (imgInfo) {
        var img = document.createElement("img");
        img.loading = "lazy";
        img.src = imgInfo.src;
        img.alt = "图 " + imgInfo.slot;
        img.addEventListener("click", function () {
          openLightbox(imgInfo.src, img.alt);
        });
        grid.appendChild(img);
      });
      container.appendChild(grid);
    } else if (declaredCount > 0) {
      container.appendChild(
        el("div", "media-note", "声明图片 " + declaredCount + " 张（当前目录未匹配到本地图片文件）")
      );
    }
  }

  function renderPostBody(container, item, isRetweet) {
    var head = el("div", "post-head");
    head.appendChild(el("span", "post-time", (isRetweet ? "@" + (item.author || "未知作者") + " · " : "") + item.time));
    if (item.edited) head.appendChild(el("span", "badge", "已编辑"));
    if (item.visibility_key && item.visibility_key !== "public") {
      head.appendChild(el("span", "badge", item.visibility));
    }
    if (item.incomplete) head.appendChild(el("span", "badge badge-warn", "不完整"));
    if (item.media.videos > 0) head.appendChild(el("span", "badge", "视频 " + item.media.videos));
    if (item.media.article) head.appendChild(el("span", "badge", "头条文章"));
    container.appendChild(head);

    if (item.incomplete) {
      var box = el("div", "warn-box", item.incomplete_note + (item.text ? "｜残缺预览：" + item.text : ""));
      container.appendChild(box);
    } else {
      container.appendChild(el("div", "post-text", item.text || ""));
    }

    renderImages(container, item.images, item.media.declared_images);

    var footParts = [];
    if (item.location) footParts.push(item.location);
    if (item.checkin) footParts.push("签到:" + item.checkin);
    if (item.source) footParts.push("来自 " + item.source);
    footParts.push(formatCount("转发", item.engagement.reposts));
    footParts.push(formatCount("评论", item.engagement.comments));
    footParts.push(formatCount("赞", item.engagement.likes));
    if (item.bid) footParts.push("BID:" + item.bid);
    container.appendChild(el("div", "post-foot", footParts.join(" · ")));
  }

  function getFiltered() {
    var q = qInput.value.trim().toLowerCase();
    var kind = typeSelect.value;
    var order = sortSelect.value;
    var filtered = posts.filter(function (p) {
      if (kind === "orig" && p.retweet) return false;
      if (kind === "rt" && !p.retweet) return false;
      if (kind === "img") {
        var count = (p.images ? p.images.length : 0) + (p.retweet && p.retweet.images ? p.retweet.images.length : 0);
        if (count === 0) return false;
      }
      if (kind === "incomplete" && !p.incomplete && !(p.retweet && p.retweet.incomplete)) return false;
      if (!q) return true;
      var hay = [
        p.text, p.author, p.location, p.checkin, p.source, p.time, p.bid,
        p.retweet ? p.retweet.text : "",
        p.retweet ? p.retweet.author : "",
        p.retweet ? p.retweet.location : ""
      ].join("\\n").toLowerCase();
      return hay.indexOf(q) !== -1;
    });
    if (order === "asc") filtered = filtered.slice().reverse();
    return filtered;
  }

  function render() {
    var list = getFiltered();
    var totalPages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
    if (page > totalPages) page = totalPages;
    var start = (page - 1) * PAGE_SIZE;
    var slice = list.slice(start, start + PAGE_SIZE);

    statusEl.textContent = "共匹配 " + list.length + " / " + posts.length + " 条微博";
    feedEl.textContent = "";
    slice.forEach(function (post) {
      var art = el("article", "post");
      renderPostBody(art, post, false);
      if (post.retweet) {
        var rtBox = el("div", "retweet");
        renderPostBody(rtBox, post.retweet, true);
        art.appendChild(rtBox);
      }
      feedEl.appendChild(art);
    });
    pageInfo.textContent = "第 " + page + " / " + totalPages + " 页";
    prevBtn.disabled = page <= 1;
    nextBtn.disabled = page >= totalPages;
  }

  qInput.addEventListener("input", function () { page = 1; render(); });
  typeSelect.addEventListener("change", function () { page = 1; render(); });
  sortSelect.addEventListener("change", function () { page = 1; render(); });
  clearBtn.addEventListener("click", function () {
    qInput.value = "";
    typeSelect.value = "all";
    sortSelect.value = "desc";
    page = 1;
    render();
  });
  prevBtn.addEventListener("click", function () { if (page > 1) { page--; render(); window.scrollTo(0, 0); } });
  nextBtn.addEventListener("click", function () { page++; render(); window.scrollTo(0, 0); });
  render();
})();
</script>
</body>
</html>
"""


def export_offline_html(
    archive: Archive,
    output_dir: Path,
    filename_suffix: str = "图文浏览版",
    *,
    image_root: Path | None = None,
    before_commit: Callable[[], None] | None = None,
) -> tuple[Path, dict]:
    """Export an offline self-contained HTML viewer linking any downloaded WeiboImages_<UID> assets."""
    if filename_suffix not in _DEFAULT_ALLOWED_BUNDLE_SUFFIXES:
        filename_suffix = "图文浏览版"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = md.safe_filename(archive.profile.screen_name)
    range_label = _range_filename_label(archive)
    desired_path = output_dir / f"{safe_name}_{archive.profile.id}_{range_label}_{filename_suffix}.html"

    default_offset = _default_offset(archive.posts)
    image_map = collect_manifest_image_map(archive, output_dir, image_root=image_root)
    serialized_posts = [
        _serialize_post_for_html(
            post,
            containing_id=post.id,
            relation="TOP_LEVEL",
            default_offset=default_offset,
            image_map=image_map,
        )
        for post in archive.posts
    ]

    linked_images = 0
    linked_posts = 0
    for item in serialized_posts:
        top_n = len(item["images"])
        rt_n = len(item["retweet"]["images"]) if item["retweet"] else 0
        if top_n or rt_n:
            linked_posts += 1
            linked_images += top_n + rt_n

    p = archive.profile
    meta_chips = [
        f"UID: {p.id}",
        f"归档微博: {len(archive.posts):,} 条",
        f"范围: {archive.fetch_range.label()}",
        f"快照时间: {archive.fetched_at.isoformat(sep=' ', timespec='minutes')}",
    ]
    if p.followers_count is not None:
        meta_chips.append(f"粉丝: {p.followers_count:,}")
    if linked_images:
        meta_chips.append(f"已关联本地图片: {linked_images:,} 张（{linked_posts:,} 条微博）")

    hero_meta_html = "".join(
        f"<span>{html.escape(chip)}</span>" for chip in meta_chips
    )
    html_text = (
        _HTML_TEMPLATE.replace(
            "__PAGE_TITLE__",
            html.escape(f"{p.screen_name} ({p.id}) · 微博离线图文归档"),
        )
        .replace("__HERO_TITLE__", html.escape(p.screen_name))
        .replace("__HERO_BIO__", html.escape(p.description or "暂无简介"))
        .replace("__HERO_META__", hero_meta_html)
        .replace(
            "__ARCHIVE_JSON__",
            _safe_json_for_script_tag({"posts": serialized_posts}),
        )
    )
    output_path = _atomic_write_text(desired_path, html_text, before_commit)
    return output_path, {
        "count": len(archive.posts),
        "linked_images": linked_images,
        "linked_posts": linked_posts,
        "output_bytes": output_path.stat().st_size,
        "output_chars": len(html_text),
        "layout": "html",
    }
