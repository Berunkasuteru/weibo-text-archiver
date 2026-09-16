"""Offline image-core contracts. Run: python -B tests/test_image_core.py"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import ssl
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
from dataclasses import FrozenInstanceError, replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weibo_archive.image_backup import ImageBackup
from weibo_archive.image_client import ImageTimelineClient
from weibo_archive.image_downloader import (
    ImageDownloader, FileFacts, detect_type, inspect_file, validate_image_url,
    MAX_IMAGE_BYTES, SOCKET_TIMEOUT, IMAGE_DEADLINE, CDN_START_SPACING,
)
from weibo_archive.image_manifest import ManifestStore
from weibo_archive.image_models import (
    ImageError, ImageRelation, ImageResultState, ImageSubtype, ImageTraversalReport,
)
from weibo_archive.image_parser import parse_image_records
from weibo_archive.models import FetchRange, Termination, TimestampProvenance
from weibo_archive.network import Cancelled, USER_AGENT

URL = "https://wx1.sinaimg.cn/mw2000/synthetic.jpg?token=DO_NOT_PERSIST"
REGULAR = "https://wx1.sinaimg.cn/orj360/synthetic.jpg"
JPEG = b"\xff\xd8\xff" + b"synthetic-data" * 8
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-png"
GIF = b"GIF89a" + b"synthetic-gif"
WEBP = b"RIFF" + (12).to_bytes(4, "little") + b"WEBP" + b"VP8 synthetic"[:8]
REPORT = ImageTraversalReport(pages_fetched=1, posts_inspected=1, timeline_posts=1,
                              termination=Termination.TARGET_COUNT)


def pic(**kw):
    result = {"pid": "p1", "url": REGULAR, "large": {"url": URL}}
    result.update(kw)
    return result


def raw(identity=101, pics=None, **kw):
    result = {"id": str(identity), "created_at": "2026-01-02 03:04:05", "user": {"id": "42"},
              "text": "PRIVATE_POST_TEXT", "pics": [pic()] if pics is None else pics}
    result.update(kw)
    return result


def record(**kw):
    return parse_image_records(raw(**kw))[0]


class Response:
    def __init__(self, body=JPEG, mime="image/jpeg", *, length="auto", status=200, url=URL, on_read=None):
        self.body = io.BytesIO(body)
        self.headers = {}
        if mime is not None:
            self.headers["Content-Type"] = mime
        if length == "auto":
            length = len(body)
        if length is not None:
            self.headers["Content-Length"] = str(length)
        self.status, self.url, self.on_read = status, url, on_read
        self.closed = False
        self.read_calls = 0

    def geturl(self):
        return self.url

    def read(self, n):
        assert n > 0, "No unbounded reads"
        self.read_calls += 1
        if self.on_read:
            self.on_read(self.read_calls)
        return self.body.read(n)

    read1 = read

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        response.url = request.full_url if response.url == URL else response.url
        return response


class Pages(ImageTimelineClient):
    def __init__(self, pages, cancel=None):
        super().__init__(http=SimpleNamespace(), cancel_event=cancel)
        self.pages = pages
        self.calls = []

    def timeline_page(self, uid, page):
        self.calls.append(page)
        if page > 20:
            raise AssertionError("unbounded pagination")
        rows = self.pages[min(page - 1, len(self.pages) - 1)]
        if isinstance(rows, Exception):
            raise rows
        return rows, rows == []


class JsonHTTP:
    def __init__(self, data):
        self.data = data
        self.calls = []
    def wait(self, seconds):
        pass
    def json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.data


class ParserTests(unittest.TestCase):
    def test_01_normal(self):
        r = record()
        self.assertEqual(r.assets[0].slot_index, 1)
        self.assertEqual(r.relation, ImageRelation.TOP_LEVEL)
        with self.assertRaises(FrozenInstanceError):
            r.source_post_id = "2"

    def test_02_multi_order(self):
        r = record(pics=[pic(pid="a"), pic(pid="b")])
        self.assertEqual([(a.slot_index, a.pid) for a in r.assets], [(1, "a"), (2, "b")])

    def test_03_nested_ownership(self):
        a, b = parse_image_records(raw(pics=[], retweeted_status=raw(202)))
        self.assertEqual((b.source_post_id, b.containing_post_id, b.relation), ("202", "101", ImageRelation.RETWEET_SOURCE))

    def test_04_top_and_rt_separate(self):
        source = raw(retweeted_status=raw(202))
        snapshot = copy.deepcopy(source)
        records = parse_image_records(source)
        self.assertEqual(len(records), 2)
        self.assertEqual(source, snapshot)
        self.assertNotEqual(records[0].source_post_id, records[1].source_post_id)

    def test_05_gif(self):
        self.assertEqual(record(pics=[pic(type="gifvideos", videoSrc="secret-video")]).assets[0].subtype, ImageSubtype.GIF)

    def test_06_livephoto(self):
        a = record(pics=[pic(type="livephoto", videoSrc="secret-video")]).assets[0]
        self.assertEqual(a.subtype, ImageSubtype.LIVE_PHOTO_STILL)
        self.assertNotIn("secret-video", repr(a))

    def test_07_video_excluded(self):
        r = record(pics=[pic(type="video"), pic()])
        self.assertEqual(r.excluded_video_slots, 1)
        self.assertEqual(r.assets[0].slot_index, 2)

    def test_08_covers_avatars_excluded(self):
        self.assertFalse(record(pics=[], page_info={"page_pic": {"url": URL}}, profile_image_url=URL).assets)

    def test_09_large_preferred(self):
        self.assertEqual(record().assets[0].selected_url, URL)

    def test_10_regular_fallback(self):
        a = record(pics=[pic(large={})]).assets[0]
        self.assertEqual((a.selected_url, a.quality_source.value), (REGULAR, "regular"))

    def test_11_gap(self):
        r = record(pic_num="12")
        self.assertEqual((r.declared_count, r.enumerated_slots, r.enumeration_gap), (12, 1, 11))

    def test_12_missing_id(self):
        for data in ({"pics": [pic()]}, raw(id="../secret"), raw(retweeted_status={"pics": [pic()]})):
            with self.assertRaises(ImageError):
                parse_image_records(data)

    def test_unknown_and_malformed_diagnostics(self):
        r = record(pics=[pic(type="future"), None, {"pid": "x"}], pic_num=True)
        self.assertFalse(r.assets)
        self.assertEqual({i.reason for i in r.issues}, {"unsupported_type", "invalid_slot", "missing_locator", "invalid_declared_count"})

    def test_mid_and_timestamp(self):
        r = parse_image_records({"mid": "123", "created_at": "刚刚", "pics": []})[0]
        self.assertEqual(r.created_at_provenance, TimestampProvenance.RELATIVE_UNVERIFIED)
        self.assertEqual(r.source_post_id, "123")


class RangeTests(unittest.TestCase):
    def collect(self, client, value):
        return list(client.iter_posts("42", value))

    def test_13_recent(self):
        c = Pages([[raw(i) for i in range(1, 6)]])
        self.assertEqual(len(self.collect(c, FetchRange.recent(2))), 2)
        self.assertEqual(c.report.termination, Termination.TARGET_COUNT)
        self.assertEqual(c.calls, [1])

    def test_14_trial(self):
        c = Pages([[raw(i) for i in range(1, 31)]])
        self.assertEqual(len(self.collect(c, FetchRange.trial())), 20)

    def test_15_pinned_not_counted_or_frontier(self):
        c = Pages([[raw(1, mblogtype=2, created_at="2001-01-01"), raw(2), raw(3)]])
        self.assertEqual(len(self.collect(c, FetchRange.recent(2))), 3)
        self.assertEqual(c.report.timeline_posts, 2)
        self.assertEqual(c.report.frontier, "2026-01-02")

    def test_16_since_two_old_pages(self):
        c = Pages([[raw(1)], [raw(2, created_at="2024-01-01")], [raw(3, created_at="2023-01-01")]])
        self.assertEqual(len(self.collect(c, FetchRange.since_date(date(2025, 1, 1)))), 1)
        self.assertEqual(c.report.termination, Termination.SINCE_REACHED)
        self.assertEqual(c.calls, [1, 2, 3])

    def test_17_relative_breaks_old_run(self):
        c = Pages([[raw(1, created_at="2024-01-01")], [raw(2, created_at="刚刚")],
                   [raw(3, created_at="2023-01-01")], []])
        self.assertEqual(len(self.collect(c, FetchRange.since_date(date(2025, 1, 1)))), 1)
        self.assertEqual(c.report.termination, Termination.NATURAL)

    def test_18_ambiguous_empty(self):
        c = ImageTimelineClient(http=JsonHTTP({"ok": 1, "data": {"cards": [{"card_type": 999}]}}))
        with self.assertRaises(ImageError):
            c.timeline_page("42", 1)

    def test_19_clean_empty(self):
        for data in ({"ok": 1, "data": {"cards": []}}, {"ok": 0, "msg": "暂无微博"}):
            c = ImageTimelineClient(http=JsonHTTP(data))
            self.assertEqual(c.timeline_page("42", 1), ([], True))

    def test_20_duplicates_and_no_progress(self):
        c = Pages([[raw(1), raw(1)]])
        it = c.iter_posts("42", FetchRange.all())
        next(it)
        with self.assertRaisesRegex(ImageError, "no_progress"):
            list(it)
        self.assertEqual(c.report.posts_inspected, 1)
        self.assertEqual(c.calls, [1, 2, 3, 4])

    def test_unknown_ok_zero(self):
        for data in ({"ok": 0}, {"ok": 0, "data": {"cards": []}}):
            with self.assertRaises(ImageError):
                ImageTimelineClient(http=JsonHTTP(data)).timeline_page("42", 1)

    def test_repeated_old_page_not_since_proof(self):
        c = Pages([[raw(1, created_at="2024-01-01")]])
        with self.assertRaisesRegex(ImageError, "no_progress"):
            self.collect(c, FetchRange.since_date(date(2025, 1, 1)))

    def test_foreign_account_ignored(self):
        c = Pages([[raw(1, user={"id": "99"}), raw(2)], []])
        self.assertEqual(len(self.collect(c, FetchRange.all())), 1)

    def test_missing_id_not_natural_end(self):
        c = ImageTimelineClient(http=JsonHTTP({"ok": 1, "data": {"cards": [{"mblog": {}}]}}))
        with self.assertRaises(ImageError):
            c.timeline_page("42", 1)

    def test_no_hydration_endpoint(self):
        http = JsonHTTP({"ok": 1, "data": {"cards": [{"mblog": raw(isLongText=True)}]}})
        c = ImageTimelineClient(http=http)
        self.collect(c, FetchRange.recent(1))
        self.assertEqual(len(http.calls), 1)
        self.assertTrue(http.calls[0][0].endswith("/api/container/getIndex"))


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def download(self, response=None, *, url=URL, **kw):
        self.opener = Opener(response or Response())
        self.downloader = ImageDownloader(opener=self.opener, spacing=0, reserve=0, **kw)
        def allocate(media):
            from weibo_archive.image_downloader import MEDIA_EXTENSIONS
            return self.root / ("file" + MEDIA_EXTENSIONS[media])
        return self.downloader.download(url, allocate=allocate, prepared=lambda p, f: None)

    def test_21_non_https(self):
        with self.assertRaisesRegex(ImageError, "non_https"):
            self.download(url="http://wx1.sinaimg.cn/a")
        self.assertFalse(self.opener.requests)

    def test_22_host_and_userinfo_ports(self):
        for url in ("https://evil.test/x", "https://wx1.sinaimg.cn.evil.test/x", "https://user@wx1.sinaimg.cn/x",
                    "https://wx1.sinaimg.cn:8443/x", "https://wx1.sinaimg.cn/x\n", "https://wx1.sinaimg.cn/x#fragment"):
            with self.assertRaises(ImageError):
                validate_image_url(url)

    def test_23_no_credentials(self):
        self.download()
        headers = {k.lower(): v for k, v in self.opener.requests[0][0].header_items()}
        self.assertNotIn("cookie", headers)
        self.assertNotIn("authorization", headers)
        self.assertNotIn("range", headers)

    def test_24_ua_referer(self):
        self.download()
        req = self.opener.requests[0][0]
        self.assertEqual(req.get_header("Referer"), "https://m.weibo.cn/")
        self.assertEqual(req.get_header("User-agent"), USER_AGENT)
        self.assertEqual(req.method, "GET")

    def test_25_redirect(self):
        with self.assertRaisesRegex(ImageError, "unexpected_redirect"):
            self.download(Response(status=302))

    def test_26_declared_size(self):
        response = Response(length=MAX_IMAGE_BYTES + 1)
        with self.assertRaisesRegex(ImageError, "too_large"):
            self.download(response)
        self.assertEqual(response.read_calls, 0)

    def test_27_actual_size(self):
        with self.assertRaisesRegex(ImageError, "too_large"):
            self.download(Response(length=None), max_bytes=20)
        self.assertFalse(list(self.root.iterdir()))

    def test_28_html(self):
        with self.assertRaisesRegex(ImageError, "invalid_content_type"):
            self.download(Response(b"<html>login</html>", "text/html"))

    def test_29_jpeg(self):
        path, facts = self.download()
        self.assertEqual(path.suffix, ".jpg")
        self.assertEqual(facts.sha256, hashlib.sha256(JPEG).hexdigest())

    def test_30_png(self):
        path, facts = self.download(Response(PNG, "image/png"))
        self.assertEqual(path.suffix, ".png")

    def test_31_gif(self):
        path, facts = self.download(Response(GIF, "image/gif"))
        self.assertEqual(path.suffix, ".gif")

    def test_32_webp(self):
        path, facts = self.download(Response(WEBP, "image/webp"))
        self.assertEqual(path.suffix, ".webp")

    def test_33_mismatch(self):
        with self.assertRaisesRegex(ImageError, "signature_mismatch"):
            self.download(Response(PNG, "image/jpeg"))

    def test_34_empty(self):
        with self.assertRaises(ImageError):
            self.download(Response(b""))

    def test_35_length_mismatch(self):
        with self.assertRaisesRegex(ImageError, "length_mismatch"):
            self.download(Response(length=len(JPEG) + 10))

    def test_36_network_sanitized(self):
        for error, reason in ((urllib.error.URLError("SECRET_URL"), "network_error"),
                              (TimeoutError("SECRET_URL"), "timeout"), (ssl.SSLError("SECRET_URL"), "tls_error")):
            with self.assertRaises(ImageError) as cm:
                self.download(error)
            self.assertEqual(str(cm.exception), reason)
            self.assertIsNone(cm.exception.__context__)
            self.assertIsNone(cm.exception.__cause__)

    def test_37_temp_removed_on_failure(self):
        with self.assertRaises(ImageError):
            self.download(Response(length=999))
        self.assertFalse(list(self.root.iterdir()))

    def test_38_temp_removed_cancel(self):
        event = threading.Event()
        with self.assertRaises(Cancelled):
            self.download(Response(on_read=lambda n: event.set() if n == 2 else None), cancel_event=event)
        self.assertFalse(list(self.root.iterdir()))

    def test_39_fsync_replace_and_pending_order(self):
        events = []
        real_replace = os.replace
        def commit(src, dst):
            events.append("replace")
            return real_replace(src, dst)
        dl = ImageDownloader(opener=Opener(Response()), spacing=0, reserve=0)
        with patch("weibo_archive.image_downloader.os.fsync", side_effect=lambda fd: events.append("fsync")), patch("weibo_archive.image_downloader.os.replace", side_effect=commit):
            dl.download(URL, allocate=lambda m: self.root / "a.jpg",
                        prepared=lambda p, f: events.append("prepared"))
        self.assertEqual(events, ["fsync", "prepared", "replace"])

    def test_race_does_not_overwrite(self):
        dl = ImageDownloader(opener=Opener(Response()), spacing=0, reserve=0)
        path = self.root / "a.jpg"
        with self.assertRaises(ImageError):
            dl.download(URL, allocate=lambda m: path, prepared=lambda p, f: p.write_bytes(b"unrelated"))
        self.assertEqual(path.read_bytes(), b"unrelated")
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_missing_mime_uses_signature(self):
        path, facts = self.download(Response(mime=None))
        self.assertEqual(facts.media_type, "image/jpeg")

    def test_unknown_svg_and_fake_jpeg(self):
        for body, mime in ((b"<svg/>", "image/svg+xml"), (b"<html>challenge</html>", "image/jpeg"), (b"unknown", None)):
            with self.assertRaises(ImageError):
                self.download(Response(body, mime))

    def test_deadline(self):
        ticks = iter([0, 0, 61])
        with self.assertRaisesRegex(ImageError, "timeout"):
            self.download(clock=lambda: next(ticks))

    def test_constants(self):
        self.assertEqual((MAX_IMAGE_BYTES, SOCKET_TIMEOUT, IMAGE_DEADLINE, CDN_START_SPACING), (50*1024*1024, 15, 60, .5))

    def test_cookiejar_and_redirect_handler_absent(self):
        import urllib.request
        from weibo_archive.image_downloader import RejectRedirects
        d = ImageDownloader()
        self.assertFalse(any(isinstance(h, urllib.request.HTTPCookieProcessor) for h in d.opener.handlers))
        self.assertTrue(any(isinstance(h, RejectRedirects) for h in d.opener.handlers))

    def test_http_error_sanitized_no_context(self):
        error = urllib.error.HTTPError(URL, 403, "PRIVATE", {}, io.BytesIO(b"PRIVATE"))
        with self.assertRaises(ImageError) as cm:
            self.download(error)
        self.assertEqual(str(cm.exception), "http_403")
        self.assertIsNone(cm.exception.__context__)
        self.assertEqual(len(self.opener.requests), 1)

    def test_redirect_handler_rejects_external_target(self):
        from weibo_archive.image_downloader import RejectRedirects
        self.assertIsNone(RejectRedirects().redirect_request(None, None, 302, "", {}, "https://evil.test"))

    def test_chunked_stream_and_missing_length(self):
        body = JPEG + b"x" * (160 * 1024)
        path, facts = self.download(Response(body, length=None))
        self.assertEqual(facts.byte_size, len(body))
        self.assertEqual(facts.sha256, hashlib.sha256(body).hexdigest())
        self.assertEqual(inspect_file(path).sha256, facts.sha256)

    def test_transport_encoding_rejected(self):
        r = Response()
        r.headers["Content-Encoding"] = "gzip"
        with self.assertRaisesRegex(ImageError, "invalid_content_encoding"):
            self.download(r)

    def test_malformed_content_length(self):
        with self.assertRaisesRegex(ImageError, "length_mismatch"):
            self.download(Response(length="2, 3"))

    def test_disk_space_drops_midstream(self):
        calls = iter([10**9, 10**9, 0])
        with self.assertRaisesRegex(ImageError, "insufficient_disk_space"):
            self.download(Response(JPEG + b"x" * 100000), disk_usage=lambda p: SimpleNamespace(free=next(calls)))
        self.assertFalse(list(self.root.iterdir()))

    def test_spacing_is_cancellation_aware(self):
        class Event:
            def __init__(self): self.waits = []
            def is_set(self): return False
            def wait(self, seconds): self.waits.append(seconds); return True
        event = Event()
        d = ImageDownloader(opener=Opener(Response()), cancel_event=event, clock=lambda: 10)
        d._last_start = 10
        with self.assertRaises(Cancelled):
            d.download(URL, allocate=lambda m: self.root / "x.jpg", prepared=lambda p,f: None)
        self.assertEqual(event.waits, [.5])
        self.assertEqual(d.request_count, 0)

    def test_tls_context_verification(self):
        import urllib.request
        d = ImageDownloader()
        h = next(h for h in d.opener.handlers if isinstance(h, urllib.request.HTTPSHandler))
        self.assertTrue(h._context.check_hostname)
        self.assertEqual(h._context.verify_mode, ssl.CERT_REQUIRED)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def run_backup(self, records=None, responses=None, *, report=REPORT, event=None, **kwargs):
        self.opener = Opener(*(responses if responses is not None else [Response()]))
        dl = ImageDownloader(opener=self.opener, spacing=0, reserve=0, **kwargs)
        self.engine = ImageBackup(self.root, cancel_event=event, downloader=dl)
        return self.engine.run_records("42", FetchRange.recent(1), [record()] if records is None else records, report=report)

    def data(self):
        return json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))

    def entry(self):
        d = self.data()
        return d["assets"][d["current_asset_keys"][0]]

    def test_40_existing_unrelated(self):
        path = self.root / "2026" / "101_01.jpg"
        path.parent.mkdir()
        path.write_bytes(b"unrelated")
        result = self.run_backup()
        self.assertEqual(result.saved, 1)
        self.assertEqual(path.read_bytes(), b"unrelated")

    def test_41_collision_suffix(self):
        folder = self.root / "2026"
        folder.mkdir()
        for name in ("101_01.jpg", "101_01_2.jpg"):
            (folder / name).write_bytes(b"unrelated")
        self.run_backup()
        self.assertEqual(self.entry()["relative_path"], "2026/101_01_3.jpg")

    def test_42_unknown_date(self):
        self.run_backup([record(created_at="刚刚")])
        self.assertTrue(self.entry()["relative_path"].startswith("unknown-date/"))

    def test_43_rt_path_and_source_year(self):
        rt = parse_image_records(raw(retweeted_status=raw(202, created_at="2024-01-01")))[1]
        self.run_backup([rt])
        self.assertEqual(self.entry()["relative_path"], "2024/101_RT_202_01.jpg")

    def test_44_schema(self):
        self.run_backup()
        self.assertEqual(self.data()["schema_version"], 1)

    def test_45_no_urls(self):
        self.run_backup()
        text = (self.root / "manifest.json").read_text()
        for token in ("https", "DO_NOT_PERSIST", "selected_url", "remote_url"):
            self.assertNotIn(token, text)

    def test_46_no_credentials_or_post_text(self):
        self.run_backup()
        text = (self.root / "manifest.json").read_text()
        for token in ("Cookie", "Referer", "headers", "PRIVATE_POST_TEXT", "screen_name", "videoSrc"):
            self.assertNotIn(token, text)

    def test_47_atomic_manifest(self):
        self.run_backup()
        before = (self.root / "manifest.json").read_bytes()
        with ManifestStore(self.root, "42") as store:
            store.data["state"] = "RUNNING"
            with patch("weibo_archive.image_manifest.os.replace", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    store.write()
        self.assertEqual((self.root / "manifest.json").read_bytes(), before)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_48_saved_after_commit(self):
        original = ManifestStore.terminal
        observed = []
        def terminal(store, key, status, reason=None):
            if status == "saved":
                e = store.data["assets"][key]
                self.assertEqual(e["status"], "pending")
                self.assertEqual(store.local_path(e["relative_path"]).read_bytes(), JPEG)
                observed.append(True)
            return original(store, key, status, reason)
        with patch.object(ManifestStore, "terminal", terminal):
            self.run_backup()
        self.assertEqual(observed, [True])

    def test_49_resume(self):
        self.run_backup()
        result = self.run_backup(responses=[])
        self.assertEqual(result.already_present, 1)
        self.assertEqual(result.saved, 0)
        self.assertFalse(self.opener.requests)

    def test_50_corrupt_redownload_preserves_corrupt_file(self):
        self.run_backup()
        path = self.root / self.entry()["relative_path"]
        path.write_bytes(b"corrupt")
        result = self.run_backup()
        self.assertEqual(result.saved, 1)
        self.assertEqual(path.read_bytes(), b"corrupt")
        self.assertTrue(self.entry()["relative_path"].endswith("_2.jpg"))

    def test_51_missing_redownload(self):
        self.run_backup()
        (self.root / self.entry()["relative_path"]).unlink()
        self.assertEqual(self.run_backup().saved, 1)

    def test_52_pending_recovery_requires_hash(self):
        self.run_backup()
        d = self.data()
        e = d["assets"][d["current_asset_keys"][0]]
        e["status"] = "pending"
        (self.root / "manifest.json").write_text(json.dumps(d))
        self.assertEqual(self.run_backup(responses=[]).already_present, 1)
        d = self.data()
        e = d["assets"][d["current_asset_keys"][0]]
        e.update(status="pending", sha256=None, byte_size=None)
        (self.root / "manifest.json").write_text(json.dumps(d))
        self.assertEqual(self.run_backup().saved, 1)
        self.assertTrue(self.entry()["relative_path"].endswith("_2.jpg"))

    def test_53_complete(self):
        self.assertEqual(self.run_backup().state, ImageResultState.COMPLETE)

    def test_54_gap_partial(self):
        result = self.run_backup([record(pic_num=12)])
        self.assertEqual((result.state, result.enumeration_gap, result.saved), (ImageResultState.PARTIAL, 11, 1))

    def test_55_failed_image_partial(self):
        result = self.run_backup([record(pics=[pic(), pic(pid="p2")])], [Response(), Response(status=404)])
        self.assertEqual((result.state, result.saved, result.unavailable), (ImageResultState.PARTIAL, 1, 1))

    def test_56_cancel_keeps_committed(self):
        event = threading.Event()
        result = self.run_backup([record(pics=[pic(), pic(pid="p2"), pic(pid="p3")])],
            [Response(), Response(on_read=lambda n: event.set())], event=event)
        self.assertEqual((result.state, result.saved, result.failed, result.pending), (ImageResultState.CANCELLED, 1, 0, 2))
        self.assertEqual(len(list(self.root.glob("2026/*.jpg"))), 1)
        self.assertFalse(list(self.root.rglob("*.tmp")))

    def test_57_failed_before_checkpoint(self):
        (self.root / "manifest.json").write_text("invalid-json")
        result = self.run_backup()
        self.assertEqual((result.state, result.failure_reason), (ImageResultState.FAILED, "invalid_manifest"))
        self.assertEqual((self.root / "manifest.json").read_text(), "invalid-json")

    def test_changed_pid_is_new_identity(self):
        self.run_backup()
        self.run_backup([record(pics=[pic(pid="new")])])
        self.assertEqual(len(self.data()["assets"]), 2)
        self.assertTrue(self.entry()["relative_path"].endswith("_2.jpg"))

    def test_pending_corrupt_not_adopted(self):
        self.run_backup()
        d = self.data()
        e = d["assets"][d["current_asset_keys"][0]]
        e["status"] = "pending"
        (self.root / e["relative_path"]).write_bytes(JPEG + b"corruption")
        (self.root / "manifest.json").write_text(json.dumps(d))
        self.assertEqual(self.run_backup().saved, 1)

    def test_disk_exhaustion(self):
        result = self.run_backup(disk_usage=lambda p: SimpleNamespace(free=0))
        self.assertEqual((result.state, result.failure_reason), (ImageResultState.FAILED, "insufficient_disk_space"))

    def test_global_discovery_stop_after_progress(self):
        client = Pages([[raw()], ImageError("no_progress")])
        dl = ImageDownloader(opener=Opener(Response()), spacing=0, reserve=0)
        result = ImageBackup(self.root, downloader=dl).run("42", FetchRange.all(), client=client)
        self.assertEqual((result.state, result.saved, result.failure_reason), (ImageResultState.PARTIAL, 1, "no_progress"))

    def test_manifest_write_failure_after_file_commit(self):
        original = ManifestStore.terminal
        def broken(store, key, status, reason=None):
            if status == "saved":
                raise OSError("storage")
            return original(store, key, status, reason)
        with patch.object(ManifestStore, "terminal", broken):
            result = self.run_backup()
        self.assertTrue(list(self.root.glob("2026/*.jpg")))
        self.assertEqual((result.state, result.saved), (ImageResultState.PARTIAL, 1))
        self.assertEqual(self.run_backup(responses=[]).already_present, 1)

    def test_path_traversal_manifest_rejected(self):
        self.run_backup()
        d = self.data()
        d["assets"][d["current_asset_keys"][0]]["relative_path"] = "../outside.jpg"
        (self.root / "manifest.json").write_text(json.dumps(d))
        result = self.run_backup(responses=[])
        self.assertEqual(result.state, ImageResultState.FAILED)
        self.assertFalse(self.opener.requests)

    def test_unknown_manifest_field_rejected(self):
        self.run_backup()
        d = self.data()
        d["remote_url"] = URL
        (self.root / "manifest.json").write_text(json.dumps(d))
        self.assertEqual(self.run_backup(responses=[]).state, ImageResultState.FAILED)

    def test_target_mismatch(self):
        self.run_backup()
        with self.assertRaisesRegex(ImageError, "target_mismatch"):
            with ManifestStore(self.root, "43"):
                pass

    def test_single_writer_lock(self):
        with ManifestStore(self.root, "42"):
            with self.assertRaises(ImageError):
                with ManifestStore(self.root, "42"):
                    pass

    def test_empty_valid_range_complete(self):
        result = self.run_backup([], [], report=replace(REPORT, termination=Termination.NATURAL))
        self.assertEqual((result.state, result.discovered), (ImageResultState.COMPLETE, 0))

    def test_403_not_deleted(self):
        result = self.run_backup(responses=[Response(status=403)])
        self.assertEqual((result.failed, result.unavailable, result.failure_reason), (1, 0, "http_403"))

    def test_incomplete_discovery_not_complete(self):
        result = self.run_backup(report=replace(REPORT, termination=None))
        self.assertEqual(result.state, ImageResultState.PARTIAL)

    def test_unknown_type_prevents_complete(self):
        result = self.run_backup([record(pics=[pic(), pic(type="future")])])
        self.assertEqual((result.state, result.discovery_warnings), (ImageResultState.PARTIAL, 1))

    def test_failed_asset_partial_distinct_from_unavailable(self):
        result = self.run_backup([record(pics=[pic(), pic(pid="p2")])], [Response(), Response(status=500)])
        self.assertEqual((result.state, result.saved, result.failed, result.unavailable), (ImageResultState.PARTIAL, 1, 1, 0))

    def test_storage_failure_before_useful_work(self):
        with patch("weibo_archive.image_manifest.os.replace", side_effect=OSError("PRIVATE")):
            result = self.run_backup()
        self.assertEqual((result.state, result.failure_reason), (ImageResultState.FAILED, "storage_error"))
        self.assertFalse(self.opener.requests)

    def test_all_downloads_failed(self):
        result = self.run_backup(responses=[Response(status=500)])
        self.assertEqual((result.state, result.failed), (ImageResultState.FAILED, 1))

    def test_wrong_saved_hash_not_skipped(self):
        self.run_backup()
        d = self.data()
        d["assets"][d["current_asset_keys"][0]]["sha256"] = "0" * 64
        (self.root / "manifest.json").write_text(json.dumps(d))
        self.assertEqual(self.run_backup().saved, 1)

    def test_symlink_output_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            alias = self.root / "alias"
            try:
                alias.symlink_to(other, target_is_directory=True)
            except OSError:
                # Windows may deny symlink creation even outside the sandbox.
                # Exercise its reparse-point rejection deterministically instead.
                original = Path.lstat
                def reparse(path):
                    if path == alias:
                        return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
                    return original(path)
                with patch.object(Path, "lstat", reparse):
                    with self.assertRaisesRegex(ImageError, "unsafe_path"):
                        with ManifestStore(alias, "42"):
                            pass
                return
            with self.assertRaisesRegex(ImageError, "unsafe_path"):
                with ManifestStore(alias, "42"):
                    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
