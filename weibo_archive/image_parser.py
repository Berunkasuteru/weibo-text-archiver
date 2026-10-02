"""Deterministic image discovery; intentionally independent of text hydration."""
from __future__ import annotations

import re

from .image_models import (
    DiscoveryIssue, ImageAsset, ImageError, ImageRecord, ImageRelation,
    ImageSubtype, QualitySource, valid_identity,
)
from .parser import parse_created_at_fact


def post_identity(raw: dict) -> str:
    for key in ("id", "mid"):
        value = raw.get(key)
        if type(value) not in (int, str):
            continue
        if valid_identity(str(value)):
            return str(value)
    raise ImageError("missing_or_invalid_identity")


def _count(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
        return int(value)
    return None


def _node(raw: dict, containing: str, relation: ImageRelation) -> ImageRecord:
    source = post_identity(raw)
    issues = []
    pics = raw.get("pics", [])
    if not isinstance(pics, list):
        issues.append(DiscoveryIssue("invalid_pics"))
        pics = []
    declared = _count(raw.get("pic_num"))
    if "pic_num" in raw and declared is None:
        issues.append(DiscoveryIssue("invalid_declared_count"))
    assets = []
    excluded = 0
    for index, pic in enumerate(pics, 1):
        if not isinstance(pic, dict):
            issues.append(DiscoveryIssue("invalid_slot", index))
            continue
        kind = pic.get("type")
        kind = kind.lower() if isinstance(kind, str) else "" if kind is None else "unknown"
        if kind == "video":
            excluded += 1
            continue
        subtype = {"": ImageSubtype.STILL, "pic": ImageSubtype.STILL,
                   "gifvideos": ImageSubtype.GIF, "livephoto": ImageSubtype.LIVE_PHOTO_STILL}.get(kind)
        if subtype is None:
            issues.append(DiscoveryIssue("unsupported_type", index))
            continue
        large = pic.get("large")
        url = large.get("url") if isinstance(large, dict) else None
        quality = QualitySource.LARGE
        if not isinstance(url, str) or not url.strip():
            url, quality = pic.get("url"), QualitySource.REGULAR
        if not isinstance(url, str) or not url.strip():
            issues.append(DiscoveryIssue("missing_locator", index))
            continue
        pid = pic.get("pid")
        if pid is not None and (not isinstance(pid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", pid)):
            issues.append(DiscoveryIssue("invalid_pid", index))
            pid = None
        # Keep the exact locator. Scheme/host validation belongs to the transport.
        assets.append(ImageAsset(index, pid, subtype, url, quality))
    when, provenance = parse_created_at_fact(raw.get("created_at"))
    return ImageRecord(source, containing, relation, when, provenance, declared,
                       len(pics), max(0, (declared or 0) - len(pics)), tuple(assets),
                       tuple(issues), excluded)


def parse_image_records(raw: dict) -> tuple[ImageRecord, ...]:
    if not isinstance(raw, dict):
        raise ImageError("invalid_record")
    containing = post_identity(raw)
    records = [_node(raw, containing, ImageRelation.TOP_LEVEL)]
    rt = raw.get("retweeted_status")
    if rt is not None:
        if not isinstance(rt, dict):
            raise ImageError("invalid_retweet")
        records.append(_node(rt, containing, ImageRelation.RETWEET_SOURCE))
    return tuple(records)
