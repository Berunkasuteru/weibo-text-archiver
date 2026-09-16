"""Thin Tk view and worker adapters for the independent image core."""
from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .credentials import CredentialError, has_saved_login, load_cookie_header
from .image_backup import ImageBackup
from .image_client import ImageTimelineClient
from .image_downloader import ImageDownloader
from .image_manifest import MAX_MANIFEST_BYTES
from .image_models import ImageBackupResult, ImageError, ImageResultState, valid_identity
from .models import FetchRange, RangeMode
from .network import AuthenticationExpired, Cancelled
from .tasking import TaskState


SCOPE = "保存本次微博接口明确返回且可下载的图片；视频不在备份范围内。"
RESUME = "已有图片会先校验，确认完整后不会重复下载。"
GAP_MESSAGE = "微博声明的媒体数量超过本次接口返回的图片槽位数量，因此不能确认所有历史图片均已枚举。"
NO_LOGIN = "请先在主窗口扫码登录微博，再开始图片备份。"
BUSY = "当前微博任务正在运行，请完成或取消后再开始图片备份。"
FULL_CONFIRM = "全量图片备份可能占用较多磁盘空间，并可能需要较长时间。继续吗？"


@dataclass(frozen=True)
class ImageBackupRequest:
    target_uid: str
    fetch_range: FetchRange
    output_base: Path
    backup_root: Path


@dataclass(frozen=True)
class ImageProgress:
    posts: int = 0
    slots: int = 0
    declared: int = 0
    declared_unknown: int = 0
    discovered: int = 0
    saved: int = 0
    already_present: int = 0
    unavailable: int = 0
    failed: int = 0
    gap: int = 0
    phase: str = "timeline"


@dataclass(frozen=True)
class ImageOutcome:
    result: ImageBackupResult
    progress: ImageProgress


def failure_message(reason):
    """Closed mapping. Never interpolate upstream exception strings or URLs."""
    return {
        "login_required": NO_LOGIN,
        "authentication_expired": "微博登录已过期。请在主窗口重新扫码登录，再运行同一图片备份；已保存文件会保留。",
        "target_mismatch": "此备份文件夹的账号与当前目标不一致，请选择正确的保存位置。",
        "invalid_manifest": "已有进度记录无法安全读取。文件已保留，请检查保存位置。",
        "output_locked": "此备份文件夹正在被另一个图片任务使用，请稍后再试。",
        "insufficient_disk_space": "磁盘可用空间不足，已停止备份；已保存的图片会保留。",
        "storage_error": "保存位置无法安全写入，已停止备份；请检查磁盘空间与访问权限。",
        "unsafe_path": "保存位置无法安全使用，请选择普通本地文件夹。",
        "timeout": "连接超时，可稍后再次运行同一备份。",
        "network_error": "网络请求未完成，可稍后再次运行同一备份。",
        "tls_error": "无法验证安全连接，已停止本次请求。",
        "challenge": "微博要求验证，已停止备份；请先在微博完成验证后再试。",
        "rate_limited": "微博暂时限制访问，已停止备份，请稍后再试。",
        "http_403": "图片服务拒绝了请求，已停止备份；这不表示图片已被删除。",
        "http_429": "图片服务暂时限制访问，请稍后再试。",
        "http_432": "图片服务暂时限制访问，请稍后再试。",
    }.get(reason, "本次图片备份未能完成，请稍后重试或检查保存位置。")


def resolve_request(target, mode, recent, since, output):
    # Reuse the published account guard and range validation, not text fetching.
    from .app import App, extract_uid, is_obvious_single_post_url
    if is_obvious_single_post_url(target):
        raise ValueError("这里需要账号的数字 UID 或账号主页链接，不是单条微博链接。")
    uid = extract_uid(target)
    if not valid_identity(uid):
        raise ValueError("请输入微博数字 UID，或包含数字 UID 的账号主页链接。")
    if mode not in {m.value for m in RangeMode}:
        raise ValueError("请选择有效的备份范围。")
    class Value:
        def __init__(self, v): self.v = v
        def get(self): return self.v
    class RangeFields:
        range_mode_var = Value(mode)
        recent_count_var = Value(recent)
        since_date_var = Value(since)
    fetch_range = App._selected_range(RangeFields(), trial=mode == RangeMode.TRIAL.value)
    if not output.strip():
        raise ValueError("请选择图片备份的保存位置。")
    try:
        base = Path(output).expanduser().resolve()
        if not base.is_absolute() or "\0" in str(base):
            raise ValueError()
        root = base / f"WeiboImages_{uid}"
    except (OSError, ValueError):
        raise ValueError("保存位置无效，请选择本地文件夹。") from None
    return ImageBackupRequest(uid, fetch_range, base, root)


