from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


@dataclass(frozen=True)
class Engagement:
    reposts: Optional[int] = None
    comments: Optional[int] = None
    likes: Optional[int] = None


@dataclass(frozen=True)
class MediaInfo:
    images: int = 0
    videos: int = 0
    article: bool = False


class ContentState(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class IncompleteReason(str, Enum):
    CONTENT_UNAVAILABLE = "content_unavailable"


@dataclass(frozen=True)
class Post:
    id: str
    bid: str
    created_at: Optional[datetime]
    text: Optional[str]
    source: str
    location: str
    author: str
    engagement: Engagement
    media: MediaInfo
    retweet: Optional["Post"] = None
    edited: bool = False
    edit_count: int = 0
    content_state: ContentState = ContentState.COMPLETE
    text_preview: Optional[str] = None
    incomplete_reason: Optional[IncompleteReason] = None

    def __post_init__(self) -> None:
        if not isinstance(self.content_state, ContentState):
            raise TypeError("content_state must be a ContentState")

        if self.content_state is ContentState.COMPLETE:
            if not isinstance(self.text, str):
                raise ValueError("complete post text must be a string")
            if self.text_preview is not None:
                raise ValueError("complete post cannot have text_preview")
            if self.incomplete_reason is not None:
                raise ValueError("complete post cannot have incomplete_reason")
            return

        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("incomplete post must have a non-empty id")
        if self.text is not None:
            raise ValueError("incomplete post text must be None")
        if self.text_preview is not None and not isinstance(self.text_preview, str):
            raise TypeError("text_preview must be a string or None")
        if not isinstance(self.incomplete_reason, IncompleteReason):
            raise ValueError("incomplete post must have an incomplete_reason")


@dataclass(frozen=True)
class ArchiveIntegrity:
    total_posts: int
    complete_records: int
    incomplete_records: int
    incomplete_top_level: int
    incomplete_retweets: int


def calculate_archive_integrity(posts) -> ArchiveIntegrity:
    posts = tuple(posts)
    incomplete_records = 0
    incomplete_top_level = 0
    incomplete_retweets = 0

    for post in posts:
        top_incomplete = post.content_state is ContentState.INCOMPLETE
        retweet_incomplete = (
            post.retweet is not None
            and post.retweet.content_state is ContentState.INCOMPLETE
        )
        if top_incomplete:
            incomplete_top_level += 1
        if retweet_incomplete:
            incomplete_retweets += 1
        if top_incomplete or retweet_incomplete:
            incomplete_records += 1

    total_posts = len(posts)
    return ArchiveIntegrity(
        total_posts=total_posts,
        complete_records=total_posts - incomplete_records,
        incomplete_records=incomplete_records,
        incomplete_top_level=incomplete_top_level,
        incomplete_retweets=incomplete_retweets,
    )


@dataclass(frozen=True)
class UserProfile:
    id: str
    screen_name: str
    description: str = ""
    followers_count: Optional[int] = None
    follow_count: Optional[int] = None
    statuses_count: Optional[int] = None
    location: str = ""
    verified: Optional[bool] = None
    verified_reason: str = ""
    registration_time: str = ""
    gender: str = ""
    birthday: str = ""
    company: str = ""
    education: str = ""
    ip_location: str = ""


class RangeMode(str, Enum):
    ALL = "all"
    RECENT = "recent"
    SINCE = "since"
    TRIAL = "trial"


@dataclass(frozen=True)
class FetchRange:
    mode: RangeMode = RangeMode.ALL
    limit: Optional[int] = None
    since: Optional[date] = None

    @classmethod
    def all(cls) -> "FetchRange":
        return cls(RangeMode.ALL)

    @classmethod
    def recent(cls, count: int) -> "FetchRange":
        return cls(RangeMode.RECENT, limit=count)

    @classmethod
    def trial(cls, count: int = 20) -> "FetchRange":
        return cls(RangeMode.TRIAL, limit=count)

    @classmethod
    def since_date(cls, value: date) -> "FetchRange":
        return cls(RangeMode.SINCE, since=value)

    def label(self) -> str:
        if self.mode is RangeMode.ALL:
            return "全量快照"
        if self.mode is RangeMode.TRIAL:
            return f"测试导出{self.limit or 20}条"
        if self.mode is RangeMode.RECENT:
            return f"最近{self.limit or 0}条"
        if self.mode is RangeMode.SINCE and self.since:
            return f"{self.since:%Y-%m-%d}起"
        return self.mode.value


class Termination(str, Enum):
    NATURAL = "natural"
    TARGET_COUNT = "target_count"
    SINCE_REACHED = "since_reached"


@dataclass(frozen=True)
class FetchReport:
    pages_fetched: int
    requests_made: int
    termination: Termination
    oldest_reached: Optional[datetime]
    newest_reached: Optional[datetime]
    profile_statuses_count: Optional[int]


@dataclass(frozen=True)
class Archive:
    profile: UserProfile
    posts: tuple[Post, ...]
    fetch_range: FetchRange
    report: FetchReport
    fetched_at: datetime = field(default_factory=datetime.now)

    @property
    def integrity(self) -> ArchiveIntegrity:
        return calculate_archive_integrity(self.posts)
