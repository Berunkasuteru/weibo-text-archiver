from __future__ import annotations

import os
import queue
import threading
from datetime import date, datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import VERSION_DISPLAY
from .auth import begin_qr_login, poll_qr_login, qr_matrix, save_cookies
from .client import WeiboClient
from .export_options import (
    CustomFilterOptions,
    CustomFilterReport,
    DateFormat,
    ExportLayout,
    ExportOptions,
    ExportPreset,
    ExportSelection,
    build_export_selections,
    filter_archive,
    filter_report_notice,
    filter_summary,
    options_summary,
    parse_filter_terms,
)
from .exporter import export_markdown
from .models import ArchiveIntegrity, FetchRange, RangeMode, Termination
from .network import Cancelled, NetworkError, RateLimited
from .paths import APP_ICON_PNG, COOKIE_FILE, DEFAULT_OUTPUT_DIR
from .security import redact_text, save_detailed_error
from .storage import save_normalized_archive
from .tasking import TaskManager, TaskState


APP_TITLE = "Weibo Text Archiver"
APP_SUBTITLE = "微博文字导出 / AI Archive"
TEST_EXPORT_LIMIT = 20
DEFAULT_WINDOW_WIDTH = 860
DEFAULT_COMPACT_HEIGHT = 760


def _compact_window_height(configured_height: int, requested_height: int) -> int:
    """Honor real Tk font/DPI requirements without arbitrary extra height."""
    return max(configured_height, requested_height)


def _fit_expanded_height(
    current_height: int,
    requested_height: int,
    current_y: int,
    work_top: int,
    work_bottom: int,
) -> tuple[int, int]:
    """Fit an expanded window into the current monitor's usable vertical area."""
    work_height = max(1, work_bottom - work_top)
    height = min(max(current_height, requested_height), work_height)
    y = min(max(current_y, work_top), work_bottom - height)
    return height, y


def _geometry_spec(width: int, height: int, x: int, y: int) -> str:
    x_part = f"+{x}" if x >= 0 else str(x)
    y_part = f"+{y}" if y >= 0 else str(y)
    return f"{width}x{height}{x_part}{y_part}"


def _centered_child_geometry(
    parent_x: int,
    parent_y: int,
    parent_width: int,
    parent_height: int,
    child_width: int,
    child_height: int,
    work_left: int,
    work_top: int,
    work_right: int,
    work_bottom: int,
) -> tuple[int, int, int, int]:
    """Center a child on its parent, then clamp it to the parent's work area."""
    work_width = max(1, work_right - work_left)
    work_height = max(1, work_bottom - work_top)
    width = min(max(1, child_width), work_width)
    height = min(max(1, child_height), work_height)

    x = parent_x + (parent_width - width) // 2
    y = parent_y + (parent_height - height) // 2
    x = min(max(x, work_left), work_right - width)
    y = min(max(y, work_top), work_bottom - height)
    return width, height, x, y


def _load_app_icon(window, icon_path: Path = APP_ICON_PNG):
    """Set the tracked icon when available; retain Tk's default on failure."""
    try:
        icon = tk.PhotoImage(master=window, file=str(icon_path))
        window.iconphoto(True, icon)
        return icon
    except (OSError, tk.TclError):
        return None


def extract_uid(value: str) -> str:
    import re
    m = re.search(r"(?<!\d)(\d{5,})(?!\d)", (value or "").strip())
    return m.group(1) if m else ""


def launch_with_system(
    path: Path,
    launcher=None,
) -> tuple[bool, str]:
    """Open an existing output path without shell commands or import-time OS assumptions."""
    target = Path(path)
    if not target.exists():
        return False, "目标已经不存在，可能已被移动或删除。"

    selected_launcher = launcher if launcher is not None else getattr(os, "startfile", None)
    if selected_launcher is None:
        return False, "当前系统不支持此打开方式。"

    try:
        selected_launcher(str(target))
    except OSError:
        return False, "Windows 无法打开该目标；请检查文件关联或访问权限。"
    return True, ""


