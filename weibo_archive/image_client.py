"""Bounded duplication of timeline rules; never calls the text fetch/hydrator."""
from __future__ import annotations

import json
import random
import threading
from dataclasses import replace
from datetime import date, datetime

from .image_models import ImageError, ImageTraversalReport, valid_identity
from .image_parser import parse_image_records, post_identity
from .models import FetchRange, RangeMode, Termination, TimestampProvenance
from .network import Cancelled, HttpClient
from .parser import parse_created_at_fact


API_CONTAINER = "https://m.weibo.cn/api/container/getIndex"
PAGE_SIZE = 100
PAGE_SPACING = (0.2, 0.5)
ABSOLUTE = {TimestampProvenance.SOURCE_OFFSET, TimestampProvenance.SOURCE_WALL}
EMPTY_MARKERS = ("这里还没有内容", "暂无微博", "暂无内容")


def validate_range(value: FetchRange):
    if not isinstance(value, FetchRange) or not isinstance(value.mode, RangeMode):
        raise ImageError("invalid_range")
    if value.mode in (RangeMode.TRIAL, RangeMode.RECENT) and (type(value.limit) is not int or value.limit <= 0):
        raise ImageError("invalid_range")
    if value.mode is RangeMode.SINCE and (not isinstance(value.since, date) or isinstance(value.since, datetime)):
        raise ImageError("invalid_range")


def _pinned(raw):
    return str(raw.get("mblogtype") or "") == "2"


def _owner(raw):
    user = raw.get("user")
    return str(user.get("id") or "") if isinstance(user, dict) else ""


def _mblogs(cards):
    for card in cards:
        if not isinstance(card, dict):
            continue
        if "mblog" in card:
            raw = card["mblog"]
            if not isinstance(raw, dict):
                raise ImageError("invalid_record")
            post_identity(raw)  # Missing identities must not become a clean empty page.
            yield raw
        group = card.get("card_group")
        if isinstance(group, list):
            yield from _mblogs(group)


class ImageTimelineClient:
    def __init__(self, *, cookie_header: str = "", cancel_event=None, http=None):
        self.cancel_event = cancel_event or threading.Event()
        self.http = http or HttpClient(cookie_header=cookie_header, cancel_event=self.cancel_event)
        self.report = ImageTraversalReport()

    def _check(self):
        if self.cancel_event.is_set():
            raise Cancelled("image_cancelled")

    def timeline_page(self, uid: str, page: int):
        if not valid_identity(uid) or type(page) is not int or page < 1:
            raise ImageError("invalid_target")
        self._check()
        self.http.wait(random.uniform(*PAGE_SPACING))
        js = self.http.json(API_CONTAINER, params={"containerid": "230413" + uid,
                            "page": page, "count": PAGE_SIZE}, timeout=15, retries=1,
                            performance_category="timeline")
        if not isinstance(js, dict):
            raise ImageError("invalid_response")
        msg = str(js.get("msg") or "")
        if "data" not in js:
            if any(x in msg for x in ("验证", "验证码", "安全验证")):
                raise ImageError("challenge")
            if "登录" in msg or "login" in str(js.get("url") or "").lower():
                raise ImageError("authentication_expired")
            if any(x in msg for x in ("访问频次", "频繁", "异常", "限制")):
                raise ImageError("rate_limited")
            if js.get("ok") == 0 and any(x in msg for x in EMPTY_MARKERS):
                return [], True
            raise ImageError("ambiguous_empty_page")
        data = js.get("data")
        cards = data.get("cards") if isinstance(data, dict) else None
        if not isinstance(cards, list):
            raise ImageError("invalid_response")
        if js.get("ok") == 0:
            raise ImageError("ambiguous_empty_page")
        rows = list(_mblogs(cards))
        if rows:
            return rows, False
        if not cards:
            return [], True
        # No values leave this local classifier or enter the manifest.
        text = json.dumps(cards, ensure_ascii=False)
        if any(x in text for x in ("验证码", "访问频次", "异常", "验证", "登录")):
            raise ImageError("challenge")
        if any(x in text for x in EMPTY_MARKERS):
            return [], True
        raise ImageError("ambiguous_empty_page")

    def iter_posts(self, uid: str, fetch_range: FetchRange):
        """Yield one W and its RT as a group, checkpointing before each yield.

        Pinned records remain included but never consume a Recent/Trial target.
        Counts therefore describe normal timeline posts, with pins as extras.
        """
        validate_range(fetch_range)
        if not valid_identity(uid):
            raise ImageError("invalid_target")
        self.report = ImageTraversalReport()
        seen, boundary_seen = set(), set()
        old_pages = no_progress = 0
        page = 1
        while True:
            self._check()
            rows, natural = self.timeline_page(uid, page)
            self.report = replace(self.report, pages_fetched=page)
            if natural:
                self.report = replace(self.report, termination=Termination.NATURAL)
                return
            normal = [r for r in rows if not _pinned(r) and _owner(r) in ("", uid)]
            facts = [parse_created_at_fact(r.get("created_at")) for r in normal]
            absolute = [d.date() for d, p in facts if d is not None and p in ABSOLUTE]
            if absolute:
                frontier = min(absolute).isoformat()
                self.report = replace(self.report, frontier=min(self.report.frontier or frontier, frontier))
            normal_ids = {post_identity(r) for r in normal}
            fresh_boundary = bool(normal_ids - boundary_seen)
            boundary_seen.update(normal_ids)
            if fetch_range.mode is RangeMode.SINCE:
                wholly_old = bool(absolute) and len(absolute) == len(normal) and all(d < fetch_range.since for d in absolute)
                # A repeated old page is not two independent pages of evidence.
                old_pages = old_pages + 1 if wholly_old and fresh_boundary else 0
                if old_pages >= 2:
                    self.report = replace(self.report, termination=Termination.SINCE_REACHED)
                    return
            before = len(seen)
            for raw in rows:
                self._check()
                if _owner(raw) not in ("", uid):
                    continue
                identity = post_identity(raw)
                if identity in seen:
                    continue
                seen.add(identity)
                self.report = replace(self.report, posts_inspected=self.report.posts_inspected + 1)
                when, provenance = parse_created_at_fact(raw.get("created_at"))
                if (fetch_range.mode is RangeMode.SINCE and when is not None
                        and provenance in ABSOLUTE and when.date() < fetch_range.since):
                    continue
                if not _pinned(raw):
                    self.report = replace(self.report, timeline_posts=self.report.timeline_posts + 1)
                records = parse_image_records(raw)
                yield records
                if (fetch_range.mode in (RangeMode.RECENT, RangeMode.TRIAL)
                        and self.report.timeline_posts >= fetch_range.limit):
                    self.report = replace(self.report, termination=Termination.TARGET_COUNT)
                    return
            no_progress = no_progress + 1 if len(seen) == before else 0
            if no_progress >= 3:
                raise ImageError("no_progress")
            page += 1
