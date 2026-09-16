"""Offline Tk/controller integration. Uses synthetic identities and no network."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from weibo_archive import app as app_module
from weibo_archive import image_ui as ui
from weibo_archive.credentials import CredentialUnavailable
from weibo_archive.image_models import ImageBackupResult, ImageResultState
from weibo_archive.models import FetchRange, RangeMode
from weibo_archive.tasking import TaskState

ROOT = Path(__file__).resolve().parents[1]


def outcome(state=ImageResultState.COMPLETE, **kw):
    r = ImageBackupResult(state, 20, 8, 5, 3, 0, 0, 0, 0, 0)
    r = replace(r, **kw)
    return ui.ImageOutcome(r, ui.ImageProgress(posts=20, slots=8, declared=8, discovered=8,
        saved=r.saved, already_present=r.already_present, unavailable=r.unavailable, failed=r.failed, gap=r.enumeration_gap))


class PureTests(unittest.TestCase):
    def request(self, **kw):
        fields = dict(target="123456", mode="trial", recent="1000", since="", output=tempfile.gettempdir())
        fields.update(kw)
        return ui.resolve_request(**fields)

    def test_recent_validation(self):
        for value in ("0", "-1", "50001", "abc"):
            with self.assertRaises(ValueError): self.request(mode="recent", recent=value)
        self.assertEqual(self.request(mode="recent", recent="7").fetch_range, FetchRange.recent(7))

    def test_since_validation(self):
        for value in ("bad", "2025-02-30", (date.today() + timedelta(days=1)).isoformat()):
            with self.assertRaises(ValueError): self.request(mode="since", since=value)
        self.assertEqual(self.request(mode="since", since="2024-01-01").fetch_range.since, date(2024, 1, 1))

    def test_immutable_request(self):
        r = self.request()
        with self.assertRaises(FrozenInstanceError): r.target_uid = "654321"
        self.assertEqual(r.backup_root, r.output_base / "WeiboImages_123456")

    def test_homepage_and_post_guard(self):
        self.assertEqual(self.request(target="https://weibo.com/u/123456").target_uid, "123456")
        for value in ("https://m.weibo.cn/detail/123456", "weibo.com/status/123456"):
            with self.assertRaisesRegex(ValueError, "不是单条微博"): self.request(target=value)

    def test_complete_truthful(self):
        title, counts, note = ui.result_presentation(outcome())
        self.assertEqual(title, "图片备份完成")
        self.assertIn("本次接口明确返回", note)
        self.assertNotIn("全部图片", note)

    def test_partial_gap(self):
        title, counts, note = ui.result_presentation(outcome(ImageResultState.PARTIAL, enumeration_gap=4, failed=1))
        self.assertEqual(title, "图片备份完成，但存在未保存项目")
        self.assertIn("枚举缺口：4", counts)
        self.assertIn(ui.GAP_MESSAGE, note)

    def test_cancel_preserves_message(self):
        self.assertIn("已经成功保存的图片和进度记录会保留", ui.result_presentation(outcome(ImageResultState.CANCELLED))[2])

    def test_failed_sanitized_no_url_or_text(self):
        title, counts, note = ui.result_presentation(outcome(ImageResultState.FAILED, failure_reason="https://secret.test/PRIVATE_POST_TEXT"))
        self.assertEqual(title, "图片备份未完成")
        self.assertNotIn("https", note)
        self.assertNotIn("PRIVATE_POST_TEXT", note)

    def test_target_mismatch_message(self):
        self.assertIn("账号与当前目标不一致", ui.failure_message("target_mismatch"))

    def test_auth_expiry_not_unavailable(self):
        result = outcome(ImageResultState.PARTIAL, failure_reason="authentication_expired")
        self.assertIn("主窗口重新扫码", ui.result_presentation(result)[2])
        self.assertEqual(result.result.unavailable, 0)

    def test_missing_credentials_worker_stops(self):
        with patch.object(ui, "load_cookie_header", side_effect=CredentialUnavailable("PRIVATE")), patch.object(ui, "ImageBackup") as core:
            result = ui.run_image_request(self.request(), threading.Event(), lambda p: None)
        self.assertEqual(result.result.failure_reason, "login_required")
        core.assert_not_called()

    def test_real_worker_adapters_use_image_core_only(self):
        from test_image_core import Opener, Response, raw
        from weibo_archive.image_downloader import ImageDownloader
        opener = Opener(Response())
        original_init = ImageDownloader.__init__
        def configure(d, **kw):
            original_init(d, opener=opener, spacing=0, reserve=0, **kw)
        with tempfile.TemporaryDirectory() as folder:
            request = self.request(output=folder, mode="recent", recent="1")
            progress = []
            with patch.object(ui, "load_cookie_header", return_value="SUB=offline"), \
                 patch.object(ui.ImageTimelineClient, "timeline_page", return_value=([raw(123456, user={"id": "123456"})], False)), \
                 patch.object(ImageDownloader, "__init__", configure), \
                 patch.object(app_module.WeiboClient, "fetch", side_effect=AssertionError("text fetch")):
                first = ui.run_image_request(request, threading.Event(), progress.append)
                second = ui.run_image_request(request, threading.Event(), progress.append)
            self.assertEqual(first.result.saved, 1)
            self.assertEqual(second.result.already_present, 1)
            self.assertEqual(len(opener.requests), 1)
            self.assertTrue(any(p.slots == 1 for p in progress))
            self.assertFalse(list(Path(folder).rglob("*.md")))
            self.assertNotIn("https", repr(progress))


class ControllerTests(unittest.TestCase):
    def request(self):
        return ui.resolve_request("123456", "trial", "1000", "", tempfile.gettempdir())

    def finish(self, controller):
        controller.worker.join(3)
        self.assertFalse(controller.worker.is_alive())
        return controller.drain()

    def test_worker_off_main_thread(self):
        threads = []
        c = ui.ImageTaskController(lambda r,e,p: (threads.append(threading.get_ident()), outcome())[1])
        c.start(self.request())
        self.finish(c)
        self.assertNotEqual(threads[0], threading.get_ident())

    def test_stale_generation_ignored(self):
        c = ui.ImageTaskController(lambda r,e,p: outcome())
        c.start(self.request())
        self.finish(c)
        c._publish(0, "progress", ui.ImageProgress(posts=999))
        self.assertEqual(c.drain(), [])

    def test_cancel_reaches_core(self):
        seen = []
        def runner(r, event, publish):
            event.wait(3)
            seen.append(event.is_set())
            return outcome(ImageResultState.CANCELLED)
        c = ui.ImageTaskController(runner)
        c.start(self.request()); c.cancel()
        events = self.finish(c)
        self.assertEqual(seen, [True])
        self.assertEqual(events[-1][1], "cancelled")

    def test_exactly_one_terminal(self):
        def runner(r,e,p):
            for n in range(10000): p(ui.ImageProgress(posts=n))
            return outcome()
        c = ui.ImageTaskController(runner)
        c.start(self.request())
        events = self.finish(c)
        self.assertEqual(sum(k != "progress" for g,k,p in events), 1)
        self.assertLessEqual(len(events), 2)
        self.assertEqual(c.drain(), [])

    def test_exception_single_safe_terminal(self):
        def runner(r,e,p): raise RuntimeError("SECRET_URL PRIVATE_POST_TEXT")
        c = ui.ImageTaskController(runner)
        c.start(self.request())
        events = self.finish(c)
        self.assertEqual(len(events), 1)
        self.assertNotIn("SECRET", repr(events))

    def test_malformed_result_still_ends_once(self):
        c = ui.ImageTaskController(lambda r,e,p: None)
        c.start(self.request())
        events = self.finish(c)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1], "error")
        self.assertFalse(c.active)

    def test_shutdown_discards_late_events(self):
        gate = threading.Event()
        def runner(r,e,p):
            gate.wait(2); p(ui.ImageProgress(posts=900)); return outcome()
        c = ui.ImageTaskController(runner)
        c.start(self.request()); c.shutdown(); gate.set()
        self.assertEqual(self.finish(c), [])

    def test_ownership_until_thread_exits(self):
        gate = threading.Event()
        def runner(r,e,p): gate.wait(2); return outcome()
        c = ui.ImageTaskController(runner)
        c.start(self.request()); c.cancel()
        self.assertTrue(c.active)
        with self.assertRaises(RuntimeError): c.start(self.request())
        gate.set(); self.finish(c)
        self.assertFalse(c.active)


class TkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patches = [patch.object(app_module, "has_saved_login", return_value=False),
                        patch.object(app_module.App, "_start_update_check"),
                        patch.object(ui, "has_saved_login", return_value=True),
                        patch.object(ui.messagebox, "showinfo"), patch.object(ui.messagebox, "showwarning"),
                        patch.object(ui.messagebox, "askyesno", return_value=True)]
        for p in self.patches: p.start(); self.addCleanup(p.stop)
        self.app = app_module.App()
        self.app.withdraw()
        self.app.uid_var.set("123456")
        self.addCleanup(self.app.destroy)
        self.win = ui.open_image_window(self.app)
        self.win.output_var.set(self.tmp.name)

    def pump(self):
        deadline = time.monotonic() + 3
        while self.win.controller.active and time.monotonic() < deadline:
            self.app.update(); time.sleep(.01)
        self.app.update()
        self.assertFalse(self.win.controller.active)

    def test_window_opens_once(self):
        self.assertIs(ui.open_image_window(self.app), self.win)
        self.assertEqual(len([c for c in self.app.winfo_children() if isinstance(c, ui.ImageBackupWindow)]), 1)

    def test_reopen_focus_lift(self):
        with patch.object(self.win, "lift") as lift, patch.object(self.win, "focus_set") as focus:
            ui.open_image_window(self.app)
        lift.assert_called_once(); focus.assert_called_once()

    def test_valid_target_prefill(self):
        self.assertEqual(self.win.target_var.get(), "123456")

    def test_independent_edit(self):
        self.win.target_var.set("654321")
        self.assertEqual(self.app.uid_var.get(), "123456")

    def test_default_trial_20(self):
        self.assertEqual(self.win.range_var.get(), "trial")
        self.assertEqual(ui.resolve_request("123456", self.win.range_var.get(), "", "", self.tmp.name).fetch_range, FetchRange.trial(20))

    def test_all_confirmation_once(self):
        self.win.range_var.set("all")
        self.win.controller.runner = lambda r,e,p: outcome()
        self.win.start(); self.pump()
        ui.messagebox.askyesno.assert_called_once()
        self.assertEqual(ui.messagebox.askyesno.call_args.args[1], ui.FULL_CONFIRM)

    def test_trial_no_confirmation(self):
        self.win.controller.runner = lambda r,e,p: outcome()
        self.win.start(); self.pump()
        ui.messagebox.askyesno.assert_not_called()

    def test_no_login_prevents_start(self):
        with patch.object(ui, "has_saved_login", return_value=False): self.win.start()
        self.assertIsNone(self.win.controller.worker)
        self.assertIn(ui.NO_LOGIN, str(ui.messagebox.showinfo.call_args))

    def test_image_blocks_text_and_login(self):
        gate = threading.Event()
        self.win.controller.runner = lambda r,e,p: (gate.wait(2), outcome())[1]
        self.win.start()
        try:
            self.assertEqual(str(self.app.export_btn.cget("state")), "disabled")
            self.assertEqual(str(self.app.login_btn.cget("state")), "disabled")
            with patch.object(self.app, "_launch_export_request") as text, patch.object(self.app, "_open_qr_window") as login:
                self.app.start_export(trial=True); self.app.start_login()
                text.assert_not_called(); login.assert_not_called()
        finally:
            gate.set(); self.pump()
        self.assertEqual(str(self.app.export_btn.cget("state")), "normal")

    def test_text_blocks_image(self):
        self.app.tasks.start(TaskState.FETCHING)
        self.win.start()
        self.assertIsNone(self.win.controller.worker)
        self.assertIn(ui.BUSY, str(ui.messagebox.showinfo.call_args))
        self.app.tasks.cancel()

    def test_draining_text_blocks_image(self):
        gate = threading.Event()
        worker = threading.Thread(target=lambda: gate.wait(2))
        worker.start()
        self.app._image_text_draining = [worker]
        try:
            self.win.start()
            self.assertIsNone(self.win.controller.worker)
        finally: gate.set(); worker.join()

    def test_request_fields_frozen_during_worker(self):
        gate = threading.Event(); requests = []
        def runner(r,e,p): requests.append(r); gate.wait(2); return outcome()
        self.win.controller.runner = runner
        self.win.start(); self.win.target_var.set("654321")
        gate.set(); self.pump()
        self.assertEqual(requests[0].target_uid, "123456")

    def test_stale_event_cannot_change_widgets(self):
        before = self.win.status_var.get()
        self.win._handle_event(-1, "done", outcome())
        self.assertEqual(self.win.status_var.get(), before)

    def test_open_folder_only_explicit_local_action(self):
        self.win.controller.runner = lambda r,e,p: outcome()
        with patch.object(app_module, "launch_with_system", return_value=(True, "")) as launch:
            self.win.start(); self.pump(); launch.assert_not_called()
            self.win.open_btn.invoke()
            launch.assert_called_once_with(Path(self.tmp.name).resolve() / "WeiboImages_123456")

    def test_existing_manifest_no_confirmation(self):
        folder = Path(self.tmp.name) / "WeiboImages_123456"
        folder.mkdir(); (folder / "manifest.json").write_text("{}")
        self.win.controller.runner = lambda r,e,p: outcome()
        self.win.start(); self.pump()
        ui.messagebox.askyesno.assert_not_called()

    def test_close_active_waits_cancel(self):
        gate = threading.Event()
        def runner(r,e,p):
            e.wait(2); gate.wait(2); return outcome(ImageResultState.CANCELLED)
        self.win.controller.runner = runner
        self.win.start(); self.win.request_close()
        self.assertTrue(self.win.winfo_exists())
        self.assertTrue(self.win.controller.cancel_event.is_set())
        self.assertTrue(ui.image_task_active(self.app))
        gate.set(); self.pump()
        self.assertFalse(self.win.winfo_exists())

    def test_indicator_reused_not_redesigned(self):
        self.assertIsInstance(self.win.activity, app_module.ActivityIndicator)
        self.assertIsNot(self.win.activity, self.app.activity)
        self.assertEqual(self.win.activity._DOT_X, self.app.activity._DOT_X)

    def test_default_save_base_matches_text(self):
        self.win.shutdown()
        w = ui.open_image_window(self.app)
        self.assertEqual(Path(w.output_var.get()), self.app.default_output_dir)

    def test_text_status_unchanged_by_image_task(self):
        before = self.app.status_var.get(), self.app.progress_detail_var.get(), self.app.activity._running
        self.win.controller.runner = lambda r,e,p: outcome()
        self.win.start(); self.pump()
        self.assertEqual((self.app.status_var.get(), self.app.progress_detail_var.get(), self.app.activity._running), before)


class FrozenTextTests(unittest.TestCase):
    def baseline(self, path):
        return subprocess.check_output(["git", "show", "f45919b:" + path], cwd=ROOT).decode("utf-8")

    def test_existing_functions_and_layout_unchanged_except_entry(self):
        old = ast.parse(self.baseline("weibo_archive/app.py"))
        new = ast.parse((ROOT / "weibo_archive/app.py").read_text(encoding="utf-8"))
        def cls(tree,name): return next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
        self.assertEqual(ast.dump(cls(old,"ActivityIndicator")), ast.dump(cls(new,"ActivityIndicator")))
        old_methods = {n.name:n for n in cls(old,"App").body if isinstance(n,ast.FunctionDef)}
        new_methods = {n.name:n for n in cls(new,"App").body if isinstance(n,ast.FunctionDef)}
        for name in ("_configure_style", "_selected_range", "_export_worker", "_show_completion", "_set_running", "_poll_events"):
            self.assertEqual(ast.dump(old_methods[name]), ast.dump(new_methods[name]), name)
        build = new_methods["_build_ui"]
        build.body = [n for n in build.body if "image_backup_btn" not in ast.unparse(n)]
        self.assertEqual(ast.dump(old_methods["_build_ui"]), ast.dump(build))

    def test_release_assertions_only_and_goldens_unchanged(self):
        paths = ["tests/run_tests.py", *[p.relative_to(ROOT).as_posix() for p in (ROOT/"tests/golden").glob("*")]]
        for path in paths:
            current = (ROOT/path).read_text(encoding="utf-8")
            if path == "tests/run_tests.py":
                # Release-only expected-version changes; all other text tests
                # remain byte-identical to the accepted text baseline.
                for old, new in (
                    ('assert __version__ == "0.5.7"', 'assert __version__ == "0.6.0"'),
                    ('assert VERSION_DISPLAY == "0.5.7"', 'assert VERSION_DISPLAY == "0.6.0"'),
                    ('assert f"{APP_TITLE} · {VERSION_DISPLAY}" == "Weibo Text Archiver · 0.5.7"',
                     'assert f"{APP_TITLE} · {VERSION_DISPLAY}" == "Weibo Text Archiver · 0.6.0"'),
                ):
                    current = current.replace(new, old)
            self.assertEqual(current, self.baseline(path).replace("\r\n", "\n"), path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