def completion_integrity_lines(integrity: ArchiveIntegrity) -> list[str]:
    if not integrity.incomplete_records:
        return []
    return [
        f"完整记录：{integrity.complete_records:,}",
        f"不完整记录：{integrity.incomplete_records:,}",
        (
            f"其中：顶层正文 {integrity.incomplete_top_level:,} · "
            f"转发原文 {integrity.incomplete_retweets:,}"
        ),
        "无法验证的内容已在 Markdown 中明确标记。",
    ]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self._app_icon_image = _load_app_icon(self)
        self.title(f"{APP_TITLE} · {VERSION_DISPLAY}")
        self.geometry(f"{DEFAULT_WINDOW_WIDTH}x{DEFAULT_COMPACT_HEIGHT}")
        self.minsize(780, 650)

        self.events: queue.Queue = queue.Queue()
        self.tasks = TaskManager()
        self.worker: threading.Thread | None = None

        self.qr_window: tk.Toplevel | None = None
        self.qr_canvas: tk.Canvas | None = None
        self.qr_status_var = tk.StringVar(value="正在获取二维码…")

        self.uid_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.range_mode_var = tk.StringVar(value=RangeMode.ALL.value)
        self.recent_count_var = tk.StringVar(value="1000")
        self.since_date_var = tk.StringVar(value="")
        self.full_output_var = tk.BooleanVar(value=True)
        self.ai_output_var = tk.BooleanVar(value=False)
        self.custom_output_var = tk.BooleanVar(value=False)
        self.custom_options = ExportOptions()
        self.custom_filter = CustomFilterOptions()
        self.content_summary_var = tk.StringVar()
        self.login_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.progress_detail_var = tk.StringVar(value="等待任务")
        self.range_hint_var = tk.StringVar(value="默认：抓取当前接口能访问到的全部微博。")
        self.logs_visible = False
        self._compact_geometry: tuple[int, int, int, int] | None = None

        self._configure_style()
        self._build_ui()
        self.update_idletasks()
        compact_height = _compact_window_height(
            DEFAULT_COMPACT_HEIGHT,
            self.winfo_reqheight(),
        )
        self.geometry(f"{DEFAULT_WINDOW_WIDTH}x{compact_height}")
        self._refresh_login_status()
        self._update_range_controls()
        self._update_content_controls()
        self.after(100, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -------------------- UI --------------------

    def _configure_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista" if os.name == "nt" else "clam")
        except tk.TclError:
            pass

        style.configure("Hero.TLabel", font=("Segoe UI", 21, "bold"))
        style.configure("Sub.TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("Section.TLabel", font=("Segoe UI", 9, "bold"))
        style.configure("Status.TLabel", font=("Consolas", 9))
        style.configure("Primary.TButton", padding=(16, 8))
        style.configure("Quiet.TButton", padding=(10, 6))
        style.configure("Card.TFrame", relief="solid", borderwidth=1)
        style.configure("Muted.TLabel", foreground="#666666")

    def _build_ui(self):
        self.root_frame = ttk.Frame(self, padding=(24, 20, 24, 18))
        self.root_frame.pack(fill="both", expand=True)

        # Hero
        hero = ttk.Frame(self.root_frame)
        hero.pack(fill="x")
        ttk.Label(hero, text=APP_TITLE, style="Hero.TLabel").pack(side="left")
        ttk.Label(
            hero,
            text=VERSION_DISPLAY,
            style="Muted.TLabel",
        ).pack(side="right", anchor="n", pady=(8, 0))
        ttk.Label(
            self.root_frame,
            text=APP_SUBTITLE,
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(0, 18))

        # Account
        self._section_label("ACCOUNT")
        account = ttk.Frame(self.root_frame, style="Card.TFrame", padding=12)
        account.pack(fill="x", pady=(4, 14))

        ttk.Label(account, textvariable=self.login_var).pack(side="left")
        self.clear_login_btn = ttk.Button(
            account,
            text="清除登录信息",
            style="Quiet.TButton",
            command=self.clear_login,
        )
        self.clear_login_btn.pack(side="right")
        self.login_btn = ttk.Button(
            account,
            text="扫码登录 / 更新",
            style="Quiet.TButton",
            command=self.start_login,
        )
        self.login_btn.pack(side="right", padx=(0, 8))

        # Target
        self._section_label("TARGET")
        target = ttk.Frame(self.root_frame, style="Card.TFrame", padding=12)
        target.pack(fill="x", pady=(4, 14))

        ttk.Label(
            target,
            text="数字 UID 或包含数字 UID 的微博主页链接",
            style="Muted.TLabel",
        ).pack(anchor="w")
        self.uid_entry = ttk.Entry(target, textvariable=self.uid_var)
        self.uid_entry.pack(fill="x", pady=(7, 0))

        # Range
        self._section_label("RANGE")
        range_card = ttk.Frame(self.root_frame, style="Card.TFrame", padding=12)
        range_card.pack(fill="x", pady=(4, 14))

        all_row = ttk.Frame(range_card)
        all_row.pack(fill="x", pady=2)
        ttk.Radiobutton(
            all_row,
            text="全量快照",
            variable=self.range_mode_var,
            value=RangeMode.ALL.value,
            command=self._update_range_controls,
        ).pack(side="left")

        recent_row = ttk.Frame(range_card)
        recent_row.pack(fill="x", pady=2)
        ttk.Radiobutton(
            recent_row,
            text="最近",
            variable=self.range_mode_var,
            value=RangeMode.RECENT.value,
            command=self._update_range_controls,
        ).pack(side="left")
        self.recent_entry = ttk.Entry(
            recent_row,
            width=8,
            textvariable=self.recent_count_var,
        )
        self.recent_entry.pack(side="left", padx=(8, 6))
        ttk.Label(recent_row, text="条").pack(side="left")

        since_row = ttk.Frame(range_card)
        since_row.pack(fill="x", pady=2)
        ttk.Radiobutton(
            since_row,
            text="从",
            variable=self.range_mode_var,
            value=RangeMode.SINCE.value,
            command=self._update_range_controls,
        ).pack(side="left")
        self.since_entry = ttk.Entry(
            since_row,
            width=14,
            textvariable=self.since_date_var,
        )
        self.since_entry.pack(side="left", padx=(8, 6))
        ttk.Label(since_row, text="起（YYYY-MM-DD）").pack(side="left")

        ttk.Label(
            range_card,
            textvariable=self.range_hint_var,
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(7, 0))

        # Output selection
        self._section_label("OUTPUT")
        content_card = ttk.Frame(self.root_frame, style="Card.TFrame", padding=12)
        content_card.pack(fill="x", pady=(4, 14))

        preset_row = ttk.Frame(content_card)
        preset_row.pack(fill="x")
        self.output_buttons = []
        for text, variable in (
            ("完整归档（推荐）", self.full_output_var),
            ("AI Compact", self.ai_output_var),
            ("自定义导出", self.custom_output_var),
        ):
            button = ttk.Checkbutton(
                preset_row,
                text=text,
                variable=variable,
                command=self._update_content_controls,
            )
            button.pack(side="left", padx=(0, 18))
            self.output_buttons.append(button)

        content_detail = ttk.Frame(content_card)
        content_detail.pack(fill="x", pady=(8, 0))
        ttk.Label(
            content_detail,
            textvariable=self.content_summary_var,
            style="Muted.TLabel",
        ).pack(side="left", fill="x", expand=True)
        self.custom_settings_btn = ttk.Button(
            content_detail,
            text="设置…",
            style="Quiet.TButton",
            command=self._open_custom_settings,
        )
        self.custom_settings_btn.pack(side="right", padx=(8, 0))

        # Save location
        self._section_label("SAVE TO")
        output_card = ttk.Frame(self.root_frame, style="Card.TFrame", padding=12)
        output_card.pack(fill="x", pady=(4, 14))
        output_row = ttk.Frame(output_card)
        output_row.pack(fill="x")
        self.output_entry = ttk.Entry(output_row, textvariable=self.output_var)
        self.output_entry.pack(side="left", fill="x", expand=True)
        self.output_choose_btn = ttk.Button(
            output_row,
            text="选择文件夹",
            style="Quiet.TButton",
            command=self.choose_output,
        )
        self.output_choose_btn.pack(side="left", padx=(8, 0))

        # Actions
        actions = ttk.Frame(self.root_frame)
        actions.pack(fill="x", pady=(2, 10))
        self.trial_btn = ttk.Button(
            actions,
            text="测试导出",
            style="Quiet.TButton",
            command=lambda: self.start_export(trial=True),
        )
        self.trial_btn.pack(side="left")

        self.stop_btn = ttk.Button(
            actions,
            text="停止",
            style="Quiet.TButton",
            command=self.stop_current,
            state="disabled",
        )
        self.stop_btn.pack(side="left", padx=(8, 0))

        self.export_btn = ttk.Button(
            actions,
            text="开始导出 →",
            style="Primary.TButton",
            command=lambda: self.start_export(trial=False),
        )
        self.export_btn.pack(side="right")

        # Status
        status_card = ttk.Frame(self.root_frame, style="Card.TFrame", padding=12)
        status_card.pack(fill="x")
        status_top = ttk.Frame(status_card)
        status_top.pack(fill="x")
        ttk.Label(status_top, textvariable=self.status_var, style="Status.TLabel").pack(side="left")
        self.details_btn = ttk.Button(
            status_top,
            text="运行详情 ›",
            style="Quiet.TButton",
            command=self.toggle_logs,
        )
        self.details_btn.pack(side="right")

        self.progress = ttk.Progressbar(status_card, mode="indeterminate")
        self.progress.pack(fill="x", pady=(9, 6))
        ttk.Label(
            status_card,
            textvariable=self.progress_detail_var,
            style="Muted.TLabel",
        ).pack(anchor="w")

        self.log_frame = ttk.Frame(self.root_frame)
        self.log_text = tk.Text(
            self.log_frame,
            height=12,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
            relief="flat",
        )
        scroll = ttk.Scrollbar(
            self.log_frame,
            orient="vertical",
            command=self.log_text.yview,
        )
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _section_label(self, text: str):
        ttk.Label(self.root_frame, text=text, style="Section.TLabel").pack(anchor="w")

    def toggle_logs(self, force: bool | None = None):
        target = (not self.logs_visible) if force is None else force
        if target == self.logs_visible:
            return
        self.logs_visible = target
        if target:
            self.update_idletasks()
            self._compact_geometry = (
                self.winfo_width(),
                self.winfo_height(),
                self.winfo_x(),
                self.winfo_y(),
            )
            self.log_frame.pack(fill="both", expand=True, pady=(10, 0))
            self.details_btn.configure(text="运行详情 ⌄")
            self.update_idletasks()

            width, compact_height, x, y = self._compact_geometry
            requested_height = max(
                self.winfo_reqheight(),
                compact_height + self.log_frame.winfo_reqheight() + 10,
            )
            _, work_top, _, work_bottom = self._current_monitor_work_area()
            height, y = _fit_expanded_height(
                compact_height,
                requested_height,
                y,
                work_top,
                work_bottom,
            )
            self.geometry(_geometry_spec(width, height, x, y))
        else:
            self.log_frame.pack_forget()
            self.details_btn.configure(text="运行详情 ›")
            if self._compact_geometry is not None:
                width, height, x, y = self._compact_geometry
                self.geometry(_geometry_spec(width, height, x, y))
                self._compact_geometry = None

    def _current_monitor_work_area(self) -> tuple[int, int, int, int]:
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                class MonitorInfo(ctypes.Structure):
                    _fields_ = (
                        ("cbSize", wintypes.DWORD),
                        ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD),
                    )

                user32 = ctypes.windll.user32
                user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
                user32.MonitorFromWindow.restype = wintypes.HANDLE
                user32.GetMonitorInfoW.argtypes = (
                    wintypes.HANDLE,
                    ctypes.POINTER(MonitorInfo),
                )
                user32.GetMonitorInfoW.restype = wintypes.BOOL

                monitor = user32.MonitorFromWindow(self.winfo_id(), 2)
                info = MonitorInfo()
                info.cbSize = ctypes.sizeof(info)
                if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    work = info.rcWork
                    return work.left, work.top, work.right, work.bottom
            except (AttributeError, OSError, TypeError, tk.TclError):
                pass

        left = self.winfo_vrootx()
        top = self.winfo_vrooty()
        return (
            left,
            top,
            left + self.winfo_vrootwidth(),
            top + self.winfo_vrootheight(),
        )

    def _center_child_window(
        self,
        child: tk.Toplevel,
        *,
        minimum_width: int = 0,
        minimum_height: int = 0,
    ) -> None:
        self.update_idletasks()
        child.update_idletasks()
        work_left, work_top, work_right, work_bottom = self._current_monitor_work_area()
        width, height, x, y = _centered_child_geometry(
            self.winfo_rootx(),
            self.winfo_rooty(),
            self.winfo_width(),
            self.winfo_height(),
            max(minimum_width, child.winfo_reqwidth()),
            max(minimum_height, child.winfo_reqheight()),
            work_left,
            work_top,
            work_right,
            work_bottom,
        )
        child.geometry(_geometry_spec(width, height, x, y))
        child.deiconify()

    def _update_range_controls(self):
        mode = self.range_mode_var.get()
        self.recent_entry.configure(
            state="normal" if mode == RangeMode.RECENT.value else "disabled"
        )
        self.since_entry.configure(
            state="normal" if mode == RangeMode.SINCE.value else "disabled"
        )
        if mode == RangeMode.ALL.value:
            self.range_hint_var.set("默认：抓取当前接口能访问到的全部微博。")
        elif mode == RangeMode.RECENT.value:
            self.range_hint_var.set("按新→旧抓取，达到指定条数后立即停止。")
        else:
            self.range_hint_var.set("按新→旧抓取，确认进入起始日期以前后停止；置顶旧微博不会触发提前结束。")

    def _update_content_controls(self):
        selected = []
        if self.full_output_var.get():
            selected.append("完整归档")
        if self.ai_output_var.get():
            selected.append("AI Compact")
        if self.custom_output_var.get():
            selected.append(
                "自定义："
                + options_summary(self.custom_options)
                + " · "
                + filter_summary(self.custom_filter)
            )
        self.content_summary_var.set(
            "；".join(selected) if selected else "请至少选择一种输出。"
        )

        running = self.tasks.state in (
            TaskState.AUTHENTICATING,
            TaskState.FETCHING,
            TaskState.EXPORTING,
        )
        state = "normal" if self.custom_output_var.get() and not running else "disabled"
        self.custom_settings_btn.configure(state=state)

    def _open_custom_settings(self):
        current = self.custom_options
        current_filter = self.custom_filter
        win = tk.Toplevel(self)
        win.withdraw()
        win.title("自定义导出内容")
        win.transient(self)
        win.resizable(False, False)

        body = ttk.Frame(win, padding=18)
        body.pack(fill="both", expand=True)

        layout_var = tk.StringVar(value=current.layout.value)
        source_var = tk.BooleanVar(value=current.include_source)
        location_var = tk.BooleanVar(value=current.include_location)
        engagement_var = tk.BooleanVar(value=current.include_engagement)
        date_var = tk.StringVar(value=current.date_format.value)
        original_var = tk.BooleanVar(value=current_filter.include_original)
        repost_var = tk.BooleanVar(value=current_filter.include_reposts)
        filter_start_var = tk.StringVar(
            value=current_filter.start_date.isoformat() if current_filter.start_date else ""
        )
        filter_end_var = tk.StringVar(
            value=current_filter.end_date.isoformat() if current_filter.end_date else ""
        )

        ttk.Label(body, text="排版", style="Section.TLabel").pack(anchor="w")
        layout_row = ttk.Frame(body)
        layout_row.pack(fill="x", pady=(4, 12))
        ttk.Radiobutton(
            layout_row,
            text="完整",
            variable=layout_var,
            value=ExportLayout.FULL.value,
        ).pack(side="left")
        ttk.Radiobutton(
            layout_row,
            text="AI 精简",
            variable=layout_var,
            value=ExportLayout.AI.value,
        ).pack(side="left", padx=(18, 0))

        ttk.Label(body, text="内容类型", style="Section.TLabel").pack(
            anchor="w", pady=(12, 0)
        )
        type_row = ttk.Frame(body)
        type_row.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(type_row, text="原创", variable=original_var).pack(side="left")
        ttk.Checkbutton(type_row, text="转发", variable=repost_var).pack(
            side="left", padx=(18, 0)
        )

        ttk.Label(body, text="关键词（可选）", style="Section.TLabel").pack(
            anchor="w", pady=(12, 0)
        )
        keyword_text = tk.Text(body, height=3, width=52, wrap="word")
        keyword_text.pack(fill="x", pady=(4, 0))
        keyword_text.insert("1.0", "，".join(current_filter.keywords))
        ttk.Label(
            body,
            text="多个关键词可用英文逗号、中文逗号或换行分隔；可直接输入 #话题#。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        ttk.Label(body, text="二次日期筛选（可选）", style="Section.TLabel").pack(
            anchor="w", pady=(12, 0)
        )
        filter_date_row = ttk.Frame(body)
        filter_date_row.pack(fill="x", pady=(4, 0))
        ttk.Label(filter_date_row, text="开始").pack(side="left")
        ttk.Entry(
            filter_date_row,
            width=12,
            textvariable=filter_start_var,
        ).pack(side="left", padx=(6, 14))
        ttk.Label(filter_date_row, text="结束").pack(side="left")
        ttk.Entry(
            filter_date_row,
            width=12,
            textvariable=filter_end_var,
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            body,
            text="格式 YYYY-MM-DD；只筛选本次已抓取记录，不会再次联网。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        ttk.Label(body, text="可选元数据", style="Section.TLabel").pack(anchor="w")
        ttk.Checkbutton(body, text="来源 / 设备", variable=source_var).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(body, text="发布位置", variable=location_var).pack(anchor="w")
        ttk.Checkbutton(body, text="转发 / 评论 / 点赞数量", variable=engagement_var).pack(anchor="w")

        ttk.Label(body, text="日期", style="Section.TLabel").pack(anchor="w", pady=(12, 0))
        date_row = ttk.Frame(body)
        date_row.pack(fill="x", pady=(4, 0))
        ttk.Radiobutton(
            date_row,
            text="YYYY-MM-DD HH:mm",
            variable=date_var,
            value=DateFormat.DATE_TIME_MINUTE.value,
        ).pack(side="left")
        ttk.Radiobutton(
            date_row,
            text="YYYY-MM-DD",
            variable=date_var,
            value=DateFormat.DATE_ONLY.value,
        ).pack(side="left", padx=(18, 0))

        ttk.Label(
            body,
            text="正文与日期属于核心归档信息，始终保留。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(12, 0))

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(18, 0))
        ttk.Button(actions, text="取消", command=win.destroy).pack(side="right")

        def save():
            def optional_date(raw: str, label: str) -> date | None:
                value = raw.strip()
                if not value:
                    return None
                try:
                    return date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(f"{label}请按 YYYY-MM-DD 填写。") from exc

            try:
                custom_options = ExportOptions(
                    layout=ExportLayout(layout_var.get()),
                    include_source=source_var.get(),
                    include_location=location_var.get(),
                    include_engagement=engagement_var.get(),
                    date_format=DateFormat(date_var.get()),
                )
                custom_filter = CustomFilterOptions(
                    include_original=original_var.get(),
                    include_reposts=repost_var.get(),
                    keywords=parse_filter_terms(keyword_text.get("1.0", "end")),
                    start_date=optional_date(filter_start_var.get(), "开始日期"),
                    end_date=optional_date(filter_end_var.get(), "结束日期"),
                )
            except (TypeError, ValueError) as exc:
                messagebox.showwarning("自定义设置有误", str(exc), parent=win)
                return

            self.custom_options = custom_options
            self.custom_filter = custom_filter
            self.custom_output_var.set(True)
            self._update_content_controls()
            win.destroy()

        ttk.Button(
            actions,
            text="保存设置",
            style="Primary.TButton",
            command=save,
        ).pack(side="right", padx=(0, 8))
        self._center_child_window(win)
        win.grab_set()

    def _set_running(self, running: bool):
        for widget in (
            self.uid_entry,
            self.output_entry,
            self.output_choose_btn,
            self.recent_entry,
            self.since_entry,
            self.trial_btn,
            self.export_btn,
            self.login_btn,
            self.clear_login_btn,
            *self.output_buttons,
        ):
            try:
                widget.configure(state="disabled" if running else "normal")
            except tk.TclError:
                pass

        if not running:
            self._update_range_controls()
        self._update_content_controls()

        self.stop_btn.configure(state="normal" if running else "disabled")
        if running:
            self.progress.start(12)
        else:
            self.progress.stop()

    # -------------------- Event channel --------------------

    def _emit(self, generation: int, kind: str, payload=None):
        self.events.put((generation, kind, payload))

    def _worker_log(self, generation: int, text: object):
        self._emit(generation, "log", redact_text(text))

    def _append_log(self, text: str):
        if text and not text.endswith("\n"):
            text += "\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_events(self):
        try:
            while True:
                generation, kind, payload = self.events.get_nowait()

                # The central invariant: stale worker generations cannot mutate UI.
                if not self.tasks.accepts(generation):
                    continue

                if kind == "log":
                    self._append_log(payload)

                elif kind == "status":
                    self.status_var.set(payload)

                elif kind == "progress":
                    message, data = payload
                    self.progress_detail_var.set(message)
                    if data and data.get("posts") is not None:
                        count_label = data.get("count_label") or "条"
                        bits = [f"{data['posts']:,} {count_label}"]
                        if data.get("page"):
                            bits.append(f"{data['page']} 页")
                        if data.get("frontier"):
                            try:
                                d = datetime.fromisoformat(data["frontier"])
                                bits.append(f"推进至 {d:%Y-%m-%d}")
                            except Exception:
                                pass
                        self.status_var.set("Fetching · " + " · ".join(bits))

                elif kind == "qr_matrix":
                    self._draw_qr(payload)

                elif kind == "qr_status":
                    self.qr_status_var.set(payload)

                elif kind == "phase_export":
                    self.tasks.transition(TaskState.EXPORTING)
                    self.status_var.set("Exporting")
                    self.progress_detail_var.set("正在生成 Markdown 并验证输出…")

                elif kind == "ready":
                    self.tasks.terminal(generation, TaskState.READY)
                    self._close_qr_window()
                    self._set_running(False)
                    self._refresh_login_status()
                    self.status_var.set("Ready")
                    self.progress_detail_var.set("登录成功，可以开始导出。")
                    messagebox.showinfo("登录成功", "微博登录状态已保存到本机。")

                elif kind == "done":
                    self.tasks.terminal(generation, TaskState.DONE)
                    self._set_running(False)
                    integrity = payload["integrity"]
                    if payload["failures"]:
                        self.status_var.set("Export complete · output warning")
                        self.progress_detail_var.set(
                            f"已生成 {len(payload['outputs'])} 个文件；"
                            f"{len(payload['failures'])} 个本地输出失败。"
                        )
                    elif integrity.incomplete_records:
                        self.status_var.set("Export complete · integrity warning")
                        self.progress_detail_var.set(
                            "导出已完成；"
                            f"其中 {integrity.incomplete_records:,} 条记录"
                            "含无法验证的历史内容。"
                        )
                    else:
                        self.status_var.set("Export complete")
                        self.progress_detail_var.set("本次导出已完成并写入 Markdown。")
                    self._show_completion(payload)
                    self.tasks.transition(TaskState.READY if COOKIE_FILE.exists() else TaskState.IDLE)

                elif kind == "error":
                    self.tasks.terminal(generation, TaskState.ERROR)
                    self._close_qr_window()
                    self._set_running(False)
                    self.status_var.set("Error")
                    self.progress_detail_var.set("任务失败；未把旧缓存伪装成成功结果。")
                    self.toggle_logs(True)
                    friendly, detail_path = payload
                    messagebox.showerror(
                        "任务失败",
                        friendly
                        + "\n\n详细错误日志：\n"
                        + detail_path
                        + "\n\n请不要发送 cookie.txt。",
                    )
                    self.tasks.transition(TaskState.READY if COOKIE_FILE.exists() else TaskState.IDLE)

                elif kind == "cancelled":
                    self.tasks.terminal(generation, TaskState.CANCELLED)
                    self._close_qr_window()
                    self._set_running(False)
                    self.status_var.set("Cancelled")
                    self.progress_detail_var.set("任务已取消；迟到的旧任务事件将被忽略。")
                    self.tasks.transition(TaskState.READY if COOKIE_FILE.exists() else TaskState.IDLE)

        except queue.Empty:
            pass

        self.after(100, self._poll_events)

    # -------------------- Login --------------------

    def _refresh_login_status(self):
        if COOKIE_FILE.exists() and COOKIE_FILE.stat().st_size > 20:
            self.login_var.set("● 已保存登录状态")
            if self.tasks.state is TaskState.IDLE:
                self.tasks.transition(TaskState.READY)
        else:
            self.login_var.set("○ 未登录")

    def _open_qr_window(self):
        self._close_qr_window()
        win = tk.Toplevel(self)
        win.withdraw()
        self.qr_window = win
        win.title("扫码登录微博")
        win.resizable(False, False)
        win.transient(self)
        win.protocol("WM_DELETE_WINDOW", self.stop_current)

        ttk.Label(
            win,
            text="请使用微博 App 扫码",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(pady=(18, 6))
        ttk.Label(win, text="微博 App → 我的 → 扫一扫").pack()

        frame = ttk.Frame(win, padding=10)
        frame.pack(pady=(10, 4))
        self.qr_canvas = tk.Canvas(
            frame,
            width=300,
            height=300,
            bg="white",
            highlightthickness=1,
            highlightbackground="#cccccc",
        )
        self.qr_canvas.pack()

        self.qr_status_var.set("正在获取二维码…")
        ttk.Label(
            win,
            textvariable=self.qr_status_var,
            wraplength=340,
            justify="center",
        ).pack(pady=(4, 12))
        ttk.Button(win, text="取消", command=self.stop_current).pack()
        self._center_child_window(win, minimum_width=390, minimum_height=470)
        win.grab_set()

    def _draw_qr(self, matrix):
        canvas = self.qr_canvas
        if canvas is None or not canvas.winfo_exists():
            return

        canvas.delete("all")
        size = len(matrix)
        if not size:
            return

        total = 284
        cell = max(1, total // size)
        qr_px = cell * size
        offset = (300 - qr_px) // 2

        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                if dark:
                    x1 = offset + x * cell
                    y1 = offset + y * cell
                    canvas.create_rectangle(
                        x1, y1, x1 + cell, y1 + cell,
                        fill="black", outline="black",
                    )

    def _close_qr_window(self):
        win = self.qr_window
        self.qr_window = None
        self.qr_canvas = None
        if win is None:
            return
        try:
            win.grab_release()
        except Exception:
            pass
        try:
            win.destroy()
        except Exception:
            pass

    def start_login(self):
        try:
            generation, cancel = self.tasks.start(TaskState.AUTHENTICATING)
        except RuntimeError:
            return

        self._open_qr_window()
        self._set_running(True)
        self.status_var.set("Authenticating")
        self.progress_detail_var.set("正在连接微博 Passport…")
        self._append_log("\n=== WEIBO TEXT ARCHIVER QR AUTH ===\n")

        self.worker = threading.Thread(
            target=self._login_worker,
            args=(generation, cancel),
            daemon=True,
        )
        self.worker.start()

    def _login_worker(self, generation: int, cancel: threading.Event):
        terminal_sent = False
        try:
            self._worker_log(generation, "开始微博二维码登录。")
            opener, jar, qrid, scan_url, csrf_headers = begin_qr_login()
            if cancel.is_set():
                raise Cancelled("任务已取消。")

            self._emit(generation, "qr_matrix", qr_matrix(scan_url))
            self._emit(generation, "qr_status", "等待扫码…")

            cookies = poll_qr_login(
                opener,
                jar,
                qrid,
                csrf_headers,
                cancel,
                lambda s: self._emit(generation, "qr_status", s),
            )
            if cancel.is_set():
                raise Cancelled("任务已取消。")

            save_cookies(COOKIE_FILE, cookies)
            self._worker_log(generation, "扫码登录成功；凭据已保存。")
            self._emit(generation, "ready", None)
            terminal_sent = True

        except Cancelled:
            self._emit(generation, "cancelled", None)
            terminal_sent = True

        except Exception as exc:
            detail = save_detailed_error("扫码登录", exc)
            friendly = "微博扫码登录没有完成。\n\n" + redact_text(exc)
            self._emit(generation, "error", (friendly, detail))
            terminal_sent = True

        finally:
            if not terminal_sent and self.tasks.accepts(generation):
                exc = RuntimeError("登录任务异常结束，未产生终态。")
                detail = save_detailed_error("登录终态保护", exc)
                self._emit(generation, "error", (str(exc), detail))

    def clear_login(self):
        if not messagebox.askyesno(
            "清除登录信息",
            "删除本工具保存的微博登录状态？\n\n不会影响手机微博或浏览器登录。",
        ):
            return
        try:
            COOKIE_FILE.unlink(missing_ok=True)
            if self.tasks.state is TaskState.READY:
                self.tasks.transition(TaskState.IDLE)
            self._refresh_login_status()
            messagebox.showinfo("完成", "登录状态已清除。")
        except Exception as exc:
            messagebox.showerror("清除失败", str(exc))

    # -------------------- Export --------------------

    def _selected_range(self, trial: bool) -> FetchRange:
        if trial:
            return FetchRange.trial(TEST_EXPORT_LIMIT)

        mode = RangeMode(self.range_mode_var.get())
        if mode is RangeMode.ALL:
            return FetchRange.all()

        if mode is RangeMode.RECENT:
            try:
                count = int(self.recent_count_var.get().strip())
            except ValueError:
                raise ValueError("“最近 N 条”必须填写整数。")
            if not 1 <= count <= 50000:
                raise ValueError("最近条数请输入 1～50000。")
            return FetchRange.recent(count)

        if mode is RangeMode.SINCE:
            raw = self.since_date_var.get().strip()
            try:
                d = date.fromisoformat(raw)
            except ValueError:
                raise ValueError("起始日期请按 YYYY-MM-DD 填写，例如 2024-01-01。")
            if d > date.today():
                raise ValueError("起始日期不能晚于今天。")
            return FetchRange.since_date(d)

        raise ValueError("未知导出范围。")

    def choose_output(self):
        folder = filedialog.askdirectory(
            title="选择 Markdown 保存位置",
            initialdir=self.output_var.get() or str(DEFAULT_OUTPUT_DIR),
        )
        if folder:
            self.output_var.set(folder)

    def _selected_export_selections(self) -> tuple[ExportSelection, ...]:
        return build_export_selections(
            include_full=self.full_output_var.get(),
            include_ai=self.ai_output_var.get(),
            include_custom=self.custom_output_var.get(),
            custom_options=self.custom_options,
            custom_filter=self.custom_filter,
        )

    def start_export(self, *, trial: bool):
        uid = extract_uid(self.uid_var.get())
        if not uid:
            messagebox.showwarning("请输入 UID", "请输入微博数字 UID，或粘贴包含数字 UID 的主页链接。")
            return

        if not COOKIE_FILE.exists():
            messagebox.showwarning("需要登录", "本工具不再匿名尝试抓取。请先扫码登录。")
            return

        try:
            fetch_range = self._selected_range(trial)
            export_selections = self._selected_export_selections()
        except ValueError as exc:
            messagebox.showwarning("导出设置有误", str(exc))
            return

        output_dir = Path(self.output_var.get().strip() or DEFAULT_OUTPUT_DIR).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("保存位置不可用", str(exc))
            return

        try:
            generation, cancel = self.tasks.start(TaskState.FETCHING)
        except RuntimeError:
            return

        self.uid_var.set(uid)
        self._set_running(True)
        self.status_var.set("Fetching")
        self.progress_detail_var.set(
            f"{fetch_range.label()} · 正在启动独立抓取核心…"
        )
        self._append_log(
            f"\n=== WEIBO TEXT ARCHIVER · UID {uid} · {fetch_range.label()} ===\n"
        )

        self.worker = threading.Thread(
            target=self._export_worker,
            args=(
                generation,
                cancel,
                uid,
                fetch_range,
                output_dir,
                export_selections,
            ),
            daemon=True,
        )
        self.worker.start()

    def _export_worker(
        self,
        generation: int,
        cancel: threading.Event,
        uid: str,
        fetch_range: FetchRange,
        output_dir: Path,
        export_selections: tuple[ExportSelection, ...],
    ):
        terminal_sent = False
        try:
            cookie = COOKIE_FILE.read_text(encoding="utf-8").strip()
            if "SUB=" not in cookie:
                raise RuntimeError("本机登录凭据缺少 SUB，请重新扫码登录。")

            def progress(message: str, data=None):
                self._worker_log(generation, message)
                self._emit(generation, "progress", (message, data or {}))

            client = WeiboClient(
                cookie_header=cookie,
                cancel_event=cancel,
                progress=progress,
            )
            archive = client.fetch(uid, fetch_range)

            if cancel.is_set():
                raise Cancelled("任务已取消。")

            self._emit(generation, "phase_export", None)
            save_normalized_archive(archive)
            if cancel.is_set():
                raise Cancelled("任务已取消。")

            def before_commit():
                if cancel.is_set():
                    raise Cancelled("任务已取消。")

            outputs = []
            failures = []
            for selection in export_selections:
                if cancel.is_set():
                    raise Cancelled("任务已取消。")

                render_archive = archive
                filter_report: CustomFilterReport | None = None
                selection_notice = None
                if selection.preset is ExportPreset.CUSTOM:
                    render_archive, filter_report = filter_archive(
                        archive,
                        selection.custom_filter,
                    )
                    selection_notice = filter_report_notice(filter_report)

                try:
                    output_path, stats = export_markdown(
                        render_archive,
                        output_dir,
                        selection.options,
                        selection.filename_suffix,
                        before_commit=before_commit,
                        selection_notice=selection_notice,
                    )
                except Cancelled:
                    raise
                except Exception as exc:
                    safe_error = redact_text(exc)
                    failures.append(
                        {
                            "label": selection.label,
                            "error": safe_error,
                        }
                    )
                    self._worker_log(
                        generation,
                        f"{selection.label} 本地输出失败：{safe_error}",
                    )
                    continue

                outputs.append(
                    {
                        "label": selection.label,
                        "path": output_path,
                        "options": selection.options,
                        "stats": stats,
                        "filter_report": filter_report,
                    }
                )
                self._worker_log(
                    generation,
                    f"已生成 {selection.label}：{output_path.name}",
                )

            if not outputs:
                failed_labels = "、".join(item["label"] for item in failures)
                raise RuntimeError(f"所有本地输出均失败：{failed_labels}")

            report = archive.report
            summary = {
                "profile": archive.profile,
                "report": report,
                "integrity": archive.integrity,
                "count": len(archive.posts),
                "outputs": outputs,
                "failures": failures,
            }
            self._emit(generation, "done", summary)
            terminal_sent = True

        except Cancelled:
            self._emit(generation, "cancelled", None)
            terminal_sent = True

        except RateLimited as exc:
            detail = save_detailed_error("微博访问限制", exc)
            self._emit(
                generation,
                "error",
                (
                    str(exc)
                    + "\n\n本工具不会在这种情况下生成“看起来完整”的导出文件。",
                    detail,
                ),
            )
            terminal_sent = True

        except NetworkError as exc:
            detail = save_detailed_error("网络请求", exc)
            self._emit(
                generation,
                "error",
                (str(exc) + "\n\n请检查网络或稍后重试。", detail),
            )
            terminal_sent = True

        except Exception as exc:
            detail = save_detailed_error("导出", exc)
            self._emit(
                generation,
                "error",
                ("导出没有完成。\n\n" + redact_text(exc), detail),
            )
            terminal_sent = True

        finally:
            if not terminal_sent and self.tasks.accepts(generation):
                exc = RuntimeError("导出任务异常结束，未产生终态。")
                detail = save_detailed_error("导出终态保护", exc)
                self._emit(generation, "error", (str(exc), detail))

    def stop_current(self):
        if self.tasks.state not in (
            TaskState.AUTHENTICATING,
            TaskState.FETCHING,
            TaskState.EXPORTING,
        ):
            return

        # Invalidate the current generation immediately. Any later HTTP/worker
        # event is structurally incapable of touching the new UI state.
        self.tasks.cancel()
        self._close_qr_window()
        self._set_running(False)
        self.status_var.set("Cancelled")
        self.progress_detail_var.set("已取消。正在退出的旧网络请求即使迟到也不会影响界面。")
        self.tasks.transition(TaskState.READY if COOKIE_FILE.exists() else TaskState.IDLE)

    # -------------------- Completion / exit --------------------

    def _show_completion(self, result: dict):
        profile = result["profile"]
        report = result["report"]
        integrity: ArchiveIntegrity = result["integrity"]
        outputs = result["outputs"]
        failures = result["failures"]

        if report.termination is Termination.NATURAL:
            termination = "自然结束"
        elif report.termination is Termination.TARGET_COUNT:
            termination = "达到指定条数"
        else:
            termination = "达到起始日期"

        lines = [f"{profile.screen_name} · UID {profile.id}", f"{result['count']:,} 条微博"]
        lines.extend(completion_integrity_lines(integrity))

        if report.oldest_reached and report.newest_reached:
            lines.append(
                f"{report.oldest_reached:%Y-%m-%d} → {report.newest_reached:%Y-%m-%d}"
            )

        lines.extend(
            [
                f"抓取终止：{termination}",
                f"页面：{report.pages_fetched} · HTTP 请求：{report.requests_made}",
            ]
        )

        if (
            report.termination is Termination.NATURAL
            and report.profile_statuses_count is not None
        ):
            lines.append(
                f"账号资料显示微博：{report.profile_statuses_count:,}（仅供参考，不等于可访问总量）"
            )

        lines.append("")
        lines.append("已生成：")
        for output in outputs:
            stats = output["stats"]
            output_line = (
                f"{output['label']}：{stats['count']:,} 条 · "
                f"{stats['output_bytes'] / 1024:.1f} KB"
            )
            filter_report: CustomFilterReport | None = output["filter_report"]
            if filter_report is not None:
                output_line += " · " + filter_report_notice(filter_report)
            lines.append(output_line)
            lines.append(f"  {output['path'].name}")

        if failures:
            lines.append("")
            lines.append("未生成：")
            for failure in failures:
                lines.append(f"{failure['label']}：{failure['error']}")

        win = tk.Toplevel(self)
        win.withdraw()
        win.title("导出完成")
        win.transient(self)
        win.resizable(False, False)

        body = ttk.Frame(win, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=(
                "✓ 导出完成（部分输出失败）"
                if failures
                else (
                    "✓ 导出完成（含完整性提醒）"
                    if integrity.incomplete_records
                    else "✓ 导出完成"
                )
            ),
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            body,
            text="\n".join(lines),
            justify="left",
        ).pack(anchor="w", pady=(10, 0))
        def launch(target: Path):
            ok, error = launch_with_system(target)
            if not ok:
                messagebox.showerror("无法打开", error, parent=win)

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(18, 0))
        ttk.Button(actions, text="关闭", command=win.destroy).pack(side="right")
        ttk.Button(
            actions,
            text="打开导出文件夹",
            command=lambda: launch(outputs[0]["path"].parent),
        ).pack(side="right", padx=(0, 8))
        for index, output in enumerate(reversed(outputs)):
            path = output["path"]
            text = "打开文件" if len(outputs) == 1 else f"打开{output['label']}"
            ttk.Button(
                actions,
                text=text,
                style="Primary.TButton" if index == 0 else "Quiet.TButton",
                command=lambda target=path: launch(target),
            ).pack(side="right", padx=(0, 8))
        self._center_child_window(win)

    def _on_close(self):
        if self.tasks.state in (
            TaskState.AUTHENTICATING,
            TaskState.FETCHING,
            TaskState.EXPORTING,
        ):
            if not messagebox.askyesno(
                "退出",
                "当前任务仍在运行。退出后本次任务将被取消，确定吗？",
            ):
                return
            self.tasks.cancel()
        self.destroy()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
