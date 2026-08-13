#!/usr/bin/env python3
from __future__ import annotations

import ast
import copy
import json
import runpy
import struct
import subprocess
import sys
import tempfile
from dataclasses import FrozenInstanceError, asdict, fields, replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from weibo_archive.client import (
    ContentUnavailable,
    HydrationOutcome,
    IncompleteContent,
    WeiboClient,
    _embedded_status_from_detail,
)
from weibo_archive.export_options import (
    AI_COMPACT_OPTIONS,
    FULL_ARCHIVE_OPTIONS,
    DateFormat,
    ExportLayout,
    ExportOptions,
    ExportPreset,
    filename_suffix_for_selection,
    options_for_preset,
)
from weibo_archive.exporter import (
    archive_to_legacy_data,
    export_markdown,
    render_legacy_markdown,
)
from weibo_archive.models import (
    Archive,
    ArchiveIntegrity,
    ContentState,
    Engagement,
    FetchRange,
    FetchReport,
    MediaInfo,
    IncompleteReason,
    Post,
    RangeMode,
    Termination,
    UserProfile,
    calculate_archive_integrity,
)
from weibo_archive.parser import extract_mblogs, parse_post, parse_profile
from weibo_archive.network import (
    Cancelled,
    HttpClient,
    InvalidResponse,
    NetworkError,
    ResponseData,
    SafeRequestDiagnostic,
    classify_non_json_response,
)
from weibo_archive.security import redact_text
from weibo_archive.tasking import TaskManager, TaskState


class _HistoricalTrialRange(FetchRange):
    """Preserve accepted Full Archive golden provenance as historical test input."""

    def label(self) -> str:
        return "试抓50条"


def build_archive() -> Archive:
    return Archive(
        profile=UserProfile(
            id="1234567890",
            screen_name="测试用户",
            description="V7 模型→渲染器回归样本",
            followers_count=321,
            follow_count=45,
            statuses_count=3,
            location="北京",
            verified=False,
        ),
        posts=(
            Post(
                id="1003", bid="b3",
                created_at=datetime(2026, 8, 13, 0, 10),
                text="这是转发时写的评论。",
                source="iPhone客户端", location="",
                author="测试用户",
                engagement=Engagement(1, 1, 5),
                media=MediaInfo(),
                retweet=Post(
                    id="9001", bid="rb1",
                    created_at=datetime(2026, 8, 10, 12, 0),
                    text="这是被转发的原文。",
                    source="", location="",
                    author="原作者",
                    engagement=Engagement(12, 8, 99),
                    media=MediaInfo(images=1),
                ),
            ),
            Post(
                id="1002", bid="b2",
                created_at=datetime(2026, 8, 12, 18, 30),
                text="带图片的微博。",
                source="iPhone客户端", location="上海",
                author="测试用户",
                engagement=Engagement(3, 0, 21),
                media=MediaInfo(images=3),
            ),
            Post(
                id="1001", bid="b1",
                created_at=datetime(2026, 8, 11, 6, 24),
                text="第一条原创微博。",
                source="微博网页版", location="北京",
                author="测试用户",
                engagement=Engagement(None, 2, 10),
                media=MediaInfo(),
            ),
        ),
        fetch_range=_HistoricalTrialRange(RangeMode.TRIAL, limit=50),
        report=FetchReport(
            pages_fetched=1,
            requests_made=7,
            termination=Termination.TARGET_COUNT,
            oldest_reached=datetime(2026, 8, 11, 6, 24),
            newest_reached=datetime(2026, 8, 13, 0, 10),
            profile_statuses_count=3,
        ),
        fetched_at=datetime(2026, 8, 13, 2, 0),
    )


def build_alpha3_archive() -> Archive:
    """Use non-empty repost metadata without changing the accepted legacy fixture."""
    archive = build_archive()
    first = archive.posts[0]
    retweet = replace(
        first.retweet,
        created_at=datetime(2026, 8, 10, 12, 0, 37),
        source="Android客户端",
        location="广州",
    )
    posts = (
        replace(
            first,
            created_at=datetime(2026, 8, 13, 0, 10, 45),
            retweet=retweet,
        ),
        replace(archive.posts[1], created_at=datetime(2026, 8, 12, 18, 30, 29)),
        replace(archive.posts[2], created_at=datetime(2026, 8, 11, 6, 24, 11)),
    )
    return replace(archive, posts=posts)


def _as_incomplete(post: Post, preview: str | None) -> Post:
    return replace(
        post,
        text=None,
        content_state=ContentState.INCOMPLETE,
        text_preview=preview,
        incomplete_reason=IncompleteReason.CONTENT_UNAVAILABLE,
    )


def build_alpha4_archive() -> Archive:
    archive = build_alpha3_archive()
    first, second, third = archive.posts
    first = replace(
        first,
        retweet=_as_incomplete(first.retweet, "原文列表预览……全文"),
    )
    second = _as_incomplete(second, "顶层列表预览……全文")
    return replace(archive, posts=(first, second, third))


