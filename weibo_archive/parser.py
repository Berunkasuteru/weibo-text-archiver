from __future__ import annotations

from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Optional

from .htmltext import html_to_text, strip_html
from .models import (
    ContentState,
    Engagement,
    IncompleteReason,
    MediaInfo,
    Post,
    TimestampProvenance,
    UserProfile,
    normalize_optional_uid,
)


def parse_count(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)

    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        if s.endswith("万+"):
            return int(float(s[:-2]) * 10000)
        if s.endswith("万"):
            return int(float(s[:-1]) * 10000)
        if s.endswith("亿"):
            return int(float(s[:-1]) * 100000000)
        return int(float(s))
    except ValueError:
        return None


def parse_created_at_fact(
    value: Any,
    now: Optional[datetime] = None,
) -> tuple[Optional[datetime], TimestampProvenance]:
    if value is None:
        return None, TimestampProvenance.UNKNOWN
    s = str(value).strip()
    if not s:
        return None, TimestampProvenance.UNKNOWN

    try:
        dt = parsedate_to_datetime(s)
        provenance = (
            TimestampProvenance.SOURCE_OFFSET
            if dt.utcoffset() is not None
            else TimestampProvenance.SOURCE_WALL
        )
        return dt, provenance
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt), TimestampProvenance.SOURCE_WALL
        except ValueError:
            pass

    now = now or datetime.now()
    if now.utcoffset() is not None:
        # The raw relative value did not provide an offset. Keep only the supplied
        # wall clock and mark the result as unverified rather than inventing one.
        now = now.replace(tzinfo=None)

    if "刚刚" in s:
        return now, TimestampProvenance.RELATIVE_UNVERIFIED

    if "分钟" in s:
        try:
            return (
                now - timedelta(minutes=int(s.split("分钟", 1)[0])),
                TimestampProvenance.RELATIVE_UNVERIFIED,
            )
        except ValueError:
            return None, TimestampProvenance.UNKNOWN

    if "小时" in s:
        try:
            return (
                now - timedelta(hours=int(s.split("小时", 1)[0])),
                TimestampProvenance.RELATIVE_UNVERIFIED,
            )
        except ValueError:
            return None, TimestampProvenance.UNKNOWN

    if s.startswith("昨天"):
        clock = s.replace("昨天", "").strip()
        base = now - timedelta(days=1)
        if clock:
            try:
                h, m = clock.split(":", 1)
                return (
                    base.replace(
                        hour=int(h), minute=int(m), second=0, microsecond=0
                    ),
                    TimestampProvenance.RELATIVE_UNVERIFIED,
                )
            except Exception:
                pass
        return base, TimestampProvenance.RELATIVE_UNVERIFIED

    return None, TimestampProvenance.UNKNOWN


def parse_created_at(value: Any, now: Optional[datetime] = None) -> Optional[datetime]:
    return parse_created_at_fact(value, now)[0]


def _media_info(raw: dict, article_from_text: bool) -> MediaInfo:
    pics = raw.get("pics") or []
    image_count = 0
    video_count = 0

    if isinstance(pics, list):
        for pic in pics:
            if not isinstance(pic, dict):
                image_count += 1
                continue
            ptype = str(pic.get("type") or "").lower()
            if ptype == "video":
                video_count += 1
                continue
            image_count += 1
            if ptype == "livephoto" and pic.get("videoSrc"):
                video_count += 1

    pic_num = parse_count(raw.get("pic_num"))
    if pic_num is not None and pic_num > image_count and video_count == 0:
        # The list endpoint may expose only the first nine images.
        image_count = pic_num

    page_info = raw.get("page_info")
    article = article_from_text
    if isinstance(page_info, dict):
        ptype = str(page_info.get("type") or "").lower()
        object_type = str(page_info.get("object_type") or "").lower()
        if ptype == "video" and video_count == 0:
            video_count = 1
        if "article" in ptype or "article" in object_type:
            article = True

    return MediaInfo(images=image_count, videos=video_count, article=article)


