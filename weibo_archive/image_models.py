"""Image-only vocabulary. Remote locators belong to runtime candidates only."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .models import TimestampProvenance, Termination


class ImageRelation(str, Enum):
    TOP_LEVEL = "TOP_LEVEL"
    RETWEET_SOURCE = "RETWEET_SOURCE"


class ImageSubtype(str, Enum):
    STILL = "still"
    GIF = "gifvideos"
    LIVE_PHOTO_STILL = "livephoto"


class QualitySource(str, Enum):
    LARGE = "large"
    REGULAR = "regular"


class AssetStatus(str, Enum):
    PENDING = "pending"
    SAVED = "saved"
    ALREADY_PRESENT = "already_present"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class ImageResultState(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


def valid_identity(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9]{1,64}", value)) and int(value) > 0


@dataclass(frozen=True)
class DiscoveryIssue:
    reason: str
    slot_index: int | None = None


@dataclass(frozen=True)
class ImageAsset:
    slot_index: int
    pid: str | None
    subtype: ImageSubtype
    selected_url: str = field(repr=False)
    quality_source: QualitySource = QualitySource.LARGE

    def __post_init__(self):
        if type(self.slot_index) is not int or self.slot_index < 1:
            raise ValueError("invalid_slot")
        if self.pid is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.pid):
            raise ValueError("invalid_pid")
        if not isinstance(self.subtype, ImageSubtype) or not isinstance(self.quality_source, QualitySource):
            raise ValueError("invalid_asset_kind")
        if not isinstance(self.selected_url, str) or not self.selected_url:
            raise ValueError("missing_locator")


@dataclass(frozen=True)
class ImageRecord:
    source_post_id: str
    containing_post_id: str
    relation: ImageRelation
    created_at: datetime | None
    created_at_provenance: TimestampProvenance
    declared_count: int | None
    enumerated_slots: int
    enumeration_gap: int
    assets: tuple[ImageAsset, ...]
    issues: tuple[DiscoveryIssue, ...] = ()
    excluded_video_slots: int = 0

    def __post_init__(self):
        if not valid_identity(self.source_post_id) or not valid_identity(self.containing_post_id):
            raise ValueError("missing_or_invalid_identity")
        if not isinstance(self.relation, ImageRelation):
            raise ValueError("invalid_relation")
        if self.relation is ImageRelation.TOP_LEVEL and self.source_post_id != self.containing_post_id:
            raise ValueError("invalid_top_level_identity")
        if not isinstance(self.created_at_provenance, TimestampProvenance):
            raise ValueError("invalid_timestamp")
        if self.created_at_provenance is TimestampProvenance.UNKNOWN:
            if self.created_at is not None:
                raise ValueError("invalid_timestamp")
        elif not isinstance(self.created_at, datetime):
            raise ValueError("invalid_timestamp")
        elif (self.created_at.utcoffset() is not None) != (self.created_at_provenance is TimestampProvenance.SOURCE_OFFSET):
            raise ValueError("invalid_timestamp")
        if self.declared_count is not None and (type(self.declared_count) is not int or self.declared_count < 0):
            raise ValueError("invalid_count")
        if type(self.enumerated_slots) is not int or self.enumerated_slots < 0:
            raise ValueError("invalid_count")
        if self.enumeration_gap != max(0, (self.declared_count or 0) - self.enumerated_slots):
            raise ValueError("invalid_gap")
        if not isinstance(self.assets, tuple) or not isinstance(self.issues, tuple):
            raise ValueError("mutable_record")
        slots = [a.slot_index for a in self.assets]
        if len(set(slots)) != len(slots) or any(i > self.enumerated_slots for i in slots):
            raise ValueError("invalid_slot")


@dataclass(frozen=True)
class ImageTraversalReport:
    pages_fetched: int = 0
    posts_inspected: int = 0
    timeline_posts: int = 0
    termination: Termination | None = None
    frontier: str | None = None
    statuses_count: int | None = None  # Informational, never completeness evidence.


@dataclass(frozen=True)
class ImageBackupResult:
    state: ImageResultState
    posts_inspected: int
    discovered: int
    saved: int
    already_present: int
    unavailable: int
    failed: int
    pending: int
    enumeration_gap: int
    discovery_warnings: int
    failure_reason: str | None = None


class ImageError(RuntimeError):
    """Only controlled codes; never attach upstream URLs, bodies or exceptions."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)