def test_startup_import():
    result = subprocess.run(
        [sys.executable, "-c", "import weibo_archive.app; print('IMPORT_OK')"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if result.returncode != 0 or "IMPORT_OK" not in result.stdout:
        raise AssertionError(result.stdout + "\n" + result.stderr)


def test_alpha4_version_and_gui_launcher():
    from weibo_archive import VERSION_DISPLAY, __version__

    assert __version__ == "0.4.2"
    assert VERSION_DISPLAY == "0.4.2"

    app_source = (ROOT / "weibo_archive" / "app.py").read_text(encoding="utf-8")
    assert "from . import VERSION_DISPLAY" in app_source
    assert "__version__" not in app_source
    assert "Alpha 1" not in app_source
    assert "Alpha1" not in app_source

    launcher = ROOT / "WeiboTextArchiver.pyw"
    namespace = runpy.run_path(str(launcher), run_name="alpha4_launcher_import_test")
    assert callable(namespace["main"])
    assert callable(namespace["_show_startup_error"])

    user_visible_startup_files = (
        ROOT / "START.bat",
        ROOT / "CHECK_ENV.bat",
        ROOT / "TEST.bat",
        ROOT / "tests" / "RUN_TESTS.bat",
        ROOT / "README.md",
        ROOT / "BUILD_WINDOWS.bat",
        launcher,
    )
    for path in user_visible_startup_files:
        source = path.read_text(encoding="utf-8-sig")
        assert "Weibo Archive V7.0 Alpha 1" not in source
        assert "Alpha1" not in source


def test_windows_preview_packaging_contract():
    from weibo_archive import VERSION_DISPLAY
    from weibo_archive.app import APP_TITLE, TEST_EXPORT_LIMIT, App
    from weibo_archive.models import RangeMode
    from weibo_archive.paths import resource_path

    assert APP_TITLE == "Weibo Text Archiver"
    assert f"{APP_TITLE} · {VERSION_DISPLAY}" == "Weibo Text Archiver · 0.4.2"
    assert TEST_EXPORT_LIMIT == 20
    trial_range = App._selected_range(object(), True)
    assert trial_range.mode is RangeMode.TRIAL
    assert trial_range.limit == 20

    class Value:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    formal_controls = type(
        "FormalRangeControls",
        (),
        {
            "range_mode_var": Value(RangeMode.RECENT.value),
            "recent_count_var": Value("37"),
        },
    )()
    formal_range = App._selected_range(formal_controls, False)
    assert formal_range.mode is RangeMode.RECENT
    assert formal_range.limit == 37
    png_path = ROOT / "assets" / "app_icon.png"
    ico_path = ROOT / "assets" / "app_icon.ico"
    assert png_path.is_file()
    assert ico_path.is_file()
    assert resource_path("assets/app_icon.png").resolve() == png_path.resolve()

    png_header = png_path.read_bytes()[:24]
    assert png_header[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png_header[16:24]) == (512, 512)

    ico = ico_path.read_bytes()
    reserved, image_type, count = struct.unpack("<HHH", ico[:6])
    assert (reserved, image_type) == (0, 1)
    sizes = set()
    for index in range(count):
        width_byte, height_byte = struct.unpack_from("BB", ico, 6 + index * 16)
        width = width_byte or 256
        height = height_byte or 256
        if width == height:
            sizes.add(width)
    assert {16, 24, 32, 48, 64, 128, 256} <= sizes

    generator = ROOT / "tools" / "generate_icon.py"
    result = subprocess.run(
        [sys.executable, str(generator), "--check"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    spec = (ROOT / "weibo_text_archiver.spec").read_text(encoding="utf-8")
    assert "console=False" in spec
    assert "exclude_binaries=True" in spec
    assert "COLLECT(" in spec
    assert "onefile" not in spec.lower()
    assert "assets/app_icon.png" not in spec  # Native Path joins remain portable.
    assert '"assets" / "app_icon.png"' in spec
    assert '"assets" / "app_icon.ico"' in spec
    assert '"PIL"' in spec

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for ignored in (".venv-build/", "/build/", "/dist/", "/release/"):
        assert ignored in gitignore

    requirements = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")
    assert "PyInstaller==" in requirements
    assert "Pillow==" in requirements

    runtime_imports = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "weibo_archive").glob("*.py")
    )
    assert "from PIL" not in runtime_imports
    assert "import PIL" not in runtime_imports

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# Weibo Text Archiver\n")
    assert not any("\u4e00" <= character <= "\u9fff" for character in readme)
    assert not (ROOT / "README_先看.txt").exists()
    assert not (ROOT / "docs" / "ROADMAP.md").exists()
    assert not (ROOT / "启动微博文字导出器.bat").exists()
    assert not (ROOT / "环境检查.bat").exists()
    assert (ROOT / "docs" / "ARCHITECTURE.md").is_file()
    assert (ROOT / "docs" / "SOURCE_NOTES.md").is_file()
    assert (ROOT / "THIRD_PARTY_NOTICES.txt").is_file()

    app_source = (ROOT / "weibo_archive" / "app.py").read_text(encoding="utf-8")
    for chinese_ui_text in (
        "扫码登录 / 更新",
        "导出内容",
        "测试导出",
        "开始导出 →",
        "清除登录信息",
    ):
        assert chinese_ui_text in app_source
    for obsolete_action in ("试抓 50 条", "开始备份 →", 'text="清除登录"'):
        assert obsolete_action not in app_source
    client_source = (ROOT / "weibo_archive" / "client.py").read_text(encoding="utf-8")
    assert "V7 " not in app_source
    assert "V7 " not in client_source


def test_parser_contract():
    basic = json.loads((ROOT / "tests/fixtures/profile_basic.json").read_text(encoding="utf-8"))
    detail = json.loads((ROOT / "tests/fixtures/profile_detail.json").read_text(encoding="utf-8"))
    timeline = json.loads((ROOT / "tests/fixtures/timeline_page.json").read_text(encoding="utf-8"))

    profile = parse_profile("1234567890", basic, detail)
    assert profile.screen_name == "测试用户"
    assert profile.location == "北京"
    assert profile.education == "测试大学"
    assert profile.company == "测试公司"

    mblogs = extract_mblogs(timeline["data"]["cards"])
    assert len(mblogs) == 3

    posts = [parse_post(x) for x in mblogs]
    assert posts[0].retweet is not None
    assert posts[0].retweet.author == "原作者"
    assert posts[0].retweet.media.images == 1

    assert posts[1].media.images == 3
    assert posts[1].location == "上海"
    assert "带图片的微博" in posts[1].text

    # Core semantic invariant: API field missing != explicit zero.
    assert posts[2].engagement.reposts is None
    assert posts[1].engagement.comments == 0


def test_model_boundary():
    post_fields = {f.name for f in fields(Post)}
    assert "raw" not in post_fields
    assert "json" not in post_fields

    p = build_archive().posts[0]
    try:
        p.text = "mutation should fail"
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("Post is not frozen")


def test_alpha4_post_invariants_and_integrity_combinations():
    base = build_archive().posts[1]
    media_only = replace(base, id="media", text="", media=MediaInfo(images=1))
    assert media_only.content_state is ContentState.COMPLETE
    assert media_only.text == ""

    incomplete = _as_incomplete(replace(base, id="inc"), None)
    assert incomplete.text is None
    assert incomplete.text_preview is None

    invalid_changes = (
        {"text": None},
        {"text_preview": "preview"},
        {"incomplete_reason": IncompleteReason.CONTENT_UNAVAILABLE},
        {
            "id": "",
            "text": None,
            "content_state": ContentState.INCOMPLETE,
            "incomplete_reason": IncompleteReason.CONTENT_UNAVAILABLE,
        },
        {
            "text": "preview must not be text",
            "content_state": ContentState.INCOMPLETE,
            "incomplete_reason": IncompleteReason.CONTENT_UNAVAILABLE,
        },
        {
            "text": None,
            "content_state": ContentState.INCOMPLETE,
            "incomplete_reason": None,
        },
    )
    for changes in invalid_changes:
        try:
            replace(base, **changes)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"invalid Post state accepted: {changes}")

    complete_retweet = replace(base, id="rt-complete")
    incomplete_retweet = _as_incomplete(replace(base, id="rt-incomplete"), "预览")
    posts = (
        replace(base, id="both-complete", retweet=complete_retweet),
        replace(_as_incomplete(replace(base, id="top-incomplete"), "预览"), retweet=complete_retweet),
        replace(base, id="retweet-incomplete", retweet=incomplete_retweet),
        replace(_as_incomplete(replace(base, id="both-incomplete"), "预览"), retweet=incomplete_retweet),
    )
    integrity = calculate_archive_integrity(posts)
    assert integrity == ArchiveIntegrity(4, 1, 3, 2, 2)
    assert integrity.complete_records + integrity.incomplete_records == integrity.total_posts

    archive = replace(build_archive(), posts=posts)
    assert archive.integrity == integrity
    assert "integrity" not in {field.name for field in fields(Archive)}


def test_alpha4_parser_explicit_reasons_and_raw_immutability():
    raw = {
        "id": "top",
        "text": "<p>顶层预览……全文</p>",
        "user": {"screen_name": "用户"},
        "retweeted_status": {
            "id": "retweet",
            "text": "<p>转发预览……全文</p>",
            "user": {"screen_name": "原作者"},
        },
    }
    before = copy.deepcopy(raw)

    combinations = (
        (None, None, ContentState.COMPLETE, ContentState.COMPLETE),
        (IncompleteReason.CONTENT_UNAVAILABLE, None, ContentState.INCOMPLETE, ContentState.COMPLETE),
        (None, IncompleteReason.CONTENT_UNAVAILABLE, ContentState.COMPLETE, ContentState.INCOMPLETE),
        (
            IncompleteReason.CONTENT_UNAVAILABLE,
            IncompleteReason.CONTENT_UNAVAILABLE,
            ContentState.INCOMPLETE,
            ContentState.INCOMPLETE,
        ),
    )
    for top_reason, retweet_reason, top_state, retweet_state in combinations:
        post = parse_post(
            raw,
            incomplete_reason=top_reason,
            retweet_incomplete_reason=retweet_reason,
        )
        assert post.content_state is top_state
        assert post.retweet.content_state is retweet_state
        if top_state is ContentState.INCOMPLETE:
            assert post.text is None
            assert post.text_preview == "顶层预览……全文"
        if retweet_state is ContentState.INCOMPLETE:
            assert post.retweet.text is None
            assert post.retweet.text_preview == "转发预览……全文"

    assert raw == before


def test_longtext_detail_decoder():
    html = (
        '<script>window.__DATA__={"status":'
        '{"id":"42","text":"<p>完整长微博正文</p>","isLongText":true},'
        '"call":"ok","other":{"x":1}}</script>'
    )
    status = _embedded_status_from_detail(html)
    assert status is not None
    assert status["id"] == "42"
    assert "完整长微博正文" in status["text"]


def _fixture_bytes(name: str) -> bytes:
    return (ROOT / "tests" / "fixtures" / name).read_bytes()


def _longtext_client(fake_request):
    import threading

    client = WeiboClient(
        cookie_header="",
        cancel_event=threading.Event(),
        progress=lambda *args, **kwargs: None,
    )
    client._wait_random = lambda *args: None
    client.http.request = fake_request
    return client


def test_network_non_json_diagnostic_has_no_body_or_query():
    http = HttpClient()
    body = _fixture_bytes("longtext_no_permission.html")
    query_secret = "fixture_query_secret"

    http.request = lambda *args, **kwargs: ResponseData(
        body=body,
        url=(
            "https://m.weibo.cn/statuses/extend"
            f"?id=9000000000000001&alt={query_secret}"
        ),
        status=200,
        content_type="text/html",
    )

    try:
        http.json_response("https://m.weibo.cn/statuses/extend")
    except InvalidResponse as exc:
        diagnostic = exc.diagnostic
        assert diagnostic is not None
        assert diagnostic.classification == "no_view_permission_html"
        assert diagnostic.host == "m.weibo.cn"
        assert diagnostic.path == "/statuses/extend"
        assert diagnostic.status == 200
        assert diagnostic.content_type == "text/html"
        assert diagnostic.body_bytes == len(body)
        rendered = str(exc) + repr(diagnostic)
        assert query_secret not in rendered
        assert "暂无查看权限" not in rendered
        assert "?" not in diagnostic.path
        assert exc.__cause__ is None
        assert exc.__context__ is None
    else:
        raise AssertionError("HTML permission page was accepted as JSON")


def test_non_json_classifier_prioritizes_challenge_and_login():
    assert classify_non_json_response(
        "<html>安全验证 暂无查看权限</html>",
        "text/html",
    ) == "challenge_html"
    assert classify_non_json_response(
        "<html>请登录 passport login 暂无查看权限</html>",
        "text/html",
    ) == "login_html"
    assert classify_non_json_response(
        "<html>暂无查看权限</html>",
        "text/html",
    ) == "no_view_permission_html"


def test_longtext_permission_failure_preserves_two_safe_attempts():
    body = _fixture_bytes("longtext_no_permission.html")
    calls = []

    def fake_request(url, **kwargs):
        calls.append(url)
        if url.endswith("/statuses/extend"):
            final_url = url + "?id=9000000000000001&alt=fixture_query_secret"
        else:
            final_url = url
        return ResponseData(
            body=body,
            url=final_url,
            status=200,
            content_type="text/html",
        )

    client = _longtext_client(fake_request)
    raw = {
        "id": "parent-post",
        "text": "转发微博",
        "retweeted_status": {
            "id": "9000000000000001",
            "isLongText": True,
            "text": "只有列表摘要……全文",
        },
    }

    try:
        client._fetch_full_text_html(raw["retweeted_status"])
    except ContentUnavailable as exc:
        assert exc.post_id == "9000000000000001"
        assert exc.reason is IncompleteReason.CONTENT_UNAVAILABLE
        assert len(exc.attempts) == 2
        assert [attempt.fallback for attempt in exc.attempts] == ["extend", "detail"]
        assert [attempt.outcome for attempt in exc.attempts] == [
            "no_view_permission_html",
            "no_view_permission_html",
        ]
        assert all(attempt.status == 200 for attempt in exc.attempts)
        assert all(attempt.content_type == "text/html" for attempt in exc.attempts)
        assert all(attempt.body_bytes == len(body) for attempt in exc.attempts)
        rendered = str(exc)
        assert "fixture_query_secret" not in rendered
        assert "暂无查看权限" not in rendered
        assert "只有列表摘要" not in rendered
        assert "9000000000000001" not in client.long_text_cache
        assert client.long_text_diagnostics["9000000000000001"] == exc.attempts
    else:
        raise AssertionError("permission failure silently returned truncated content")

    assert calls == [
        "https://m.weibo.cn/statuses/extend",
        "https://m.weibo.cn/detail/9000000000000001",
    ]


def test_longtext_mixed_or_challenge_outcomes_remain_fatal():
    permission = _fixture_bytes("longtext_no_permission.html")
    unknown = _fixture_bytes("longtext_detail_missing_status.html")
    timeout = NetworkError(
        "timeout",
        diagnostic=SafeRequestDiagnostic(
            classification="timeout",
            host="m.weibo.cn",
            path="/detail/42",
        ),
    )

    cases = []

    def permission_then_timeout(url, **kwargs):
        if url.endswith("/statuses/extend"):
            return ResponseData(permission, url, 200, "text/html")
        raise timeout

    cases.append((permission_then_timeout, ["no_view_permission_html", "timeout"]))

    def permission_then_unknown(url, **kwargs):
        body = permission if url.endswith("/statuses/extend") else unknown
        return ResponseData(body, url, 200, "text/html")

    cases.append(
        (permission_then_unknown, ["no_view_permission_html", "embedded_status_absent"])
    )

    challenge = "<html>安全验证 暂无查看权限</html>".encode("utf-8")

    def challenge_with_permission_marker(url, **kwargs):
        return ResponseData(challenge, url, 200, "text/html")

    cases.append((challenge_with_permission_marker, ["challenge_html", "challenge_html"]))

    login = "<html>请登录 passport login 暂无查看权限</html>".encode("utf-8")

    def login_with_permission_marker(url, **kwargs):
        return ResponseData(login, url, 200, "text/html")

    cases.append((login_with_permission_marker, ["login_html", "login_html"]))

    for fake_request, expected in cases:
        client = _longtext_client(fake_request)
        try:
            client._fetch_full_text_html({"id": "42"})
        except ContentUnavailable:
            raise AssertionError("mixed/challenge response became recoverable")
        except IncompleteContent as exc:
            assert [attempt.outcome for attempt in exc.attempts] == expected
            assert "42" not in client.unavailable_cache
        else:
            raise AssertionError("mixed/challenge response was accepted")


def test_longtext_cancelled_is_not_converted_to_incomplete():
    import threading

    cancel = threading.Event()
    cancel.set()
    client = WeiboClient(
        cookie_header="",
        cancel_event=cancel,
        progress=lambda *args, **kwargs: None,
    )
    try:
        client._fetch_full_text_html({"id": "42"})
    except Cancelled:
        assert not client.unavailable_cache
    else:
        raise AssertionError("cancelled long-text acquisition did not remain cancelled")


def test_unavailable_negative_cache_is_deterministic():
    permission = _fixture_bytes("longtext_no_permission.html")
    calls = []

    def fake_request(url, **kwargs):
        calls.append(url)
        return ResponseData(permission, url, 200, "text/html")

    client = _longtext_client(fake_request)
    raw = {
        "id": "parent",
        "text": "用户评论",
        "retweeted_status": {
            "id": "9000000000000001",
            "isLongText": True,
            "text": "列表预览……全文",
        },
    }

    for _ in range(5):
        outcome = client._hydrate_long_texts(raw)
        assert outcome.retweet_incomplete_reason is IncompleteReason.CONTENT_UNAVAILABLE

    assert len(calls) == 2
    assert client.unique_long_text_attempted == 1
    assert client.unique_content_unavailable == 1
    assert client.unavailable_cache == {
        "9000000000000001": IncompleteReason.CONTENT_UNAVAILABLE
    }
    assert "9000000000000001" not in client.long_text_cache


def test_top_and_retweet_hydration_states_are_independent():
    raw = {
        "id": "top",
        "isLongText": True,
        "text": "顶层预览……全文",
        "user": {"screen_name": "用户"},
        "retweeted_status": {
            "id": "retweet",
            "isLongText": True,
            "text": "转发预览……全文",
            "user": {"screen_name": "原作者"},
        },
    }

    for unavailable_ids, expected_states in (
        ({"retweet"}, (ContentState.COMPLETE, ContentState.INCOMPLETE)),
        ({"top"}, (ContentState.INCOMPLETE, ContentState.COMPLETE)),
        ({"top", "retweet"}, (ContentState.INCOMPLETE, ContentState.INCOMPLETE)),
    ):
        client = _longtext_client(lambda *args, **kwargs: None)
        calls = []

        def fetch_full(node):
            post_id = str(node["id"])
            calls.append(post_id)
            if post_id in unavailable_ids:
                raise ContentUnavailable(
                    post_id,
                    reason=IncompleteReason.CONTENT_UNAVAILABLE,
                )
            return f"<p>{post_id} 的完整正文</p>"

        client._fetch_full_text_html = fetch_full
        before = copy.deepcopy(raw)
        outcome = client._hydrate_long_texts(raw)
        post = parse_post(
            outcome.raw,
            incomplete_reason=outcome.top_incomplete_reason,
            retweet_incomplete_reason=outcome.retweet_incomplete_reason,
        )
        client._validate_post_hydration(post, outcome)
        assert (post.content_state, post.retweet.content_state) == expected_states
        assert calls == ["top", "retweet"]
        assert raw == before


def test_runtime_post_parse_validation_fails_closed():
    client = _longtext_client(lambda *args, **kwargs: None)
    complete = build_archive().posts[0]

    for outcome in (
        HydrationOutcome(
            raw={},
            top_incomplete_reason=IncompleteReason.CONTENT_UNAVAILABLE,
        ),
        HydrationOutcome(
            raw={},
            retweet_incomplete_reason=IncompleteReason.CONTENT_UNAVAILABLE,
        ),
    ):
        try:
            client._validate_post_hydration(complete, outcome)
        except IncompleteContent:
            pass
        else:
            raise AssertionError("parser safety regression was not rejected")


def test_longtext_global_safety_fuse_thresholds_and_unique_ids():
    attempts = (
        type("Attempt", (), {"summary": lambda self: "safe"})(),
    )

    consecutive = _longtext_client(lambda *args, **kwargs: None)
    for post_id in ("1", "2"):
        consecutive._begin_long_text_acquisition(post_id)
        consecutive._record_content_unavailable(post_id, attempts)
    consecutive._begin_long_text_acquisition("3")
    try:
        consecutive._record_content_unavailable("3", attempts)
    except IncompleteContent as exc:
        assert "连续 3 个" in str(exc)
    else:
        raise AssertionError("three consecutive unavailable IDs did not trip fuse")

    ratio = _longtext_client(lambda *args, **kwargs: None)
    ratio._begin_long_text_acquisition("1")
    ratio._record_content_unavailable("1", attempts)
    for post_id in ("2", "3", "4"):
        ratio._begin_long_text_acquisition(post_id)
        ratio._record_long_text_success()
    ratio._begin_long_text_acquisition("5")
    try:
        ratio._record_content_unavailable("5", attempts)
    except IncompleteContent:
        assert ratio.unique_content_unavailable == 2
        assert ratio.unique_long_text_attempted == 5
    else:
        raise AssertionError("more than 20 percent unavailable did not trip fuse")

    ratio_crossed_by_success = _longtext_client(lambda *args, **kwargs: None)
    for post_id, unavailable in (
        ("1", True),
        ("2", False),
        ("3", True),
        ("4", False),
    ):
        ratio_crossed_by_success._begin_long_text_acquisition(post_id)
        if unavailable:
            ratio_crossed_by_success._record_content_unavailable(post_id, attempts)
        else:
            ratio_crossed_by_success._record_long_text_success()
    ratio_crossed_by_success._begin_long_text_acquisition("5")
    try:
        ratio_crossed_by_success._record_long_text_success()
    except IncompleteContent:
        assert ratio_crossed_by_success.unique_content_unavailable == 2
        assert ratio_crossed_by_success.unique_long_text_attempted == 5
        assert ratio_crossed_by_success.consecutive_unique_unavailable == 0
    else:
        raise AssertionError("ratio crossing on a successful fifth ID did not trip fuse")

    sparse = _longtext_client(lambda *args, **kwargs: None)
    for number in range(1, 1001):
        post_id = str(number)
        sparse._begin_long_text_acquisition(post_id)
        if number in (1, 500, 1000):
            sparse._record_content_unavailable(post_id, attempts)
        else:
            sparse._record_long_text_success()
    sparse._begin_long_text_acquisition("1000")
    sparse._record_content_unavailable("1000", attempts)
    assert sparse.unique_long_text_attempted == 1000
    assert sparse.unique_content_unavailable == 3


def test_longtext_detail_fallback_success_after_extend_schema_failure():
    extend_body = _fixture_bytes("longtext_extend_missing_content.json")
    detail_body = _fixture_bytes("longtext_detail_success.html")
    calls = []

    def fake_request(url, **kwargs):
        calls.append(url)
        if url.endswith("/statuses/extend"):
            return ResponseData(
                extend_body,
                url + "?id=9000000000000001",
                200,
                "application/json",
            )
        return ResponseData(detail_body, url, 200, "text/html")

    client = _longtext_client(fake_request)
    content = client._fetch_full_text_html({"id": "9000000000000001"})
    assert content == "<p>详情页中的完整长微博正文</p>"
    attempts = client.long_text_diagnostics["9000000000000001"]
    assert [attempt.outcome for attempt in attempts] == [
        "json_missing_full_text",
        "success",
    ]
    assert attempts[0].json_keys == ("data", "ok")
    assert attempts[0].data_keys == (
        "attitudes_count",
        "comments_count",
        "reposts_count",
    )
    assert attempts[1].embedded_status_present is True
    assert attempts[1].id_matches is True
    assert len(calls) == 2


def test_longtext_extend_supported_shapes_do_not_call_detail():
    cases = (
        ("longtext_extend_success.json", "data.longTextContent"),
        ("longtext_extend_nested_success.json", "data.longText.longTextContent"),
    )
    for fixture, expected_field in cases:
        body = _fixture_bytes(fixture)
        calls = []

        def fake_request(url, **kwargs):
            calls.append(url)
            if not url.endswith("/statuses/extend"):
                raise AssertionError("detail fallback should not run after extend success")
            return ResponseData(
                body,
                url + "?id=9000000000000001",
                200,
                "application/json",
            )

        client = _longtext_client(fake_request)
        content = client._fetch_full_text_html({"id": "9000000000000001"})
        assert "完整长微博正文" in content
        attempts = client.long_text_diagnostics["9000000000000001"]
        assert len(attempts) == 1
        assert attempts[0].outcome == "success"
        assert attempts[0].content_field == expected_field
        assert attempts[0].content_chars == len(content)
        assert "9000000000000001" not in client.unavailable_cache
        assert calls == ["https://m.weibo.cn/statuses/extend"]


def test_longtext_detail_id_mismatch_fails_closed():
    extend_body = _fixture_bytes("longtext_extend_missing_content.json")
    detail_body = _fixture_bytes("longtext_detail_success.html").replace(
        b'"9000000000000001"', b'"9999999999999999"'
    )

    def fake_request(url, **kwargs):
        if url.endswith("/statuses/extend"):
            return ResponseData(extend_body, url, 200, "application/json")
        return ResponseData(detail_body, url, 200, "text/html")

    client = _longtext_client(fake_request)
    try:
        client._fetch_full_text_html({"id": "9000000000000001"})
    except IncompleteContent as exc:
        assert exc.attempts[-1].outcome == "embedded_status_id_mismatch"
        assert exc.attempts[-1].embedded_status_present is True
        assert exc.attempts[-1].id_matches is False
        assert "9000000000000001" not in client.long_text_cache
    else:
        raise AssertionError("mismatched embedded status was accepted as full text")


def test_longtext_unknown_detail_schema_fails_closed():
    extend_body = _fixture_bytes("longtext_extend_missing_content.json")
    detail_body = _fixture_bytes("longtext_detail_missing_status.html")

    def fake_request(url, **kwargs):
        if url.endswith("/statuses/extend"):
            return ResponseData(extend_body, url, 200, "application/json")
        return ResponseData(detail_body, url, 200, "text/html")

    client = _longtext_client(fake_request)
    try:
        client._fetch_full_text_html({"id": "9000000000000001"})
    except IncompleteContent as exc:
        assert [attempt.outcome for attempt in exc.attempts] == [
            "json_missing_full_text",
            "embedded_status_absent",
        ]
        assert exc.attempts[-1].embedded_status_present is False
        assert "9000000000000001" not in client.long_text_cache
    else:
        raise AssertionError("unknown detail schema was treated as complete content")


def test_exporter_golden():
    archive = build_archive()
    expected_full = (ROOT / "tests/golden/model_full.md").read_text(encoding="utf-8")
    expected_ai = (ROOT / "tests/golden/model_ai.md").read_text(encoding="utf-8")

    full_text, _ = render_legacy_markdown(archive)
    _, ai_text = render_legacy_markdown(
        replace(archive, fetch_range=FetchRange.trial(20))
    )
    assert full_text == expected_full
    assert ai_text == expected_ai


def test_alpha3_exporter_goldens_and_single_output():
    archive = build_alpha3_archive()
    cases = (
        (
            FULL_ARCHIVE_OPTIONS,
            "完整",
            "model_alpha3_full.md",
        ),
        (
            AI_COMPACT_OPTIONS,
            "AI精简版",
            "model_alpha3_ai.md",
        ),
        (
            ExportOptions(
                layout=ExportLayout.FULL,
                include_source=False,
                include_location=False,
                include_engagement=False,
                date_format=DateFormat.DATE_ONLY,
            ),
            "自定义_完整",
            "model_alpha3_custom_minimal.md",
        ),
    )

    with tempfile.TemporaryDirectory(prefix="weibo_v7_export_") as td:
        output_dir = Path(td)
        for options, suffix, golden_name in cases:
            for existing in output_dir.glob("*.md"):
                existing.unlink()
            expected = (ROOT / "tests/golden" / golden_name).read_text(encoding="utf-8")
            render_archive = (
                replace(archive, fetch_range=FetchRange.trial(20))
                if options.layout is ExportLayout.AI
                else archive
            )
            output, stats = export_markdown(render_archive, output_dir, options, suffix)
            assert output.read_text(encoding="utf-8") == expected
            assert stats["count"] == 3
            expected_range = "测试导出20条" if options.layout is ExportLayout.AI else "测试导出50条"
            assert expected_range in output.name
            assert list(output_dir.glob("*.md")) == [output]


def test_alpha4_incomplete_full_and_ai_goldens():
    archive = build_alpha4_archive()
    cases = (
        (FULL_ARCHIVE_OPTIONS, "完整", "model_alpha4_incomplete_full.md"),
        (AI_COMPACT_OPTIONS, "AI精简版", "model_alpha4_incomplete_ai.md"),
    )
    with tempfile.TemporaryDirectory(prefix="weibo_v7_alpha4_export_") as td:
        output_dir = Path(td)
        for options, suffix, golden_name in cases:
            expected = (ROOT / "tests/golden" / golden_name).read_text(encoding="utf-8")
            render_archive = (
                replace(archive, fetch_range=FetchRange.trial(20))
                if options.layout is ExportLayout.AI
                else archive
            )
            output, stats = export_markdown(render_archive, output_dir, options, suffix)
            rendered = output.read_text(encoding="utf-8")
            assert rendered == expected
            assert stats["count"] == 3
            assert "全文无法验证" in rendered
            assert "列表预览……全文" in rendered


def test_alpha4_ai_incomplete_retweet_dedup_and_empty_preview():
    from weibo_archive import markdown_v5

    archive = build_archive()
    retweet = _as_incomplete(archive.posts[0].retweet, None)
    first = replace(archive.posts[0], id="2001", retweet=retweet)
    second = replace(archive.posts[0], id="2002", retweet=retweet)
    data = archive_to_legacy_data(replace(archive, posts=(first, second)))

    text, _, stats = markdown_v5.build_ai_markdown(
        data,
        archive.profile.id,
        AI_COMPACT_OPTIONS,
    )
    assert text.count("CONTENT=INCOMPLETE") == 1
    assert text.count("PREVIEW_ONLY") == 2  # one rule plus one record marker
    assert text.count(">[PREVIEW_ONLY｜全文无法验证]") == 1
    assert "当前没有可保存的列表预览" in text
    assert ">[=RT1]" in text
    assert stats["unique_retweets"] == 1
    assert stats["duplicate_retweets"] == 1


def test_ai_compact_attribution_and_field_schema():
    from weibo_archive import markdown_v5

    archive = build_alpha3_archive()
    posts = list(archive.posts)
    posts[1] = replace(
        posts[1],
        media=MediaInfo(images=3, videos=2, article=True),
    )
    data = archive_to_legacy_data(replace(archive, posts=tuple(posts)))
    text, _, stats = markdown_v5.build_ai_markdown(
        data,
        archive.profile.id,
        AI_COMPACT_OPTIONS,
    )

    assert "ATTRIBUTION: W = target account's top-level text;" in text
    assert "RT = nested repost source and must not be directly attributed" in text
    assert (
        "MEDIA: I/V/A > 0 = referenced media exists but is not included; "
        "text must not be treated as complete context."
    ) in text
    assert "REFERENCE: =RT* = reference to an already emitted repost source." in text
    assert (
        "INCOMPLETE: PREVIEW_ONLY is an unverified timeline preview "
        "and must not be treated as full text."
    ) in text
    assert "来源字典：S1=" in text
    assert "摘要：共3条" in text
    assert stats["count"] == 3
    assert stats["unique_retweets"] == 1

    lines = text.splitlines()
    rich_top = next(line for line in lines if "P=上海" in line)
    assert rich_top == "[W｜2026-08-12 18:30｜S1｜P=上海｜I3 V2 A1｜R3 C0 L21]"
    repost = next(line for line in lines if line.startswith(">[RT1"))
    assert repost == ">[RT1｜@原作者｜2026-08-10 12:00｜S2｜P=广州｜I1｜R12 C8 L99]"
    assert all(line.startswith("[W｜") for line in lines if line.startswith("[W"))
    assert "｜地=" not in text
    assert "地=发布位置" not in text

    incomplete_data = archive_to_legacy_data(build_alpha4_archive())
    incomplete_text, _, _ = markdown_v5.build_ai_markdown(
        incomplete_data,
        archive.profile.id,
        AI_COMPACT_OPTIONS,
    )
    assert "[W｜2026-08-12 18:30｜S1｜P=上海｜I3｜R3 C0 L21｜CONTENT=INCOMPLETE]" in incomplete_text
    assert ">[RT1｜@原作者｜2026-08-10 12:00｜S2｜P=广州｜I1｜R12 C8 L99｜CONTENT=INCOMPLETE]" in incomplete_text
    assert incomplete_text.count("PREVIEW_ONLY") == 3  # one rule plus two records

    empty_post = replace(archive.posts[1], text="")
    empty_data = archive_to_legacy_data(replace(archive, posts=(empty_post,)))
    empty_text, _, empty_stats = markdown_v5.build_ai_markdown(
        empty_data,
        archive.profile.id,
        AI_COMPACT_OPTIONS,
    )
    assert "[W｜2026-08-12 18:30｜S1｜P=上海｜I3｜R3 C0 L21]" in empty_text
    assert "仅媒体1条" in empty_text
    assert "[PREVIEW_ONLY｜" not in empty_text
    assert empty_stats["media_only_posts"] == 1


def test_export_options_resolution_and_snapshot():
    assert options_for_preset(ExportPreset.FULL_ARCHIVE) == FULL_ARCHIVE_OPTIONS
    assert options_for_preset(ExportPreset.AI_COMPACT) == AI_COMPACT_OPTIONS
    assert filename_suffix_for_selection(
        ExportPreset.FULL_ARCHIVE,
        FULL_ARCHIVE_OPTIONS,
    ) == "完整"
    assert filename_suffix_for_selection(
        ExportPreset.AI_COMPACT,
        AI_COMPACT_OPTIONS,
    ) == "AI精简版"

    custom = ExportOptions(
        layout=ExportLayout.AI,
        include_source=False,
        include_location=True,
        include_engagement=False,
        date_format=DateFormat.DATE_ONLY,
    )
    snapshot = options_for_preset(ExportPreset.CUSTOM, custom)
    assert snapshot is custom
    assert filename_suffix_for_selection(ExportPreset.CUSTOM, snapshot) == "自定义_AI精简"
    assert "preset" not in {field.name for field in fields(ExportOptions)}

    try:
        snapshot.include_source = True
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("ExportOptions is not frozen")

    later_gui_value = replace(custom, include_source=True)
    assert snapshot.include_source is False
    assert later_gui_value.include_source is True


def test_alpha3_field_policy_applies_to_main_and_repost():
    from weibo_archive import markdown_v5

    archive = build_alpha3_archive()
    data = archive_to_legacy_data(archive)
    minimal_full = ExportOptions(
        layout=ExportLayout.FULL,
        include_source=False,
        include_location=False,
        include_engagement=False,
        date_format=DateFormat.DATE_ONLY,
    )
    full_text, _, _ = markdown_v5.build_markdown(data, archive.profile.id, minimal_full)
    for hidden in (
        "iPhone客户端",
        "微博网页版",
        "Android客户端",
        "位置：上海",
        "位置：北京",
        "位置：广州",
        "转 1 · 评 1 · 赞 5",
        "转 12 · 评 8 · 赞 99",
    ):
        assert hidden not in full_text
    assert "- 所在地：北京" in full_text
    assert "## 2026-08-13" in full_text
    assert "日期：2026-08-10" in full_text
    assert "这是转发时写的评论。" in full_text
    assert "这是被转发的原文。" in full_text
    assert "图片×1" in full_text

    minimal_ai = replace(minimal_full, layout=ExportLayout.AI)
    ai_text, _, _ = markdown_v5.build_ai_markdown(data, archive.profile.id, minimal_ai)
    for hidden in (
        "S1",
        "来源字典",
        "来源3种",
        "S*=发布来源",
        "R=转发",
        "R1 C1 L5",
        "R12 C8 L99",
        "｜P=上海",
        "｜P=北京",
        "｜P=广州",
    ):
        assert hidden not in ai_text
    assert "所在地=北京" in ai_text
    assert "[W｜2026-08-13]" in ai_text
    assert "[RT1｜@原作者｜2026-08-10｜I1]" in ai_text
    assert "这是转发时写的评论。" in ai_text
    assert "这是被转发的原文。" in ai_text

    ids_before = [item["id"] for item in markdown_v5.prepare_items(data)]
    date_only_data = archive_to_legacy_data(archive)
    ids_after = [item["id"] for item in markdown_v5.prepare_items(date_only_data)]
    assert ids_after == ids_before


def test_export_does_not_mutate_normalized_archive():
    from weibo_archive import markdown_v5, storage

    archive = build_alpha3_archive()
    before = asdict(archive)
    data_before = archive_to_legacy_data(archive)

    markdown_v5.build_markdown(data_before, archive.profile.id, FULL_ARCHIVE_OPTIONS)
    markdown_v5.build_ai_markdown(data_before, archive.profile.id, AI_COMPACT_OPTIONS)
    assert asdict(archive) == before

    with tempfile.TemporaryDirectory(prefix="weibo_v7_cache_") as td:
        old_cache_dir = storage.CACHE_DIR
        storage.CACHE_DIR = Path(td)
        try:
            cache_path = storage.save_normalized_archive(archive)
        finally:
            storage.CACHE_DIR = old_cache_dir
        payload = json.loads(cache_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["integrity"] == {
        "total_posts": 3,
        "complete_records": 3,
        "incomplete_records": 0,
        "incomplete_top_level": 0,
        "incomplete_retweets": 0,
    }
    assert payload["posts"][0]["content_state"] == "complete"
    assert payload["posts"][0]["text_preview"] is None
    assert payload["posts"][0]["incomplete_reason"] is None
    assert payload["posts"][0]["source"] == "iPhone客户端"
    assert payload["posts"][0]["retweet"]["source"] == "Android客户端"
    assert payload["posts"][0]["retweet"]["location"] == "广州"
    assert payload["posts"][0]["retweet"]["engagement"]["likes"] == 99


def test_alpha4_normalized_cache_contains_only_stable_semantics():
    from weibo_archive import storage

    archive = build_alpha4_archive()
    with tempfile.TemporaryDirectory(prefix="weibo_v7_alpha4_cache_") as td:
        old_cache_dir = storage.CACHE_DIR
        storage.CACHE_DIR = Path(td)
        try:
            cache_path = storage.save_normalized_archive(archive)
        finally:
            storage.CACHE_DIR = old_cache_dir
        raw = cache_path.read_text(encoding="utf-8")
        payload = json.loads(raw)

    assert payload["schema_version"] == 1
    assert payload["integrity"]["incomplete_records"] == 2
    assert payload["posts"][0]["retweet"]["content_state"] == "incomplete"
    assert payload["posts"][0]["retweet"]["text"] is None
    assert payload["posts"][0]["retweet"]["text_preview"].endswith("全文")
    assert payload["posts"][0]["retweet"]["incomplete_reason"] == "content_unavailable"

    keys = set()

    def collect_keys(value):
        if isinstance(value, dict):
            keys.update(value)
            for child in value.values():
                collect_keys(child)
        elif isinstance(value, list):
            for child in value:
                collect_keys(child)

    collect_keys(payload)
    for forbidden_key in (
        "attempts",
        "outcome",
        "body_bytes",
        "json_keys",
        "query",
        "cookie",
        "token",
    ):
        assert forbidden_key not in keys


def test_completion_integrity_warning_text():
    from weibo_archive.app import completion_integrity_lines

    assert completion_integrity_lines(ArchiveIntegrity(3, 3, 0, 0, 0)) == []
    lines = completion_integrity_lines(ArchiveIntegrity(3, 1, 2, 1, 1))
    assert lines == [
        "完整记录：1",
        "不完整记录：2",
        "其中：顶层正文 1 · 转发原文 1",
        "无法验证的内容已在 Markdown 中明确标记。",
    ]


def test_atomic_export_preserves_existing_final_on_cancel_or_failure():
    archive = replace(build_alpha3_archive(), fetch_range=FetchRange.trial(20))

    class SimulatedCancel(Exception):
        pass

    with tempfile.TemporaryDirectory(prefix="weibo_v7_atomic_") as td:
        output_dir = Path(td)
        final = output_dir / "测试用户_1234567890_测试导出20条_完整.md"
        final.write_text("existing user archive", encoding="utf-8")

        def cancel_before_commit():
            raise SimulatedCancel("cancel before atomic replace")

        try:
            export_markdown(
                archive,
                output_dir,
                FULL_ARCHIVE_OPTIONS,
                "完整",
                before_commit=cancel_before_commit,
            )
        except SimulatedCancel:
            pass
        else:
            raise AssertionError("simulated cancellation did not stop commit")

        assert final.read_text(encoding="utf-8") == "existing user archive"
        assert not list(output_dir.glob("*.tmp"))

        def fail_before_commit():
            raise OSError("simulated write failure")

        try:
            export_markdown(
                archive,
                output_dir,
                FULL_ARCHIVE_OPTIONS,
                "完整",
                before_commit=fail_before_commit,
            )
        except OSError:
            pass
        else:
            raise AssertionError("simulated failure did not stop commit")

        assert final.read_text(encoding="utf-8") == "existing user archive"
        assert not list(output_dir.glob("*.tmp"))


def test_safe_system_launcher():
    from weibo_archive.app import launch_with_system

    launched = []
    with tempfile.TemporaryDirectory(prefix="weibo_v7_launch_") as td:
        target = Path(td) / "archive.md"
        target.write_text("ok", encoding="utf-8")
        ok, error = launch_with_system(target, launched.append)
        assert ok and not error
        assert launched == [str(target)]

        target.unlink()
        ok, error = launch_with_system(target, launched.append)
        assert not ok and "不存在" in error

        def broken_launcher(_):
            raise OSError("no association")

        folder = Path(td)
        ok, error = launch_with_system(folder, broken_launcher)
        assert not ok and "无法打开" in error


def test_generation_guard():
    manager = TaskManager()
    manager.transition(TaskState.READY)

    gen1, cancel1 = manager.start(TaskState.FETCHING)
    assert manager.accepts(gen1)
    manager.cancel()
    assert cancel1.is_set()
    assert not manager.accepts(gen1)

    manager.transition(TaskState.READY)
    gen2, _ = manager.start(TaskState.FETCHING)
    assert gen2 != gen1
    assert manager.accepts(gen2)
    assert not manager.accepts(gen1)


def test_redaction():
    secret = "very_secret_value"
    with tempfile.TemporaryDirectory(prefix="weibo_v7_secret_") as td:
        cookie = Path(td) / "cookie.txt"
        cookie.write_text(
            f"SUB={secret}; SUBP=another_secret; SSOLoginState=12345",
            encoding="utf-8",
        )
        text = (
            f"SUB={secret}; SUBP=another_secret "
            "https://example.invalid/?alt=one_time_token&x=1 "
            "SSOLoginState=12345"
        )
        safe = redact_text(text, cookie)
        for value in (secret, "another_secret", "one_time_token", "12345"):
            assert value not in safe


def test_dependency_audit():
    forbidden_imports = {"PIL", "requests", "playwright", "selenium", "subprocess"}
    bad_imports = []

    for path in (ROOT / "weibo_archive").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "verify=False" not in source
        assert "ssl._create_unverified_context" not in source
        assert "master.zip" not in source
        assert "pip install" not in source
        assert "github.com/dataabc" not in source

        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
            else:
                continue
            for name in names & forbidden_imports:
                bad_imports.append((path.name, name))

    if bad_imports:
        raise AssertionError(f"forbidden runtime imports: {bad_imports}")


def test_alpha4_recovery_and_diagnostic_layer_guards():
    runtime_files = list((ROOT / "weibo_archive").glob("*.py"))
    for path in runtime_files:
        source = path.read_text(encoding="utf-8")
        assert "except IncompleteContent" not in source
        if path.name == "client.py":
            assert source.count("except ContentUnavailable") == 2
        else:
            assert "except ContentUnavailable" not in source

    product_layers = (
        "models.py",
        "parser.py",
        "storage.py",
        "exporter.py",
        "markdown_v5.py",
        "app.py",
    )
    diagnostic_words = (
        "no_view_permission_html",
        "challenge_html",
        "login_html",
        "timeout",
    )
    for name in product_layers:
        source = (ROOT / "weibo_archive" / name).read_text(encoding="utf-8")
        for word in diagnostic_words:
            assert word not in source, f"{word} leaked into {name}"



def test_range_semantics():
    from datetime import date
    from weibo_archive.app import TEST_EXPORT_LIMIT

    all_range = FetchRange.all()
    assert all_range.mode is RangeMode.ALL

    recent = FetchRange.recent(1000)
    assert recent.mode is RangeMode.RECENT
    assert recent.limit == 1000
    assert recent.label() == "最近1000条"

    trial = FetchRange.trial(TEST_EXPORT_LIMIT)
    assert trial.mode is RangeMode.TRIAL
    assert trial.limit == 20
    assert trial.label() == "测试导出20条"
    assert FetchRange.trial() == trial

    since = FetchRange.since_date(date(2024, 1, 1))
    assert since.mode is RangeMode.SINCE
    assert since.since.isoformat() == "2024-01-01"
    assert since.label() == "2024-01-01起"


def test_no_raw_api_escape_hatch():
    from dataclasses import fields
    from weibo_archive.models import UserProfile, MediaInfo, Engagement

    for model in (Post, UserProfile, MediaInfo, Engagement):
        names = {f.name for f in fields(model)}
        assert "raw" not in names
        assert "payload" not in names
        assert "json" not in names



def _fake_mblog(post_id: int, created: str, *, pinned: bool = False):
    raw = {
        "id": str(post_id),
        "bid": f"B{post_id}",
        "created_at": created,
        "text": f"post-{post_id}",
        "source": "fixture",
        "reposts_count": 0,
        "comments_count": 0,
        "attitudes_count": 0,
        "user": {"id": 1234567890, "screen_name": "测试用户"},
    }
    if pinned:
        raw["mblogtype"] = 2
    return raw


def test_incomplete_posts_continue_current_and_next_page():
    import threading

    class FakeClient(WeiboClient):
        def __init__(self):
            self.cancel_event = threading.Event()
            self.progress = lambda *args, **kwargs: None
            self.posts_since_batch_rest = 0
            self.posts_since_session_rest = 0
            self.http = type("H", (), {"request_count": 0})()
            self.pages = []

        def preheat(self):
            pass

        def fetch_profile(self, uid):
            return UserProfile(id=uid, screen_name="测试用户", statuses_count=3)

        def _rest_if_needed(self):
            pass

        def _hydrate_long_texts(self, raw):
            if raw["id"] == "1":
                return HydrationOutcome(
                    raw,
                    retweet_incomplete_reason=IncompleteReason.CONTENT_UNAVAILABLE,
                )
            if raw["id"] == "2":
                return HydrationOutcome(
                    raw,
                    top_incomplete_reason=IncompleteReason.CONTENT_UNAVAILABLE,
                )
            return HydrationOutcome(raw)

        def _timeline_page(self, uid, page):
            self.pages.append(page)
            if page == 1:
                first = _fake_mblog(1, "2026-08-13 10:00:00")
                first["retweeted_status"] = {
                    "id": "901",
                    "text": "转发列表预览……全文",
                    "user": {"screen_name": "原作者"},
                }
                return [first, _fake_mblog(2, "2026-08-13 09:00:00")], False
            if page == 2:
                return [_fake_mblog(3, "2026-08-13 08:00:00")], False
            return [], True

    client = FakeClient()
    archive = client.fetch("1234567890", FetchRange.all())
    assert client.pages == [1, 2, 3]
    assert len(archive.posts) == 3
    by_id = {post.id: post for post in archive.posts}
    assert by_id["1"].content_state is ContentState.COMPLETE
    assert by_id["1"].text == "post-1"
    assert by_id["1"].retweet.content_state is ContentState.INCOMPLETE
    assert by_id["2"].content_state is ContentState.INCOMPLETE
    assert by_id["3"].content_state is ContentState.COMPLETE
    assert archive.integrity == ArchiveIntegrity(3, 1, 2, 1, 1)


def test_client_recent_range_excludes_pinned_old():
    import threading
    from datetime import datetime, timedelta
    from weibo_archive.client import WeiboClient
    from weibo_archive.models import UserProfile

    class FakeClient(WeiboClient):
        def __init__(self):
            self.cancel_event = threading.Event()
            self.progress = lambda *args, **kwargs: None
            self.posts_since_batch_rest = 0
            self.posts_since_session_rest = 0
            self.http = type("H", (), {"request_count": 0})()

        def preheat(self):
            pass

        def fetch_profile(self, uid):
            return UserProfile(id=uid, screen_name="测试用户", statuses_count=100)

        def _wait_random(self, low, high):
            pass

        def _rest_if_needed(self):
            pass

        def _hydrate_long_texts(self, raw):
            return HydrationOutcome(raw)

        def _timeline_page(self, uid, page):
            base = datetime(2026, 8, 13, 12, 0)
            if page == 1:
                # One deliberately old pinned-looking item mixed into the first page.
                rows = [_fake_mblog(999999, "2018-01-01 00:00:00", pinned=True)]
                rows += [
                    _fake_mblog(200000-i, (base - timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"))
                    for i in range(19)
                ]
                return rows, False
            if page == 2:
                rows = [
                    _fake_mblog(199981-i, (base - timedelta(minutes=19+i)).strftime("%Y-%m-%d %H:%M:%S"))
                    for i in range(20)
                ]
                return rows, False
            return [], True

    archive = FakeClient().fetch("1234567890", FetchRange.recent(5))
    assert len(archive.posts) == 5
    assert all(p.created_at.year == 2026 for p in archive.posts)
    assert "999999" not in {p.id for p in archive.posts}


def test_trial_stops_on_50_timeline_candidates_and_ignores_pinned_frontier():
    import threading
    from datetime import datetime, timedelta
    from weibo_archive.client import WeiboClient
    from weibo_archive.models import UserProfile

    class FakeClient(WeiboClient):
        def __init__(self):
            self.cancel_event = threading.Event()
            self.events = []
            self.progress = lambda message, data=None: self.events.append((message, data or {}))
            self.posts_since_batch_rest = 0
            self.posts_since_session_rest = 0
            self.http = type("H", (), {"request_count": 0})()
            self.pages = []
            self.hydrated_ids = []

        def preheat(self):
            pass

        def fetch_profile(self, uid):
            return UserProfile(id=uid, screen_name="测试用户", statuses_count=100)

        def _rest_if_needed(self):
            pass

        def _hydrate_long_texts(self, raw):
            self.hydrated_ids.append(str(raw["id"]))
            return HydrationOutcome(raw)

        def _timeline_page(self, uid, page):
            self.pages.append(page)
            base = datetime(2026, 8, 11, 12, 0)
            if page == 1:
                return [
                    _fake_mblog(900001, "2025-11-08 02:32:08", pinned=True),
                    _fake_mblog(900002, "2025-03-11 22:04:29", pinned=True),
                    *[
                        _fake_mblog(
                            300000 - i,
                            (base - timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
                        )
                        for i in range(20)
                    ],
                ], False
            if page == 2:
                return [
                    _fake_mblog(
                        299980 - i,
                        (base - timedelta(minutes=20 + i)).strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    for i in range(20)
                ], False
            if page == 3:
                return [
                    _fake_mblog(
                        299960 - i,
                        (base - timedelta(minutes=40 + i)).strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    for i in range(20)
                ], False
            raise AssertionError("trial fetch requested an unnecessary fourth page")

    client = FakeClient()
    archive = client.fetch("1234567890", FetchRange.trial(50))

    assert client.pages == [1, 2, 3]
    assert len(client.hydrated_ids) == 52  # two pinned records plus 50 timeline candidates
    assert len(archive.posts) == 50
    assert archive.report.pages_fetched == 3
    assert archive.report.termination is Termination.TARGET_COUNT
    assert {"900001", "900002"}.isdisjoint({post.id for post in archive.posts})

    page_one = next(
        (message, data)
        for message, data in client.events
        if message.startswith("已获得 20 条时间线候选")
    )
    assert "2025-03-11" not in page_one[0]
    assert page_one[1]["posts"] == 20
    assert page_one[1]["count_label"] == "条候选"
    assert page_one[1]["frontier"].startswith("2026-08-11")

    final_progress = next(
        (message, data)
        for message, data in reversed(client.events)
        if message.startswith("已获得")
    )
    assert final_progress[0].startswith("已获得 50 条时间线候选")
    assert final_progress[1]["posts"] == 50


def test_log_expansion_height_is_clamped_to_work_area():
    from weibo_archive.app import (
        _compact_window_height,
        _fit_expanded_height,
        _geometry_spec,
    )

    assert _compact_window_height(760, 771) == 771
    assert _compact_window_height(760, 740) == 760

    height, y = _fit_expanded_height(760, 1000, 100, 0, 1040)
    assert (height, y) == (1000, 40)

    height, y = _fit_expanded_height(760, 1300, 100, 0, 1040)
    assert (height, y) == (1040, 0)
    assert _geometry_spec(860, height, -1920, y) == "860x1040-1920+0"


def test_child_window_centering_and_work_area_clamp():
    from weibo_archive.app import _centered_child_geometry, _geometry_spec

    geometry = _centered_child_geometry(
        100, 100, 860, 760,
        400, 300,
        0, 0, 1920, 1040,
    )
    assert geometry == (400, 300, 330, 330)

    geometry = _centered_child_geometry(
        -1700, 100, 860, 760,
        400, 300,
        -1920, 0, 0, 1040,
    )
    assert geometry == (400, 300, -1470, 330)
    assert _geometry_spec(*geometry) == "400x300-1470+330"

    geometry = _centered_child_geometry(
        1700, 900, 300, 200,
        500, 300,
        0, 0, 1920, 1040,
    )
    assert geometry == (500, 300, 1420, 740)

    geometry = _centered_child_geometry(
        100, 100, 860, 760,
        2200, 1200,
        0, 0, 1920, 1040,
    )
    assert geometry == (1920, 1040, 0, 0)


def test_client_since_range_requires_two_old_pages():
    import threading
    from datetime import datetime
    from weibo_archive.client import WeiboClient
    from weibo_archive.models import UserProfile

    class FakeClient(WeiboClient):
        def __init__(self):
            self.cancel_event = threading.Event()
            self.progress = lambda *args, **kwargs: None
            self.posts_since_batch_rest = 0
            self.posts_since_session_rest = 0
            self.http = type("H", (), {"request_count": 0})()

        def preheat(self):
            pass

        def fetch_profile(self, uid):
            return UserProfile(id=uid, screen_name="测试用户", statuses_count=100)

        def _wait_random(self, low, high):
            pass

        def _rest_if_needed(self):
            pass

        def _hydrate_long_texts(self, raw):
            return HydrationOutcome(raw)

        def _timeline_page(self, uid, page):
            if page == 1:
                # Mixed page: one old pinned item must not stop the crawl.
                return [
                    _fake_mblog(1, "2018-01-01 00:00:00"),
                    _fake_mblog(2, "2025-06-01 00:00:00"),
                    _fake_mblog(3, "2024-02-01 00:00:00"),
                ], False
            if page == 2:
                return [
                    _fake_mblog(4, "2023-12-20 00:00:00"),
                    _fake_mblog(5, "2023-11-20 00:00:00"),
                ], False
            if page == 3:
                return [
                    _fake_mblog(6, "2023-10-20 00:00:00"),
                    _fake_mblog(7, "2023-09-20 00:00:00"),
                ], False
            return [], True

    from datetime import date
    archive = FakeClient().fetch(
        "1234567890",
        FetchRange.since_date(date(2024, 1, 1)),
    )
    ids = {p.id for p in archive.posts}
    assert ids == {"2", "3"}
    assert archive.report.termination is Termination.SINCE_REACHED
    assert archive.report.pages_fetched == 3


def test_ambiguous_empty_page_fails_closed():
    import threading
    from weibo_archive.client import WeiboClient
    from weibo_archive.network import InvalidResponse

    client = WeiboClient(
        cookie_header="SUB=fake",
        cancel_event=threading.Event(),
        progress=lambda *args, **kwargs: None,
    )
    client._wait_random = lambda *args: None
    client.http.json = lambda *args, **kwargs: {
        "ok": 1,
        "data": {
            "cards": [
                {"card_type": 8, "title": "unexpected layout"}
            ]
        },
    }

    try:
        client._timeline_page("1234567890", 99)
    except InvalidResponse:
        pass
    else:
        raise AssertionError("ambiguous non-empty cards were treated as natural end")



def test_unknown_ok0_is_not_natural_end():
    import threading
    from weibo_archive.client import WeiboClient
    from weibo_archive.network import RateLimited

    client = WeiboClient(
        cookie_header="SUB=fake",
        cancel_event=threading.Event(),
        progress=lambda *args, **kwargs: None,
    )
    client._wait_random = lambda *args: None
    client.http.json = lambda *args, **kwargs: {
        "ok": 0,
        "msg": "访问频繁，请稍后再试",
    }

    try:
        client._timeline_page("1234567890", 99)
    except RateLimited:
        pass
    else:
        raise AssertionError("limited ok=0 response was treated as natural end")


def test_since_unknown_date_cannot_trigger_early_stop():
    import threading
    from datetime import date
    from weibo_archive.client import WeiboClient
    from weibo_archive.models import UserProfile

    class FakeClient(WeiboClient):
        def __init__(self):
            self.cancel_event = threading.Event()
            self.progress = lambda *args, **kwargs: None
            self.posts_since_batch_rest = 0
            self.posts_since_session_rest = 0
            self.http = type("H", (), {"request_count": 0})()

        def preheat(self):
            pass

        def fetch_profile(self, uid):
            return UserProfile(id=uid, screen_name="测试用户", statuses_count=100)

        def _wait_random(self, low, high):
            pass

        def _rest_if_needed(self):
            pass

        def _hydrate_long_texts(self, raw):
            return HydrationOutcome(raw)

        def _timeline_page(self, uid, page):
            if page == 1:
                unknown = _fake_mblog(88, "2023-12-20 00:00:00")
                unknown["created_at"] = "UNRECOGNIZED-DATE-FORMAT"
                return [
                    _fake_mblog(87, "2023-12-21 00:00:00"),
                    unknown,
                ], False
            if page == 2:
                return [_fake_mblog(89, "2023-11-20 00:00:00")], False
            if page == 3:
                # If page 1 had incorrectly counted as "wholly old", the crawler
                # would stop before reaching this newer post.
                return [_fake_mblog(90, "2024-02-20 00:00:00")], False
            return [], True

    archive = FakeClient().fetch(
        "1234567890",
        FetchRange.since_date(date(2024, 1, 1)),
    )
    ids = {p.id for p in archive.posts}
    assert "90" in ids
    assert "88" in ids   # Unknown date is retained rather than silently discarded.
    assert "87" not in ids
    assert "89" not in ids
    assert archive.report.termination is Termination.NATURAL

def main():
    suite = [
        ("startup import", test_startup_import),
        ("Alpha4 version and no-console GUI launcher", test_alpha4_version_and_gui_launcher),
        ("Windows preview packaging contract", test_windows_preview_packaging_contract),
        ("raw parser contract", test_parser_contract),
        ("frozen model boundary", test_model_boundary),
        ("Alpha4 Post invariants and integrity", test_alpha4_post_invariants_and_integrity_combinations),
        ("Alpha4 parser explicit incomplete reasons", test_alpha4_parser_explicit_reasons_and_raw_immutability),
        ("long-text detail decoder", test_longtext_detail_decoder),
        ("non-JSON diagnostics contain no body/query", test_network_non_json_diagnostic_has_no_body_or_query),
        ("non-JSON classifier safety priority", test_non_json_classifier_prioritizes_challenge_and_login),
        ("long-text permission failure diagnostics", test_longtext_permission_failure_preserves_two_safe_attempts),
        ("long-text mixed outcomes remain fatal", test_longtext_mixed_or_challenge_outcomes_remain_fatal),
        ("long-text cancellation remains cancellation", test_longtext_cancelled_is_not_converted_to_incomplete),
        ("long-text unavailable negative cache", test_unavailable_negative_cache_is_deterministic),
        ("top and retweet hydration independence", test_top_and_retweet_hydration_states_are_independent),
        ("post-parse hydration safety validation", test_runtime_post_parse_validation_fails_closed),
        ("long-text global safety fuse", test_longtext_global_safety_fuse_thresholds_and_unique_ids),
        ("long-text detail fallback", test_longtext_detail_fallback_success_after_extend_schema_failure),
        ("long-text extend response shapes", test_longtext_extend_supported_shapes_do_not_call_detail),
        ("long-text detail ID mismatch", test_longtext_detail_id_mismatch_fails_closed),
        ("long-text unknown detail schema", test_longtext_unknown_detail_schema_fails_closed),
        ("legacy exporter goldens", test_exporter_golden),
        ("Alpha3 exporter goldens and single output", test_alpha3_exporter_goldens_and_single_output),
        ("Alpha4 incomplete exporter goldens", test_alpha4_incomplete_full_and_ai_goldens),
        ("Alpha4 AI incomplete retweet dedup", test_alpha4_ai_incomplete_retweet_dedup_and_empty_preview),
        ("0.4.2 AI Compact attribution schema", test_ai_compact_attribution_and_field_schema),
        ("Alpha3 options resolver and frozen snapshot", test_export_options_resolution_and_snapshot),
        ("Alpha3 document-wide field policy", test_alpha3_field_policy_applies_to_main_and_repost),
        ("Alpha3 normalized archive independence", test_export_does_not_mutate_normalized_archive),
        ("Alpha4 normalized cache semantics", test_alpha4_normalized_cache_contains_only_stable_semantics),
        ("Alpha4 completion integrity warning", test_completion_integrity_warning_text),
        ("Alpha3 atomic cancellation safety", test_atomic_export_preserves_existing_final_on_cancel_or_failure),
        ("Alpha3 Windows launcher helper", test_safe_system_launcher),
        ("generation guard", test_generation_guard),
        ("range semantics", test_range_semantics),
        ("no raw API escape hatch", test_no_raw_api_escape_hatch),
        ("Alpha4 recovery layer guards", test_alpha4_recovery_and_diagnostic_layer_guards),
        ("incomplete posts continue pagination", test_incomplete_posts_continue_current_and_next_page),
        ("recent range pinned-post guard", test_client_recent_range_excludes_pinned_old),
        ("trial target and pinned frontier", test_trial_stops_on_50_timeline_candidates_and_ignores_pinned_frontier),
        ("log expansion work-area clamp", test_log_expansion_height_is_clamped_to_work_area),
        ("child window centering and work-area clamp", test_child_window_centering_and_work_area_clamp),
        ("since-date two-page guard", test_client_since_range_requires_two_old_pages),
        ("ambiguous empty page fails closed", test_ambiguous_empty_page_fails_closed),
        ("unknown ok=0 fails closed", test_unknown_ok0_is_not_natural_end),
        ("since-date unknown timestamp guard", test_since_unknown_date_cannot_trigger_early_stop),
        ("security redaction", test_redaction),
        ("zero-runtime-dependency audit", test_dependency_audit),
    ]
    for name, fn in suite:
        fn()
        print(f"[PASS] {name}")
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
