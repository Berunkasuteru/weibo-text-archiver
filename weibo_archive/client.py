from __future__ import annotations

import json
import random
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from json import JSONDecoder
from typing import Callable, Optional

from .models import (
    Archive,
    ContentState,
    FetchRange,
    RangeMode,
    FetchReport,
    IncompleteReason,
    Post,
    Termination,
)
from .network import (
    Cancelled,
    HttpClient,
    InvalidResponse,
    NetworkError,
    RateLimited,
    SafeRequestDiagnostic,
    classify_non_json_response,
)
from .parser import extract_mblogs, parse_created_at, parse_post, parse_profile


API_CONTAINER = "https://m.weibo.cn/api/container/getIndex"
API_EXTEND = "https://m.weibo.cn/statuses/extend"
DETAIL_URL = "https://m.weibo.cn/detail/{id}"

PAGE_SIZE = 20
PAGE_DELAY = (2.0, 5.0)
LONGTEXT_DELAY = (1.0, 2.2)
BATCH_POSTS = 60
BATCH_DELAY = 8.0
SESSION_POSTS = 2000
SESSION_REST = 120.0


class InvalidUser(RuntimeError):
    pass


class NeedLogin(RuntimeError):
    pass


@dataclass(frozen=True)
class LongTextAttemptDiagnostic:
    fallback: str
    host: str
    path: str
    outcome: str
    status: Optional[int] = None
    content_type: str = ""
    body_bytes: Optional[int] = None
    json_keys: tuple[str, ...] = ()
    data_keys: tuple[str, ...] = ()
    content_field: str = ""
    content_chars: Optional[int] = None
    embedded_status_present: Optional[bool] = None
    id_matches: Optional[bool] = None

    def summary(self) -> str:
        parts = [
            f"fallback={self.fallback}",
            f"endpoint={self.host}{self.path}",
            f"outcome={self.outcome}",
        ]
        if self.status is not None:
            parts.append(f"http={self.status}")
        if self.content_type:
            parts.append(f"content_type={self.content_type}")
        if self.body_bytes is not None:
            parts.append(f"body_bytes={self.body_bytes}")
        if self.json_keys:
            parts.append("json_keys=" + ",".join(self.json_keys))
        if self.data_keys:
            parts.append("data_keys=" + ",".join(self.data_keys))
        if self.content_field:
            parts.append(f"content_field={self.content_field}")
        if self.content_chars is not None:
            parts.append(f"content_chars={self.content_chars}")
        if self.embedded_status_present is not None:
            parts.append(
                "embedded_status=" + ("yes" if self.embedded_status_present else "no")
            )
        if self.id_matches is not None:
            parts.append("id_match=" + ("yes" if self.id_matches else "no"))
        return "; ".join(parts)


class IncompleteContent(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        attempts: tuple[LongTextAttemptDiagnostic, ...] = (),
    ):
        self.attempts = attempts
        if attempts:
            diagnostics = "\n".join(
                f"- {attempt.summary()}" for attempt in attempts
            )
            message = f"{message}\n脱敏诊断：\n{diagnostics}"
        super().__init__(message)


class ContentUnavailable(IncompleteContent):
    def __init__(
        self,
        post_id: str,
        *,
        reason: IncompleteReason,
        attempts: tuple[LongTextAttemptDiagnostic, ...] = (),
    ):
        self.post_id = post_id
        self.reason = reason
        super().__init__(
            f"长微博 {post_id} 当前无法取得并验证完整正文。",
            attempts=attempts,
        )


@dataclass(frozen=True)
class HydrationOutcome:
    raw: dict
    top_incomplete_reason: IncompleteReason | None = None
    retweet_incomplete_reason: IncompleteReason | None = None


Progress = Callable[[str, Optional[dict]], None]


def _noop_progress(message: str, data: Optional[dict] = None):
    pass


def _created_at_of(raw: dict) -> Optional[datetime]:
    return parse_created_at(raw.get("created_at"))


def _is_long(raw: dict) -> bool:
    return bool(raw.get("isLongText"))


def _is_pinned(raw: dict) -> bool:
    # Mobile timeline cards use mblogtype=2 for posts injected ahead of the
    # chronological stream. They remain valid posts, but are not crawl frontier.
    return str(raw.get("mblogtype") or "") == "2"


def _raw_user_id(raw: dict) -> str:
    user = raw.get("user")
    return str(user.get("id") or "") if isinstance(user, dict) else ""


