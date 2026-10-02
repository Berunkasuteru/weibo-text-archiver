"""Image schema 1, explicit URL-free projection and atomic checkpoints."""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from datetime import datetime
from pathlib import Path

from .image_downloader import MEDIA_EXTENSIONS, inspect_file
from .image_models import (
    AssetStatus, ImageError, ImageRelation, ImageResultState, ImageSubtype,
    QualitySource, valid_identity,
)
from .models import TimestampProvenance


SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
TOP_KEYS = {"schema_version", "target_identity", "requested_range", "started_at", "finished_at",
            "state", "failure_reason", "traversal", "summary", "records", "assets", "current_asset_keys"}
ASSET_KEYS = {"containing_post_id", "source_post_id", "relation", "slot_index", "pid", "subtype",
              "quality_source", "status", "relative_path", "media_type", "content_type",
              "byte_size", "sha256", "content_length", "failure_reason"}
RECORD_KEYS = {"containing_post_id", "source_post_id", "relation", "created_at", "created_at_provenance",
               "declared_count", "enumerated_slots", "enumeration_gap", "excluded_video_slots", "issues"}
ISSUE_CODES = {"invalid_pics", "invalid_declared_count", "invalid_slot", "unsupported_type", "missing_locator", "invalid_pid"}
FAILURE_CODES = {"invalid_config", "invalid_range", "invalid_target", "invalid_record", "invalid_retweet",
                 "missing_or_invalid_identity", "invalid_response", "ambiguous_empty_page", "challenge",
                 "authentication_expired", "rate_limited", "no_progress", "invalid_url", "non_https",
                 "unexpected_host", "unexpected_redirect", "tls_error", "timeout", "network_error",
                 "invalid_content_type", "invalid_content_encoding", "signature_mismatch", "too_large",
                 "length_mismatch", "storage_error", "insufficient_disk_space", "invalid_manifest",
                 "target_mismatch", "unsafe_path", "output_locked", "discovery_incomplete"}


def safe_reason(value):
    return value if value in FAILURE_CODES or isinstance(value, str) and re.fullmatch(r"http_[1-5][0-9]{2}", value) else "storage_error"


def now_iso():
    return datetime.now().astimezone().isoformat()


def range_payload(value):
    return {"mode": value.mode.value, "limit": value.limit,
            "since": value.since.isoformat() if value.since else None}


def asset_key(entry):
    return ":".join(str(entry[k]) for k in ("relation", "containing_post_id", "source_post_id",
                                          "slot_index", "pid", "subtype", "quality_source"))


def filename_stem(entry):
    stem = entry["source_post_id"]
    if entry["relation"] == ImageRelation.RETWEET_SOURCE.value:
        stem = entry["containing_post_id"] + "_RT_" + stem
    return f"{stem}_{entry['slot_index']:02d}"


def _no_links(path):
    """Reject existing symlink/junction/reparse components, including output root."""
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ImageError("unsafe_path")