def parse_post(
    raw: dict,
    *,
    allow_retweet: bool = True,
    incomplete_reason: IncompleteReason | None = None,
    retweet_incomplete_reason: IncompleteReason | None = None,
) -> Post:
    if not isinstance(raw, dict):
        raise ValueError("post payload is not an object")

    visible_text, location_from_html, article_from_text = html_to_text(
        raw.get("text", "")
    )

    user = raw.get("user") or {}
    author = str(user.get("screen_name") or "") if isinstance(user, dict) else ""
    raw_author_id = user.get("id") if isinstance(user, dict) else None
    author_id = normalize_optional_uid(raw_author_id)

    location = str(
        raw.get("region_name")
        or raw.get("location")
        or location_from_html
        or ""
    ).strip()

    retweet = None
    retweeted_status = raw.get("retweeted_status")
    if allow_retweet and isinstance(retweeted_status, dict) and retweeted_status.get("id"):
        # One explicit retweet layer is enough for the archive model. If Weibo ever
        # starts returning nested retweets again, the raw-parser contract test should fail.
        retweet = parse_post(
            retweeted_status,
            allow_retweet=False,
            incomplete_reason=retweet_incomplete_reason,
        )

    edit_count = parse_count(raw.get("edit_count")) or 0
    created_at, created_at_provenance = parse_created_at_fact(raw.get("created_at"))

    return Post(
        id=str(raw.get("id") or raw.get("mid") or ""),
        bid=str(raw.get("bid") or ""),
        created_at=created_at,
        created_at_provenance=created_at_provenance,
        text=None if incomplete_reason is not None else visible_text,
        source=strip_html(raw.get("source") or ""),
        location=location,
        author=author,
        author_id=author_id,
        engagement=Engagement(
            reposts=parse_count(raw.get("reposts_count")),
            comments=parse_count(raw.get("comments_count")),
            likes=parse_count(raw.get("attitudes_count")),
        ),
        media=_media_info(raw, article_from_text),
        retweet=retweet,
        edited=edit_count > 0,
        edit_count=edit_count,
        content_state=(
            ContentState.INCOMPLETE
            if incomplete_reason is not None
            else ContentState.COMPLETE
        ),
        text_preview=visible_text if incomplete_reason is not None else None,
        incomplete_reason=incomplete_reason,
    )


_PROFILE_LABELS = {
    "生日": "birthday",
    "所在地": "location",
    "IP属地": "ip_location",
    "小学": "education",
    "初中": "education",
    "高中": "education",
    "大学": "education",
    "公司": "company",
    "注册时间": "registration_time",
    "阳光信用": "sunshine",
}


def _walk_cards(cards):
    if not isinstance(cards, list):
        return
    for card in cards:
        if not isinstance(card, dict):
            continue
        yield card
        group = card.get("card_group")
        if isinstance(group, list):
            yield from _walk_cards(group)


def parse_profile(uid: str, basic_json: dict, detail_json: Optional[dict] = None) -> UserProfile:
    data = basic_json.get("data") or {}
    info = data.get("userInfo") or {}
    if not isinstance(info, dict):
        raise ValueError("用户资料响应中没有 userInfo")

    extras: dict[str, str] = {}
    education: list[str] = []

    if isinstance(detail_json, dict):
        detail_data = detail_json.get("data") or {}
        for card in _walk_cards(detail_data.get("cards") or []):
            label = str(card.get("item_name") or "")
            value = str(card.get("item_content") or "").strip()
            key = _PROFILE_LABELS.get(label)
            if not key or not value:
                continue
            if key == "education":
                education.append(value)
            else:
                extras[key] = value

    return UserProfile(
        id=str(uid),
        screen_name=str(info.get("screen_name") or uid),
        description=str(info.get("description") or ""),
        followers_count=parse_count(info.get("followers_count")),
        follow_count=parse_count(info.get("follow_count")),
        statuses_count=parse_count(info.get("statuses_count")),
        location=extras.get("location", ""),
        verified=info.get("verified") if isinstance(info.get("verified"), bool) else None,
        verified_reason=str(info.get("verified_reason") or ""),
        registration_time=extras.get("registration_time", ""),
        gender=str(info.get("gender") or ""),
        birthday=extras.get("birthday", ""),
        company=extras.get("company", ""),
        education="；".join(education),
        ip_location=extras.get("ip_location", ""),
    )


def extract_mblogs(cards) -> list[dict]:
    result: list[dict] = []
    for card in _walk_cards(cards or []):
        mblog = card.get("mblog")
        if isinstance(mblog, dict) and mblog.get("id"):
            result.append(mblog)
    return result