def _merge_long_text(original: dict, long_html: str) -> dict:
    merged = dict(original)
    merged["text"] = long_html
    return merged


def _safe_json_keys(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    keys = {
        str(key)
        for key in value
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(key))
    }
    return tuple(sorted(keys)[:24])


def _attempt_from_network_error(
    fallback: str,
    exc: NetworkError,
    *,
    default_path: str,
) -> LongTextAttemptDiagnostic:
    diagnostic: SafeRequestDiagnostic | None = exc.diagnostic
    if diagnostic is None:
        return LongTextAttemptDiagnostic(
            fallback=fallback,
            host="m.weibo.cn",
            path=default_path,
            outcome="network_error",
        )
    return LongTextAttemptDiagnostic(
        fallback=fallback,
        host=diagnostic.host,
        path=diagnostic.path,
        outcome=diagnostic.classification,
        status=diagnostic.status,
        content_type=diagnostic.content_type,
        body_bytes=diagnostic.body_bytes,
    )


def _embedded_status_from_detail(html: str) -> Optional[dict]:
    """
    Extract the JSON object assigned to "status" from m.weibo.cn/detail HTML.

    JSONDecoder.raw_decode avoids depending on a specific trailing JS field such
    as "call", which has changed across mobile-site revisions.
    """
    marker = '"status":'
    pos = html.find(marker)
    if pos < 0:
        return None
    pos += len(marker)
    while pos < len(html) and html[pos].isspace():
        pos += 1

    try:
        value, _ = JSONDecoder().raw_decode(html[pos:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


class WeiboClient:
    def __init__(
        self,
        *,
        cookie_header: str,
        cancel_event: threading.Event,
        progress: Progress = _noop_progress,
    ):
        self.http = HttpClient(cookie_header=cookie_header, cancel_event=cancel_event)
        self.cancel_event = cancel_event
        self.progress = progress
        self.posts_since_batch_rest = 0
        self.posts_since_session_rest = 0
        self.long_text_cache: dict[str, str] = {}
        self.unavailable_cache: dict[str, IncompleteReason] = {}
        self.long_text_diagnostics: dict[
            str, tuple[LongTextAttemptDiagnostic, ...]
        ] = {}
        self.unique_long_text_attempted = 0
        self.unique_content_unavailable = 0
        self.consecutive_unique_unavailable = 0
        self._long_text_attempted_ids: set[str] = set()
        self._content_unavailable_ids: set[str] = set()

    def _emit(self, message: str, **data):
        self.progress(message, data or None)

    def _wait_random(self, low: float, high: float):
        self.http.wait(random.uniform(low, high))

    def preheat(self):
        self._emit("正在建立微博会话…")
        # Preheating lets the mobile site issue any current fingerprint/session cookies.
        self.http.request("https://m.weibo.cn/", timeout=12, retries=2)

    def fetch_profile(self, uid: str):
        self._emit("正在读取账号资料…")
        basic = self.http.json(
            API_CONTAINER,
            params={"containerid": "100505" + uid},
            timeout=15,
            retries=4,
        )

        data = basic.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("userInfo"), dict):
            msg = str(basic.get("msg") or "")
            if "这里还没有内容" in msg:
                raise InvalidUser("没有找到该账号，可能 UID 不正确或账号已注销。")
            if basic.get("url") or "登录" in msg or "验证" in msg:
                raise NeedLogin("微博没有返回用户资料，请重新扫码登录后再试。")
            raise InvalidResponse("用户资料响应缺少 userInfo。")

        detail = None
        try:
            self._wait_random(0.4, 1.0)
            detail = self.http.json(
                API_CONTAINER,
                params={"containerid": f"230283{uid}_-_INFO"},
                timeout=15,
                retries=2,
            )
        except NetworkError:
            # Extended profile is useful metadata, not required to preserve post text.
            self._emit("扩展资料暂时不可用，将继续备份正文。")

        return parse_profile(uid, basic, detail)

    def _begin_long_text_acquisition(self, post_id: str) -> None:
        if post_id not in self._long_text_attempted_ids:
            self._long_text_attempted_ids.add(post_id)
            self.unique_long_text_attempted += 1

    def _enforce_long_text_safety_fuse(
        self,
        attempts: tuple[LongTextAttemptDiagnostic, ...] = (),
    ) -> None:
        # Conservative fail-closed safety fuse: a run or abnormal proportion of
        # inaccessible long posts may indicate a global session/endpoint problem.
        too_many_consecutive = self.consecutive_unique_unavailable >= 3
        excessive_ratio = (
            self.unique_long_text_attempted >= 5
            and self.unique_content_unavailable * 5
            > self.unique_long_text_attempted
        )
        if too_many_consecutive or excessive_ratio:
            raise IncompleteContent(
                "长微博不可取得的比例异常，无法继续安全地将其判断为单条历史内容问题。"
                f"已尝试 {self.unique_long_text_attempted} 个不同 ID，"
                f"其中 {self.unique_content_unavailable} 个当前不可取得，"
                f"连续 {self.consecutive_unique_unavailable} 个。",
                attempts=attempts,
            )

    def _record_long_text_success(
        self,
        attempts: tuple[LongTextAttemptDiagnostic, ...] = (),
    ) -> None:
        self.consecutive_unique_unavailable = 0
        self._enforce_long_text_safety_fuse(attempts)

    def _record_content_unavailable(
        self,
        post_id: str,
        attempts: tuple[LongTextAttemptDiagnostic, ...],
    ) -> None:
        if post_id in self._content_unavailable_ids:
            return
        self._content_unavailable_ids.add(post_id)
        self.unique_content_unavailable += 1
        self.consecutive_unique_unavailable += 1

        self._enforce_long_text_safety_fuse(attempts)

    def _fetch_full_text_html(self, raw: dict) -> str:
        post_id = str(raw.get("id") or "")
        if not post_id:
            raise IncompleteContent("长微博缺少 ID，无法获取全文。")

        cached = self.long_text_cache.get(post_id)
        if cached:
            return cached

        unavailable_reason = self.unavailable_cache.get(post_id)
        if unavailable_reason is not None:
            raise ContentUnavailable(
                post_id,
                reason=unavailable_reason,
                attempts=self.long_text_diagnostics.get(post_id, ()),
            )

        self._begin_long_text_acquisition(post_id)

        self._emit(f"正在读取长微博全文 {post_id}…")
        self._wait_random(*LONGTEXT_DELAY)
        attempts: list[LongTextAttemptDiagnostic] = []

        # Fast path: statuses/extend is a small JSON response.
        try:
            js, response = self.http.json_response(
                API_EXTEND,
                params={"id": post_id},
                headers={"Referer": f"https://m.weibo.cn/detail/{post_id}"},
                timeout=15,
                retries=3,
            )
            response_meta = response.safe_diagnostic("response")
            data = js.get("data")
            content = None
            content_field = ""
            if isinstance(data, dict):
                content = data.get("longTextContent")
                if isinstance(content, str) and content.strip():
                    content_field = "data.longTextContent"
                if not content:
                    long_text = data.get("longText")
                    if isinstance(long_text, dict):
                        content = long_text.get("longTextContent")
                        if isinstance(content, str) and content.strip():
                            content_field = "data.longText.longTextContent"
                if isinstance(content, str) and content.strip():
                    attempts.append(
                        LongTextAttemptDiagnostic(
                            fallback="extend",
                            host=response_meta.host,
                            path=response_meta.path,
                            outcome="success",
                            status=response.status,
                            content_type=response.content_type,
                            body_bytes=len(response.body),
                            json_keys=_safe_json_keys(js),
                            data_keys=_safe_json_keys(data),
                            content_field=content_field,
                            content_chars=len(content),
                        )
                    )
                    self.long_text_diagnostics[post_id] = tuple(attempts)
                    self.long_text_cache[post_id] = content
                    self._record_long_text_success(tuple(attempts))
                    return content
            outcome = (
                "json_ok_0"
                if js.get("ok") == 0
                else "json_data_not_object"
                if not isinstance(data, dict)
                else "json_missing_full_text"
            )
            attempts.append(
                LongTextAttemptDiagnostic(
                    fallback="extend",
                    host=response_meta.host,
                    path=response_meta.path,
                    outcome=outcome,
                    status=response.status,
                    content_type=response.content_type,
                    body_bytes=len(response.body),
                    json_keys=_safe_json_keys(js),
                    data_keys=_safe_json_keys(data),
                    content_chars=len(content) if isinstance(content, str) else None,
                )
            )
        except NetworkError as exc:
            attempts.append(
                _attempt_from_network_error(
                    "extend", exc, default_path="/statuses/extend"
                )
            )

        # Compatibility fallback: current weibo-crawler reads the embedded status
        # object from the mobile detail page. We do the same, but keep TLS verification on.
        self._wait_random(0.5, 1.2)
        try:
            html, response = self.http.text_response(
                DETAIL_URL.format(id=post_id),
                headers={"Referer": "https://m.weibo.cn/"},
                timeout=18,
                retries=3,
            )
            response_meta = response.safe_diagnostic("response")
            status = _embedded_status_from_detail(html)
            if isinstance(status, dict):
                status_id = str(status.get("id") or status.get("mid") or "")
                id_matches = status_id == post_id
                content = status.get("text")
                if id_matches and isinstance(content, str) and content.strip():
                    attempts.append(
                        LongTextAttemptDiagnostic(
                            fallback="detail",
                            host=response_meta.host,
                            path=response_meta.path,
                            outcome="success",
                            status=response.status,
                            content_type=response.content_type,
                            body_bytes=len(response.body),
                            content_field="status.text",
                            content_chars=len(content),
                            embedded_status_present=True,
                            id_matches=True,
                        )
                    )
                    self.long_text_diagnostics[post_id] = tuple(attempts)
                    self.long_text_cache[post_id] = content
                    self._record_long_text_success(tuple(attempts))
                    return content
                attempts.append(
                    LongTextAttemptDiagnostic(
                        fallback="detail",
                        host=response_meta.host,
                        path=response_meta.path,
                        outcome=(
                            "embedded_status_id_mismatch"
                            if not id_matches
                            else "embedded_status_missing_text"
                        ),
                        status=response.status,
                        content_type=response.content_type,
                        body_bytes=len(response.body),
                        content_chars=len(content) if isinstance(content, str) else None,
                        embedded_status_present=True,
                        id_matches=id_matches,
                    )
                )
            else:
                classification = classify_non_json_response(
                    html, response.content_type
                )
                attempts.append(
                    LongTextAttemptDiagnostic(
                        fallback="detail",
                        host=response_meta.host,
                        path=response_meta.path,
                        outcome=(
                            "embedded_status_absent"
                            if classification == "unexpected_html"
                            else classification
                        ),
                        status=response.status,
                        content_type=response.content_type,
                        body_bytes=len(response.body),
                        embedded_status_present=False,
                    )
                )
        except NetworkError as exc:
            attempts.append(
                _attempt_from_network_error(
                    "detail", exc, default_path=f"/detail/{post_id}"
                )
            )

        attempt_tuple = tuple(attempts)
        self.long_text_diagnostics[post_id] = attempt_tuple
        if (
            [attempt.fallback for attempt in attempt_tuple] == ["extend", "detail"]
            and all(
                attempt.outcome == "no_view_permission_html"
                for attempt in attempt_tuple
            )
        ):
            reason = IncompleteReason.CONTENT_UNAVAILABLE
            self.unavailable_cache[post_id] = reason
            self._record_content_unavailable(post_id, attempt_tuple)
            raise ContentUnavailable(
                post_id,
                reason=reason,
                attempts=attempt_tuple,
            )
        raise IncompleteContent(
            f"长微博 {post_id} 的全文无法取得。为避免静默保存截断正文，本次导出已停止。",
            attempts=attempt_tuple,
        )

    def _hydrate_long_texts(self, raw: dict) -> HydrationOutcome:
        hydrated = dict(raw)
        top_reason = None
        retweet_reason = None

        if _is_long(hydrated):
            try:
                hydrated = _merge_long_text(
                    hydrated,
                    self._fetch_full_text_html(hydrated),
                )
            except ContentUnavailable as exc:
                top_reason = exc.reason

        rt = hydrated.get("retweeted_status")
        if isinstance(rt, dict) and rt.get("id") and _is_long(rt):
            try:
                hydrated["retweeted_status"] = _merge_long_text(
                    rt,
                    self._fetch_full_text_html(rt),
                )
            except ContentUnavailable as exc:
                retweet_reason = exc.reason

        return HydrationOutcome(
            raw=hydrated,
            top_incomplete_reason=top_reason,
            retweet_incomplete_reason=retweet_reason,
        )

    def _validate_post_hydration(
        self,
        post: Post,
        outcome: HydrationOutcome,
    ) -> None:
        if outcome.top_incomplete_reason is not None and not (
            post.content_state is ContentState.INCOMPLETE
            and post.text is None
        ):
            raise IncompleteContent(
                "顶层长微博已确认全文不可取得，但 parser 未保留 INCOMPLETE 语义。"
            )

        if outcome.retweet_incomplete_reason is not None and not (
            post.retweet is not None
            and post.retweet.content_state is ContentState.INCOMPLETE
            and post.retweet.text is None
        ):
            raise IncompleteContent(
                "转发原文已确认全文不可取得，但 parser 未保留 INCOMPLETE 语义。"
            )

    def _timeline_page(self, uid: str, page: int) -> tuple[list[dict], bool]:
        self._wait_random(*PAGE_DELAY)
        js = self.http.json(
            API_CONTAINER,
            params={
                "containerid": "230413" + uid,
                "page": page,
                "count": PAGE_SIZE,
            },
            timeout=15,
            retries=4,
        )

        if "data" not in js:
            msg = str(js.get("msg") or "")
            empty_markers = ("这里还没有内容", "暂无微博", "暂无内容")
            if js.get("ok") == 0 and any(marker in msg for marker in empty_markers):
                return [], True

            # Fail closed: an unknown ok=0 response is never treated as the end
            # of the timeline. Anti-bot JSON often has no challenge URL.
            limit_markers = ("验证", "访问频次", "频繁", "异常", "限制", "登录")
            if js.get("url") or any(marker in msg for marker in limit_markers):
                raise RateLimited(
                    "微博要求额外验证或限制了访问。请稍后再试；本次不会生成伪完整备份。"
                )
            raise InvalidResponse(
                "微博列表响应缺少 data，且不能证明这是自然结束。"
                + (f" 返回信息：{msg}" if msg else "")
            )

        data = js.get("data") or {}
        cards = data.get("cards") if isinstance(data, dict) else None
        if not isinstance(cards, list):
            raise InvalidResponse("微博列表响应中的 cards 不是数组。")

        mblogs = extract_mblogs(cards)
        if mblogs:
            return mblogs, False

        # Backup-trust rule: "no parsed posts" is not automatically "natural end".
        # An explicit empty card list is the clean end-of-timeline signal.
        if not cards:
            return [], True

        # Some accounts/pages return a human-readable empty-content card.
        card_text = json.dumps(cards, ensure_ascii=False)
        if "这里还没有内容" in card_text or "暂无微博" in card_text or "暂无内容" in card_text:
            return [], True

        # Captcha / anti-bot / unknown card layouts must fail loudly instead of
        # becoming a false "complete archive".
        if any(
            token in card_text
            for token in ("验证码", "访问频次", "异常", "验证", "登录")
        ):
            raise RateLimited(
                "微博返回了验证/限制页面，本次抓取不能证明完整性。请稍后再试。"
            )

        raise InvalidResponse(
            "微博列表返回了非空卡片，但其中没有可识别微博。"
            "为避免误判为自然结束，V7 已停止导出。"
        )

    def _rest_if_needed(self):
        if self.posts_since_batch_rest >= BATCH_POSTS:
            self._emit(f"已抓取一批微博，安全休息 {int(BATCH_DELAY)} 秒…")
            self.http.wait(BATCH_DELAY)
            self.posts_since_batch_rest = 0

        if self.posts_since_session_rest >= SESSION_POSTS:
            self._emit(
                f"已连续抓取 {SESSION_POSTS} 条，为降低风控概率休息 {int(SESSION_REST)} 秒…"
            )
            self.http.wait(SESSION_REST)
            self.posts_since_session_rest = 0

    def fetch(self, uid: str, fetch_range: FetchRange) -> Archive:
        self.preheat()
        profile = self.fetch_profile(uid)

        posts_by_id: dict[str, Post] = {}
        page = 1
        consecutive_old_pages = 0
        consecutive_no_new_pages = 0
        termination: Termination | None = None
        timeline_candidates = 0
        crawl_frontier: datetime | None = None
        limited_range = (
            fetch_range.mode in (RangeMode.RECENT, RangeMode.TRIAL)
            and bool(fetch_range.limit)
        )
        target = fetch_range.limit or 0

        while True:
            if self.cancel_event.is_set():
                raise Cancelled("任务已取消。")

            self._emit(
                f"正在读取第 {page} 页…",
                page=page,
                posts=timeline_candidates if limited_range else len(posts_by_id),
                count_label="条候选" if limited_range else "条记录",
            )
            mblogs, natural_end = self._timeline_page(uid, page)
            count_before_page = len(posts_by_id)

            if natural_end:
                termination = Termination.NATURAL
                break

            timeline_mblogs = [
                raw
                for raw in mblogs
                if not _is_pinned(raw) and _raw_user_id(raw) in ("", uid)
            ]
            page_dates = [_created_at_of(raw) for raw in timeline_mblogs]
            known_dates = [d for d in page_dates if d is not None]
            if known_dates:
                page_frontier = min(known_dates)
                crawl_frontier = (
                    page_frontier
                    if crawl_frontier is None
                    else min(crawl_frontier, page_frontier)
                )

            # A since-date range can stop without hydrating pages that are wholly older.
            if fetch_range.mode is RangeMode.SINCE and fetch_range.since and known_dates:
                page_dates_complete = len(known_dates) == len(timeline_mblogs)
                if (
                    page_dates_complete
                    and all(d.date() < fetch_range.since for d in known_dates)
                ):
                    consecutive_old_pages += 1
                else:
                    # Unknown timestamps must not help trigger an early stop.
                    # They are retained because silently discarding them could lose
                    # a post whose date format changed.
                    consecutive_old_pages = 0

                # Two consecutive wholly-old timeline pages avoid treating one
                # anomalous page as proof that the requested boundary was reached.
                if consecutive_old_pages >= 2:
                    termination = Termination.SINCE_REACHED
                    break

            for raw in mblogs:
                if self.cancel_event.is_set():
                    raise Cancelled("任务已取消。")

                raw_uid = _raw_user_id(raw)
                if raw_uid and raw_uid != uid:
                    # Defensive: ignore recommendation cards if they ever appear.
                    continue

                raw_id = str(raw.get("id") or raw.get("mid") or "")
                if raw_id and raw_id in posts_by_id:
                    continue

                created_at = _created_at_of(raw)
                if (
                    fetch_range.mode is RangeMode.SINCE
                    and fetch_range.since
                    and created_at is not None
                    and created_at.date() < fetch_range.since
                ):
                    continue

                hydration = self._hydrate_long_texts(raw)
                post = parse_post(
                    hydration.raw,
                    incomplete_reason=hydration.top_incomplete_reason,
                    retweet_incomplete_reason=hydration.retweet_incomplete_reason,
                )
                self._validate_post_hydration(post, hydration)
                if not post.id:
                    continue
                posts_by_id[post.id] = post
                self.posts_since_batch_rest += 1
                self.posts_since_session_rest += 1

                if limited_range and not _is_pinned(raw):
                    timeline_candidates += 1
                    if timeline_candidates >= target:
                        termination = Termination.TARGET_COUNT
                        break

            if len(posts_by_id) == count_before_page:
                consecutive_no_new_pages += 1
            else:
                consecutive_no_new_pages = 0

            if consecutive_no_new_pages >= 3:
                raise InvalidResponse(
                    "连续 3 页没有出现新的微博 ID，分页似乎没有继续向历史推进。"
                    "为避免无限循环或伪完整备份，V7 已停止。"
                )

            progress_count = (
                timeline_candidates if limited_range else len(posts_by_id)
            )
            count_name = "时间线候选" if limited_range else "记录"
            self._emit(
                f"已获得 {progress_count} 条{count_name}"
                + (
                    f" · 时间线推进到 {crawl_frontier:%Y-%m-%d}"
                    if crawl_frontier
                    else ""
                ),
                page=page,
                posts=progress_count,
                count_label="条候选" if limited_range else "条记录",
                frontier=crawl_frontier.isoformat() if crawl_frontier else None,
            )

            if termination is not None:
                break

            self._rest_if_needed()
            page += 1

        posts = sorted(
            posts_by_id.values(),
            key=lambda p: (p.created_at or datetime.min, p.id),
            reverse=True,
        )

        if fetch_range.mode in (RangeMode.RECENT, RangeMode.TRIAL) and fetch_range.limit:
            posts = posts[: fetch_range.limit]

        if not posts:
            raise InvalidResponse(
                "没有取得可导出的微博正文。可能是账号没有公开内容、Cookie 失效或接口受限。"
            )

        oldest = min((p.created_at for p in posts if p.created_at), default=None)
        newest = max((p.created_at for p in posts if p.created_at), default=None)

        assert termination is not None
        report = FetchReport(
            pages_fetched=page,
            requests_made=self.http.request_count,
            termination=termination,
            oldest_reached=oldest,
            newest_reached=newest,
            profile_statuses_count=profile.statuses_count,
        )
        return Archive(
            profile=profile,
            posts=tuple(posts),
            fetch_range=fetch_range,
            report=report,
        )