class ManifestStore:
    """One writer per root, including across processes; OS lock releases on crash.

    The empty .image-backup.lock file is deliberately retained. Deleting an advisory
    lock file while another process is waiting can create two independent locks.
    """
    def __init__(self, root, target_identity: str):
        if not valid_identity(target_identity):
            raise ImageError("invalid_target")
        self.root = Path(root).absolute()
        self.target_identity = target_identity
        self.path = self.root / "manifest.json"
        self.data = None
        self._lock = None

    def __enter__(self):
        reason = None
        locking = False
        try:
            _no_links(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            _no_links(self.path)
            lock_path = self.root / ".image-backup.lock"
            _no_links(lock_path)
            self._lock = lock_path.open("a+b")
            locking = True
            if os.name == "nt":
                import msvcrt
                self._lock.seek(0, 2)
                if self._lock.tell() == 0:
                    self._lock.write(b"\0")
                    self._lock.flush()
                self._lock.seek(0)
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locking = False
            self.data = self._load()
            return self
        except ImageError as exc:
            reason = exc.reason
        except OSError:
            reason = "output_locked" if locking else "storage_error"
        if self._lock is not None:
            self._lock.close()
            self._lock = None
        raise ImageError(reason)

    def __exit__(self, *args):
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def local_path(self, relative: str) -> Path:
        if not isinstance(relative, str) or not re.fullmatch(
                r"(?:[0-9]{4}|unknown-date)/[0-9]+(?:_RT_[0-9]+)?_[0-9]+(?:_[0-9]+)?\.(?:jpg|png|gif|webp)", relative):
            raise ImageError("unsafe_path")
        path = self.root / relative
        _no_links(path)
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ImageError("unsafe_path")
        return path

    def _load(self):
        if not self.path.exists():
            return {"schema_version": 1, "target_identity": self.target_identity, "assets": {}}
        reason = None
        try:
            if self.path.stat().st_size > MAX_MANIFEST_BYTES:
                raise ValueError()
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._validate(data)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            reason = "invalid_manifest"
        if reason:
            raise ImageError(reason)
        if data["target_identity"] != self.target_identity:
            raise ImageError("target_mismatch")
        return data

    def _validate(self, data):
        # Strict shape, not a raw JSON passthrough: unknown fields never round-trip.
        if not isinstance(data, dict) or set(data) != TOP_KEYS or type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError()
        if not valid_identity(data["target_identity"]):
            raise ValueError()
        if data["state"] not in {"RUNNING", *(x.value for x in ImageResultState)}:
            raise ValueError()
        for key in ("started_at", "finished_at"):
            if data[key] is not None:
                datetime.fromisoformat(data[key])
        if data["failure_reason"] is not None and safe_reason(data["failure_reason"]) != data["failure_reason"]:
            raise ValueError()
        r = data["requested_range"]
        if set(r) != {"mode", "limit", "since"} or r["mode"] not in {"all", "trial", "recent", "since"}:
            raise ValueError()
        if r["limit"] is not None and (type(r["limit"]) is not int or r["limit"] < 1):
            raise ValueError()
        if r["since"] is not None:
            datetime.strptime(r["since"], "%Y-%m-%d")
        traversal = data["traversal"]
        if set(traversal) != {"pages_fetched", "posts_inspected", "timeline_posts", "termination", "frontier", "statuses_count"}:
            raise ValueError()
        if traversal["termination"] not in (None, "natural", "target_count", "since_reached"):
            raise ValueError()
        for key in ("pages_fetched", "posts_inspected", "timeline_posts", "statuses_count"):
            if traversal[key] is not None and (type(traversal[key]) is not int or traversal[key] < 0):
                raise ValueError()
        if traversal["frontier"] is not None:
            datetime.strptime(traversal["frontier"], "%Y-%m-%d")
        if set(data["summary"]) != {"declared_total_known", "declared_unknown_records", "enumerated_slots", "enumeration_gap", "discovery_warnings", "excluded_video_slots"}:
            raise ValueError()
        if any(type(v) is not int or v < 0 for v in data["summary"].values()):
            raise ValueError()
        for rec in data["records"]:
            if set(rec) != RECORD_KEYS or not valid_identity(rec["containing_post_id"]) or not valid_identity(rec["source_post_id"]):
                raise ValueError()
            ImageRelation(rec["relation"])
            TimestampProvenance(rec["created_at_provenance"])
            if rec["created_at"] is not None:
                datetime.fromisoformat(rec["created_at"])
            for name in ("declared_count", "enumerated_slots", "enumeration_gap", "excluded_video_slots"):
                if rec[name] is not None and (type(rec[name]) is not int or rec[name] < 0):
                    raise ValueError()
            for issue in rec["issues"]:
                if set(issue) != {"reason", "slot_index"} or issue["reason"] not in ISSUE_CODES:
                    raise ValueError()
                if issue["slot_index"] is not None and (type(issue["slot_index"]) is not int or issue["slot_index"] < 1):
                    raise ValueError()
        paths = set()
        for key, entry in data["assets"].items():
            if set(entry) != ASSET_KEYS or key != asset_key(entry):
                raise ValueError()
            if not valid_identity(entry["source_post_id"]) or not valid_identity(entry["containing_post_id"]):
                raise ValueError()
            ImageRelation(entry["relation"])
            if entry["relation"] == "TOP_LEVEL" and entry["source_post_id"] != entry["containing_post_id"]:
                raise ValueError()
            ImageSubtype(entry["subtype"])
            QualitySource(entry["quality_source"])
            AssetStatus(entry["status"])
            if type(entry["slot_index"]) is not int or entry["slot_index"] < 1:
                raise ValueError()
            if entry["pid"] is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", entry["pid"]):
                raise ValueError()
            if entry["failure_reason"] is not None and safe_reason(entry["failure_reason"]) != entry["failure_reason"]:
                raise ValueError()
            for name in ("byte_size", "content_length"):
                if entry[name] is not None and (type(entry[name]) is not int or entry[name] < 0):
                    raise ValueError()
            if entry["sha256"] is not None and not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                raise ValueError()
            if entry["media_type"] not in (None, *MEDIA_EXTENSIONS) or entry["content_type"] not in (None, *MEDIA_EXTENSIONS):
                raise ValueError()
            relative = entry["relative_path"]
            if relative is not None:
                path = self.local_path(relative)
                if not re.fullmatch(re.escape(filename_stem(entry)) + r"(?:_[0-9]+)?\.(?:jpg|png|gif|webp)", path.name):
                    raise ValueError()
                if relative in paths:
                    raise ValueError()
                paths.add(relative)
        keys = data["current_asset_keys"]
        if not isinstance(keys, list) or len(keys) != len(set(keys)) or any(k not in data["assets"] for k in keys):
            raise ValueError()

    def begin(self, fetch_range, report):
        self.data.update(requested_range=range_payload(fetch_range), started_at=now_iso(), finished_at=None,
                         state="RUNNING", failure_reason=None, records=[], current_asset_keys=[],
                         summary={"declared_total_known": 0, "declared_unknown_records": 0,
                                  "enumerated_slots": 0, "enumeration_gap": 0, "discovery_warnings": 0,
                                  "excluded_video_slots": 0})
        self.set_report(report)
        self.write()

    def set_report(self, report):
        self.data["traversal"] = {"pages_fetched": report.pages_fetched, "posts_inspected": report.posts_inspected,
                                 "timeline_posts": report.timeline_posts,
                                 "termination": report.termination.value if report.termination else None,
                                 "frontier": report.frontier, "statuses_count": report.statuses_count}

    def discover(self, record):
        self.data["records"].append({"containing_post_id": record.containing_post_id, "source_post_id": record.source_post_id,
            "relation": record.relation.value, "created_at": record.created_at.isoformat() if record.created_at else None,
            "created_at_provenance": record.created_at_provenance.value, "declared_count": record.declared_count,
            "enumerated_slots": record.enumerated_slots, "enumeration_gap": record.enumeration_gap,
            "excluded_video_slots": record.excluded_video_slots,
            "issues": [{"reason": i.reason, "slot_index": i.slot_index} for i in record.issues]})
        summary = self.data["summary"]
        summary["declared_total_known"] += record.declared_count or 0
        summary["declared_unknown_records"] += record.declared_count is None
        summary["enumerated_slots"] += record.enumerated_slots
        summary["enumeration_gap"] += record.enumeration_gap
        summary["discovery_warnings"] += len(record.issues)
        summary["excluded_video_slots"] += record.excluded_video_slots
        keys = []
        for asset in record.assets:
            entry = {"containing_post_id": record.containing_post_id, "source_post_id": record.source_post_id,
                     "relation": record.relation.value, "slot_index": asset.slot_index, "pid": asset.pid,
                     "subtype": asset.subtype.value, "quality_source": asset.quality_source.value,
                     "status": "pending", "relative_path": None, "media_type": None, "content_type": None,
                     "byte_size": None, "sha256": None, "content_length": None, "failure_reason": None}
            key = asset_key(entry)
            if key in self.data["current_asset_keys"]:
                raise ImageError("invalid_record")
            self.data["assets"].setdefault(key, entry)
            # Prior success is not a success of this run until local verification.
            self.data["assets"][key].update(status="pending", failure_reason=None)
            self.data["current_asset_keys"].append(key)
            keys.append(key)
        self.write()
        return keys

    def verified(self, key, check):
        entry = self.data["assets"][key]
        if entry["status"] not in ("saved", "already_present", "pending"):
            return False
        # Pending recovery requires the durable pre-commit digest, not just a name.
        if not entry["relative_path"] or not entry["sha256"] or not entry["byte_size"] or not entry["media_type"]:
            return False
        path = self.local_path(entry["relative_path"])
        try:
            facts = inspect_file(path, check=check)
        except (OSError, ImageError):
            return False
        if (facts.byte_size, facts.sha256, facts.media_type) != (entry["byte_size"], entry["sha256"], entry["media_type"]):
            return False
        entry.update(status="already_present", failure_reason=None)
        self.write()
        return True

    def allocate(self, key, record, media):
        entry = self.data["assets"][key]
        folder = (f"{record.created_at.year:04d}" if record.created_at is not None and record.created_at_provenance in
                  (TimestampProvenance.SOURCE_OFFSET, TimestampProvenance.SOURCE_WALL) else "unknown-date")
        stem = filename_stem(entry)
        occupied = {v["relative_path"] for k, v in self.data["assets"].items() if k != key}
        number = 1
        while True:
            suffix = "" if number == 1 else f"_{number}"
            relative = f"{folder}/{stem}{suffix}{MEDIA_EXTENSIONS[media]}"
            path = self.local_path(relative)
            if not path.exists() and relative not in occupied:
                break
            number += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        _no_links(path)
        entry.update(status="pending", relative_path=relative, media_type=media, content_type=None,
                     byte_size=None, sha256=None, content_length=None, failure_reason=None)
        self.write()
        return path

    def prepared(self, key, path, facts):
        entry = self.data["assets"][key]
        if self.local_path(entry["relative_path"]) != path:
            raise ImageError("unsafe_path")
        entry.update(status="pending", media_type=facts.media_type, content_type=facts.content_type,
                     byte_size=facts.byte_size, sha256=facts.sha256, content_length=facts.content_length)
        self.write()

    def terminal(self, key, status, reason=None):
        entry = self.data["assets"][key]
        if status == "saved" and not self.local_path(entry["relative_path"]).is_file():
            raise ImageError("storage_error")
        entry.update(status=status, failure_reason=safe_reason(reason) if reason else None)
        self.write()

    def write(self):
        self._validate(self.data)
        _no_links(self.path)
        fd, name = tempfile.mkstemp(prefix=".manifest-", suffix=".tmp", dir=self.root)
        temp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                if handle.tell() > MAX_MANIFEST_BYTES:
                    raise ImageError("storage_error")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)