def text_task_active(app):
    active = {TaskState.AUTHENTICATING, TaskState.FETCHING, TaskState.EXPORTING}
    if app.tasks.state in active or getattr(app, "_pending_export_request", None) is not None:
        return True
    workers = [getattr(app, "worker", None), *getattr(app, "_image_text_draining", ())]
    return any(w is not None and w.is_alive() for w in workers)


def image_task_active(app):
    window = getattr(app, "_image_window", None)
    return window is not None and window.controller.active


def open_image_window(app):
    window = getattr(app, "_image_window", None)
    if window is not None and window.winfo_exists():
        window.deiconify()
        window.lift()
        window.focus_set()
        return window
    window = ImageBackupWindow(app)
    app._image_window = window
    return window


class ProgressAdapter:
    """Worker-only aggregate projection. Core APIs and persisted schema unchanged."""
    def __init__(self, request, publish):
        self.request, self.publish = request, publish
        self.value = ImageProgress()
        self.started = False

    def discovered(self, group, posts):
        self.started = True  # Core has begun this run before it consumes discovery.
        self.value = replace(self.value, posts=posts,
            slots=self.value.slots + sum(r.enumerated_slots for r in group),
            declared=self.value.declared + sum(r.declared_count or 0 for r in group),
            declared_unknown=self.value.declared_unknown + sum(r.declared_count is None for r in group),
            discovered=self.value.discovered + sum(len(r.assets) for r in group),
            gap=self.value.gap + sum(r.enumeration_gap for r in group), phase="images")
        self.publish(self.value)

    def checkpoint(self, phase="images"):
        # Atomic core checkpoints can be read without sharing mutable worker state.
        # This read is on the worker, never on Tk; only integer facts leave here.
        if self.started:
            try:
                path = self.request.backup_root / "manifest.json"
                if path.stat().st_size <= MAX_MANIFEST_BYTES:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if data.get("target_identity") == self.request.target_uid:
                        counts = Counter(data["assets"][k]["status"] for k in data["current_asset_keys"])
                        self.value = replace(self.value, saved=counts["saved"], already_present=counts["already_present"],
                                             unavailable=counts["unavailable"], failed=counts["failed"])
            except (OSError, ValueError, KeyError, TypeError):
                pass  # Reporting must not change the core's result or recovery.
        self.value = replace(self.value, phase=phase)
        self.publish(self.value)


def run_image_request(request, cancel, publish):
    """Worker entry: never touches Tk, never sends arbitrary diagnostics to UI."""
    progress = ProgressAdapter(request, publish)
    publish(progress.value)
    result = None
    reason = None
    try:
        if cancel.is_set():
            raise Cancelled("image_cancelled")
        cookie = load_cookie_header()
        class Timeline(ImageTimelineClient):
            def iter_posts(self, uid, fetch_range):
                for group in super().iter_posts(uid, fetch_range):
                    progress.discovered(group, self.report.posts_inspected)
                    yield group
                    progress.checkpoint("timeline")
        class Downloader(ImageDownloader):
            def download(self, url, **kwargs):
                progress.checkpoint("images")
                return super().download(url, **kwargs)
        client = Timeline(cookie_header=cookie, cancel_event=cancel)
        core = ImageBackup(request.backup_root, cancel_event=cancel,
                           downloader=Downloader(cancel_event=cancel))
        result = core.run(request.target_uid, request.fetch_range, client=client)
    except CredentialError:
        reason = "login_required"
    except AuthenticationExpired:
        reason = "authentication_expired"
    except Cancelled:
        reason = "cancelled"
    except Exception:
        reason = "unexpected_failure"
    if result is None:
        result = ImageBackupResult(ImageResultState.CANCELLED if reason == "cancelled" else ImageResultState.FAILED,
                                   0, 0, 0, 0, 0, 0, 0, 0, 0, None if reason == "cancelled" else reason)
    p = replace(progress.value, posts=result.posts_inspected, discovered=result.discovered,
                saved=result.saved, already_present=result.already_present, unavailable=result.unavailable,
                failed=result.failed, gap=result.enumeration_gap)
    return ImageOutcome(result, p)


