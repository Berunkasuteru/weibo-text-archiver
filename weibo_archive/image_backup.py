"""Non-UI orchestration. No text archive, GUI, credential writes or live defaults."""
from __future__ import annotations

import threading
from collections import Counter

from .image_client import validate_range
from .image_downloader import ImageDownloader
from .image_manifest import ManifestStore, now_iso, safe_reason
from .image_models import ImageBackupResult, ImageError, ImageResultState, ImageTraversalReport
from .network import AuthenticationExpired, Cancelled, ChallengeRequired, NetworkError, RateLimited


def _reason(exc):
    if isinstance(exc, ImageError):
        return safe_reason(exc.reason)
    if isinstance(exc, AuthenticationExpired):
        return "authentication_expired"
    if isinstance(exc, ChallengeRequired):
        return "challenge"
    if isinstance(exc, RateLimited):
        return "rate_limited"
    if isinstance(exc, NetworkError):
        return "network_error"
    return "storage_error"


def _result(data, report, *, reason=None, cancelled=False):
    counts = Counter(data["assets"][key]["status"] for key in data["current_asset_keys"])
    summary = data["summary"]
    warnings = summary["discovery_warnings"]
    gap = summary["enumeration_gap"]
    useful = counts["saved"] + counts["already_present"]
    if cancelled:
        state = ImageResultState.CANCELLED
    elif not reason and report.termination is not None and not (gap or warnings or counts["pending"] or counts["failed"] or counts["unavailable"]):
        state = ImageResultState.COMPLETE
    elif useful:
        state = ImageResultState.PARTIAL
    else:
        state = ImageResultState.FAILED
    return ImageBackupResult(state, report.posts_inspected, sum(counts.values()), counts["saved"],
                             counts["already_present"], counts["unavailable"], counts["failed"], counts["pending"],
                             gap, warnings, reason)


class ImageBackup:
    """One instance/run at a time. Output roots also carry an OS process lock."""
    def __init__(self, output_root, *, cancel_event=None, downloader=None):
        self.output_root = output_root
        self.cancel_event = cancel_event or threading.Event()
        self.downloader = downloader or ImageDownloader(cancel_event=self.cancel_event)
        # A supplied downloader must observe the same task cancellation signal.
        self.downloader.cancel_event = self.cancel_event

    def run(self, target_identity, fetch_range, *, client):
        client.cancel_event = self.cancel_event
        if hasattr(client, "http"):
            client.http.cancel_event = self.cancel_event
        return self._run(target_identity, fetch_range, client.iter_posts(target_identity, fetch_range),
                         lambda: client.report)

    def run_records(self, target_identity, fetch_range, records, *, report: ImageTraversalReport):
        """Process already-discovered runtime records (e.g. a bounded smoke).

        No network discovery is performed. Caller supplies traversal evidence;
        use termination=None for an intentionally truncated sample, never invent
        a natural/target termination. Reuse the same runtime records for resume.
        """
        return self._run(target_identity, fetch_range, ((r,) for r in records), lambda: report)

    def _run(self, target, fetch_range, groups, report_getter):
        fallback = ImageTraversalReport()
        reason = None
        cancelled = False
        result = None
        try:
            validate_range(fetch_range)
            with ManifestStore(self.output_root, target) as store:
                store.begin(fetch_range, report_getter())
                try:
                    for group in groups:
                        self.downloader.check_cancelled()
                        store.set_report(report_getter())
                        work = [(record, store.discover(record)) for record in group]
                        for record, keys in work:
                            for asset, key in zip(record.assets, keys):
                                self.downloader.check_cancelled()
                                if store.verified(key, self.downloader.check_cancelled):
                                    continue
                                store.data["assets"][key]["status"] = "pending"
                                store.write()
                                failure = None
                                try:
                                    self.downloader.download(asset.selected_url,
                                        allocate=lambda media: store.allocate(key, record, media),
                                        prepared=lambda path, facts: store.prepared(key, path, facts))
                                except ImageError as exc:
                                    failure = safe_reason(exc.reason)
                                if failure:
                                    status = "unavailable" if failure in ("http_404", "http_410") else "failed"
                                    store.terminal(key, status, failure)
                                    if failure in {"http_401", "http_403", "http_429", "http_432",
                                                   "insufficient_disk_space", "storage_error"}:
                                        raise ImageError(failure)
                                else:
                                    # The downloader has already fsynced and committed
                                    # the file. Even if checkpoint persistence fails,
                                    # this is useful preserved output, hence PARTIAL.
                                    commit_error = False
                                    try:
                                        store.terminal(key, "saved")
                                    except (OSError, ImageError):
                                        store.data["assets"][key]["status"] = "saved"
                                        commit_error = True
                                    if commit_error:
                                        raise ImageError("storage_error")
                    self.downloader.check_cancelled()
                    if report_getter().termination is None:
                        reason = "discovery_incomplete"
                except Cancelled:
                    cancelled = True
                except (ImageError, NetworkError, OSError, ValueError) as exc:
                    reason = _reason(exc)
                report = report_getter()
                store.set_report(report)
                result = _result(store.data, report, reason=reason, cancelled=cancelled)
                store.data.update(state=result.state.value, finished_at=now_iso(), failure_reason=reason)
                try:
                    store.write()
                except (OSError, ImageError, ValueError):
                    # The preceding valid checkpoint remains readable; report the
                    # failure separately, rather than claiming durable completion.
                    result = _result(store.data, report, reason="storage_error", cancelled=cancelled)
                return result
        except Cancelled:
            cancelled = True
        except (ImageError, OSError, ValueError) as exc:
            reason = _reason(exc)
        return ImageBackupResult(ImageResultState.CANCELLED if cancelled else ImageResultState.FAILED,
                                 fallback.posts_inspected, 0, 0, 0, 0, 0, 0, 0, 0, reason)