class ImageTaskController:
    """Bounded mailbox: one coalesced progress and one terminal event per task."""
    def __init__(self, runner=run_image_request):
        self.runner = runner
        self.generation = 0
        self.active = False
        self.worker = None
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._progress = self._terminal = None
        self.closed = False

    def start(self, request):
        if self.active or self.closed:
            raise RuntimeError("image_task_active")
        self.generation += 1
        generation = self.generation
        self.cancel_event = threading.Event()
        self.active = True
        self._progress = self._terminal = None
        self.worker = threading.Thread(target=self._work, args=(generation, request, self.cancel_event), daemon=True)
        try:
            self.worker.start()
        except Exception:
            self.active = False
            raise

    def _publish(self, generation, kind, payload):
        with self._lock:
            if generation != self.generation or self.closed or not self.active:
                return
            if kind == "progress" and self._terminal is None:
                self._progress = (generation, kind, payload)
            elif kind != "progress" and self._terminal is None:
                self._terminal = (generation, kind, payload)

    def _work(self, generation, request, cancel):
        try:
            outcome = self.runner(request, cancel, lambda p: self._publish(generation, "progress", p))
            kind = {ImageResultState.COMPLETE: "done", ImageResultState.PARTIAL: "partial",
                    ImageResultState.CANCELLED: "cancelled", ImageResultState.FAILED: "error"}[outcome.result.state]
        except Exception:
            outcome = ImageOutcome(ImageBackupResult(ImageResultState.FAILED, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                                                    "unexpected_failure"), ImageProgress())
            kind = "error"
        self._publish(generation, kind, outcome)

    def drain(self):
        with self._lock:
            events = []
            if self._progress is not None:
                events.append(self._progress)
                self._progress = None
            # Ownership is retained until the thread has actually returned.
            if self._terminal is not None and not self.worker.is_alive():
                events.append(self._terminal)
                self._terminal = None
                self.active = False
            return events

    def cancel(self):
        self.cancel_event.set()

    def shutdown(self):
        self.cancel()
        with self._lock:
            self.closed = True
            self.generation += 1
            self._progress = self._terminal = None


def progress_text(p):
    return (f"检查微博：{p.posts:,}    枚举图片槽位：{p.slots:,}\n"
            f"声明数量（已知）：{p.declared:,}    数量未知记录：{p.declared_unknown:,}\n"
            f"保存：{p.saved:,}    已存在：{p.already_present:,}    无法取得：{p.unavailable:,}\n"
            f"失败：{p.failed:,}    枚举缺口：{p.gap:,}")


def result_presentation(outcome):
    result = outcome.result
    title, explanation = {
        ImageResultState.COMPLETE: ("图片备份完成", "本次接口明确返回的可下载图片已处理完成。"),
        ImageResultState.PARTIAL: ("图片备份完成，但存在未保存项目", "已成功保存的文件会保留，可稍后再次运行同一备份继续检查。"),
        ImageResultState.CANCELLED: ("已取消图片备份", "已经成功保存的图片和进度记录会保留。"),
        ImageResultState.FAILED: ("图片备份未完成", "已保存的图片和进度记录会保留。"),
    }[result.state]
    if result.enumeration_gap:
        explanation += "\n" + GAP_MESSAGE
    if result.discovery_warnings:
        explanation += f"\n有 {result.discovery_warnings:,} 项图片信息无法确认，未按成功处理。"
    if result.pending:
        explanation += f"\n待处理图片：{result.pending:,}。"
    if result.failure_reason:
        explanation += "\n" + failure_message(result.failure_reason)
    elif result.state is ImageResultState.FAILED:
        explanation += "\n" + failure_message(None)
    return title, progress_text(outcome.progress), explanation


class ImageBackupWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        from .app import ActivityIndicator, extract_uid, is_obvious_single_post_url
        self.app = app
        self.withdraw()
        self.title("微博图片备份")
        self.transient(app)
        self.resizable(False, False)
        self.controller = ImageTaskController()
        self._after_id = None
        self._closing = self._close_app = self._destroyed = False
        self._main_states = {}
        self.request = None
        self.last_outcome = None
        self.useful_root = None
        target = app.uid_var.get()
        uid = "" if is_obvious_single_post_url(target) else extract_uid(target)
        self.target_var = tk.StringVar(self, uid if valid_identity(uid) else "")
        self.range_var = tk.StringVar(self, RangeMode.TRIAL.value)
        self.recent_var = tk.StringVar(self, app.recent_count_var.get())
        self.since_var = tk.StringVar(self, "")
        self.output_var = tk.StringVar(self, str(app.default_output_dir))
        self.status_var = tk.StringVar(self, "就绪")
        self.counts_var = tk.StringVar(self, progress_text(ImageProgress()))
        self.note_var = tk.StringVar(self, "微博返回的图片列表可能少于声明数量，缺口会在结果中报告。")
        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="微博图片备份", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        ttk.Label(body, text=SCOPE, style="Muted.TLabel", wraplength=590).pack(anchor="w", pady=(6, 14))
        ttk.Label(body, text="目标账号 · UID / 主页", style="Section.TLabel").pack(anchor="w")
        self.target_entry = ttk.Entry(body, textvariable=self.target_var)
        self.target_entry.pack(fill="x", pady=(5, 12))
        ttk.Label(body, text="备份范围", style="Section.TLabel").pack(anchor="w")
        ranges = ttk.Frame(body)
        ranges.pack(fill="x", pady=(5, 12))
        self.range_buttons = []
        for row, (label, mode) in enumerate((("测试备份 20 条", RangeMode.TRIAL), ("最近", RangeMode.RECENT),
                                            ("从", RangeMode.SINCE), ("全量备份", RangeMode.ALL))):
            button = ttk.Radiobutton(ranges, text=label, value=mode.value, variable=self.range_var,
                                     command=self._range_controls)
            button.grid(row=row, column=0, sticky="w", pady=3)
            self.range_buttons.append(button)
        self.recent_entry = ttk.Entry(ranges, textvariable=self.recent_var, width=12)
        self.recent_entry.grid(row=1, column=1, sticky="w", padx=(8, 5))
        ttk.Label(ranges, text="条").grid(row=1, column=2, sticky="w")
        self.since_entry = ttk.Entry(ranges, textvariable=self.since_var, width=12)
        self.since_entry.grid(row=2, column=1, sticky="w", padx=(8, 5))
        ttk.Label(ranges, text="起  ·  YYYY-MM-DD").grid(row=2, column=2, sticky="w")
        ttk.Label(body, text="保存位置", style="Section.TLabel").pack(anchor="w")
        output = ttk.Frame(body)
        output.pack(fill="x", pady=(5, 5))
        self.choose_btn = ttk.Button(output, text="选择…", style="Quiet.TButton", command=self.choose_output)
        self.choose_btn.pack(side="right", padx=(8, 0))
        self.output_entry = ttk.Entry(output, textvariable=self.output_var)
        self.output_entry.pack(side="left", fill="x", expand=True)
        ttk.Label(body, text="按账号分别保存。" + RESUME, style="Muted.TLabel", wraplength=590).pack(anchor="w")
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(14, 12))
        self.start_btn = ttk.Button(actions, text="开始图片备份", style="Primary.TButton", command=self.start)
        self.start_btn.pack(side="right")
        self.cancel_btn = ttk.Button(actions, text="取消", style="Quiet.TButton", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="right", padx=(0, 8))
        self.open_btn = ttk.Button(actions, text="打开备份文件夹", style="Quiet.TButton", command=self.open_folder, state="disabled")
        self.open_btn.pack(side="left")
        card = ttk.Frame(body, style="Card.TFrame", padding=12)
        card.pack(fill="both", expand=True)
        ttk.Label(card, textvariable=self.status_var, style="Status.TLabel").pack(anchor="w")
        self.activity = ActivityIndicator(card)
        self.activity.pack(anchor="w", pady=(8, 6))
        ttk.Label(card, textvariable=self.counts_var, justify="left").pack(anchor="w")
        ttk.Label(card, textvariable=self.note_var, style="Muted.TLabel", wraplength=580, justify="left").pack(anchor="w", pady=(8, 0))
        self._range_controls()
        self.protocol("WM_DELETE_WINDOW", self.request_close)
        app._center_child_window(self, minimum_width=660, minimum_height=640)
        self._after_id = self.after(100, self._poll)

    def _range_controls(self):
        for entry, mode in ((self.recent_entry, RangeMode.RECENT), (self.since_entry, RangeMode.SINCE)):
            entry.configure(state="normal" if not self.controller.active and self.range_var.get() == mode.value else "disabled")

    def choose_output(self):
        folder = filedialog.askdirectory(parent=self, title="选择图片备份的基础保存位置", initialdir=self.output_var.get())
        if folder:
            self.output_var.set(folder)

    def _set_running(self, running):
        for w in (self.target_entry, self.output_entry, self.choose_btn, self.start_btn, *self.range_buttons):
            w.configure(state="disabled" if running else "normal")
        self.cancel_btn.configure(state="normal" if running else "disabled")
        self._range_controls()
        for name in ("trial_btn", "export_btn", "login_btn", "clear_login_btn"):
            w = getattr(self.app, name)
            if running:
                self._main_states[name] = str(w.cget("state"))
                w.configure(state="disabled")
            elif name in self._main_states:
                w.configure(state=self._main_states.pop(name))
        if running:
            self.activity.start()
        else:
            self.activity.stop()

    def start(self):
        if self.controller.active or self._closing:
            return
        if text_task_active(self.app):
            messagebox.showinfo("任务正在运行", BUSY, parent=self)
            return
        try:
            request = resolve_request(self.target_var.get(), self.range_var.get(), self.recent_var.get(),
                                      self.since_var.get(), self.output_var.get())
        except ValueError as exc:
            messagebox.showwarning("图片备份设置有误", str(exc), parent=self)
            return
        except OSError:
            messagebox.showwarning("图片备份设置有误", "保存位置无效。", parent=self)
            return
        if not has_saved_login():
            messagebox.showinfo("需要登录", NO_LOGIN, parent=self)
            self.app.lift()
            self.app.login_btn.focus_set()
            return
        if request.fetch_range.mode is RangeMode.ALL and not messagebox.askyesno("全量图片备份", FULL_CONFIRM, parent=self):
            return
        # Dialogs run a nested Tk loop. Recheck ownership after they close.
        if text_task_active(self.app):
            messagebox.showinfo("任务正在运行", BUSY, parent=self)
            return
        self.request = request
        self.last_outcome = None
        self.useful_root = None
        self.open_btn.configure(state="disabled")
        self.status_var.set("正在读取微博时间线…")
        self.counts_var.set(progress_text(ImageProgress()))
        self.note_var.set("正在处理本次返回的图片。" + RESUME)
        try:
            self.controller.start(request)
        except Exception:
            self.status_var.set("图片备份未完成")
            self.note_var.set("无法启动后台任务，请稍后重试。")
            return
        self._set_running(True)

    def cancel(self):
        if self.controller.active:
            self.controller.cancel()
            self.cancel_btn.configure(state="disabled")
            self.status_var.set("正在取消图片备份…")
            self.note_var.set("正在结束当前操作；已经成功保存的图片会保留。")

    def _handle_event(self, generation, kind, payload):
        if generation != self.controller.generation or self._destroyed:
            return
        if kind == "progress":
            self.counts_var.set(progress_text(payload))
            if not self.controller.cancel_event.is_set():
                self.status_var.set("正在读取微博时间线…" if payload.phase == "timeline" else "正在校验 / 保存图片…")
            return
        self.last_outcome = payload
        self._set_running(False)
        title, counts, note = result_presentation(payload)
        self.status_var.set(title)
        self.counts_var.set(counts)
        self.note_var.set(note)
        if payload.result.saved + payload.result.already_present > 0:
            self.useful_root = self.request.backup_root
            self.open_btn.configure(state="normal")
        # Longer result notes may need more height, using the unchanged app helper.
        self.app._center_child_window(self, minimum_width=660, minimum_height=640)

    def _poll(self):
        self._after_id = None
        if self._destroyed:
            return
        for event in self.controller.drain():
            self._handle_event(*event)
        if self._closing and not self.controller.active:
            close_app = self._close_app
            self.shutdown()
            if close_app:
                self.app._on_close()
            return
        self._after_id = self.after(100, self._poll)

    def open_folder(self):
        if self.useful_root is None:
            return
        from .app import launch_with_system
        ok, message = launch_with_system(self.useful_root)
        if not ok:
            messagebox.showinfo("打开文件夹", message, parent=self)

    def request_close(self, *, close_app=False):
        self._closing = True
        self._close_app = self._close_app or close_app
        if self.controller.active:
            self.cancel()
        else:
            self.shutdown()

    def shutdown(self):
        if self._destroyed:
            return
        self._destroyed = True
        self.controller.shutdown()
        self.activity.stop()
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
        if getattr(self.app, "_image_window", None) is self:
            self.app._image_window = None
        super().destroy()
