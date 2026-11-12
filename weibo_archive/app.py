from __future__ import annotations

import os
import queue
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import VERSION_DISPLAY
from .auth import QrCodeExpired, begin_qr_login, poll_qr_login, qr_matrix
from .client import WeiboClient
from .credentials import (
    clear_saved_login,
    has_saved_login,
    legacy_cookie_file,
    load_cookie_header,
    save_cookies,
)
from .export_options import (
    AIVisibilityOptions,
    CustomFilterOptions,
    CustomFilterReport,
    DateFormat,
    ExportLayout,
    ExportOptions,
    ExportPreset,
    ExportSelection,
    build_export_selections,
    filter_archive,
    filter_archive_visibility,
    filter_report_notice,
    filter_summary,
    options_summary,
    parse_filter_terms,
    visibility_scope_text,
)
from .ai_format import estimate_tokens, render_ai_markdown
from .exporter import export_ai_variant, export_markdown
from .models import (
    Archive,
    ArchiveIntegrity,
    FetchRange,
    RangeMode,
    Termination,
    VisibilityState,
)
from .network import AuthenticationExpired, Cancelled, NetworkError, RateLimited
from .offline_archive import (
    OfflineArchiveError,
    export_offline_html,
    export_portable_json,
    incremental_since_date,
    list_cached_archives,
    load_normalized_archive_file,
    merge_incremental_archives,
)
from .paths import (
    APP_ICON_PNG,
    default_output_dir,
    fallback_output_dir,
    prepare_output_dir,
)
from .security import redact_text, save_detailed_error
from .settings import UiPreferences, load_ui_settings, save_ui_settings
from .storage import save_normalized_archive
from .tasking import TaskManager, TaskState
from .update_check import (
    GITHUB_LATEST_RELEASE_PAGE,
    find_newer_github_version,
    launch_official_release_page,
)
from .image_ui import image_task_active, open_image_window


APP_TITLE = "Weibo Text Archiver"
APP_SUBTITLE = "把微博历史整理成便于长期保存与 AI 分析的本地归档"
TEST_EXPORT_LIMIT = 20
DEFAULT_WINDOW_WIDTH = 860
DEFAULT_COMPACT_HEIGHT = 760
MIN_WINDOW_HEIGHT = 650
# Tk geometry is the client area; leave room for the title bar and borders.
WINDOW_FRAME_ALLOWANCE = 48
# A full-archive run logs one line per page; keep the widget from growing forever.
MAX_LOG_LINES = 2000


@dataclass(frozen=True)
class ExportRequest:
    uid: str
    fetch_range: FetchRange
    output_dir: Path
    export_selections: tuple[ExportSelection, ...]
    fallback_dir: Path | None = None
    auth_retry_count: int = 0
    target_url: str | None = None
    incremental_cache_path: Path | None = None


def format_elapsed_time(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02}:{seconds:02}"
    return f"{minutes:02}:{seconds:02}"


def activity_status_text(read_count: int, elapsed_seconds: float) -> str:
    return f"已读取 {max(0, int(read_count)):,} 条 · 用时 {format_elapsed_time(elapsed_seconds)}"


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


def _enable_system_dpi_awareness() -> None:
    """Render at the display's real DPI instead of being bitmap-stretched by Windows."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def _ui_scale(widget) -> float:
    """Real pixels per 96-DPI design pixel; 1.0 unless Tk runs DPI-aware on a scaled display."""
    try:
        return max(1.0, round(widget.winfo_fpixels("1i") / 96.0, 2))
    except tk.TclError:
        return 1.0


def _scaled_distance(value, factor: float):
    parts = value if isinstance(value, (tuple, list)) else str(value).split()
    if not parts:
        return value
    scaled = tuple(round(float(str(part)) * factor) for part in parts)
    return scaled[0] if len(scaled) == 1 else scaled


def _scale_layout(root, factor: float) -> None:
    """Scale the pixel paddings of a built widget tree; fonts already follow Tk's own scaling.

    Child windows and canvases are left to their owners, and each tree must be scaled once.
    """
    if factor == 1.0:
        return
    pending = [root]
    while pending:
        widget = pending.pop()
        pending.extend(
            child
            for child in widget.winfo_children()
            if not isinstance(child, tk.Toplevel)
        )
        for option in ("padding", "wraplength"):
            try:
                value = widget.cget(option)
                if str(value).strip() not in ("", "0"):
                    widget.configure(**{option: _scaled_distance(value, factor)})
            except (tk.TclError, ValueError):
                pass
        manager = widget.winfo_manager()
        if manager not in ("pack", "grid"):
            continue
        info = widget.pack_info() if manager == "pack" else widget.grid_info()
        pads = {
            name: _scaled_distance(info[name], factor)
            for name in ("padx", "pady", "ipadx", "ipady")
            if name in info
        }
        (widget.pack_configure if manager == "pack" else widget.grid_configure)(**pads)


def _load_app_icon(window, icon_path: Path = APP_ICON_PNG):
    """Set the tracked icon when available; retain Tk's default on failure."""
    try:
        icon = tk.PhotoImage(master=window, file=str(icon_path))
        window.iconphoto(True, icon)
        return icon
    except (OSError, tk.TclError):
        return None


_PROFILE_TAB_SEGMENTS = frozenset(
    {
        "profile",
        "home",
        "feed",
        "info",
        "about",
        "follow",
        "follows",
        "following",
        "fans",
        "followers",
        "album",
        "photos",
        "photo",
        "video",
        "videos",
        "article",
        "articles",
        "weibo",
        "original",
        "super",
        "manage",
        "all",
        "mine",
    }
)


def _path_segments(parsed: urllib.parse.SplitResult) -> list[str]:
    return [
        urllib.parse.unquote(segment).strip()
        for segment in parsed.path.split("/")
        if segment.strip()
    ]


def extract_uid(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = _parse_weibo_url(raw)
    if parsed is not None:
        if is_obvious_single_post_url(raw):
            return ""
        segments = _path_segments(parsed)
        if segments:
            first = segments[0].casefold()
            if first in {"u", "profile"} and len(segments) >= 2:
                if re.fullmatch(r"\d{5,}", segments[1]):
                    return segments[1]
            elif first == "p" and len(segments) >= 2:
                matched = re.fullmatch(r"100505(\d{5,})", segments[1])
                if matched:
                    return matched.group(1)
            elif re.fullmatch(r"\d{5,}", segments[0]):
                if len(segments) == 1 or segments[1].casefold() in _PROFILE_TAB_SEGMENTS:
                    return segments[0]
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
        for key in ("uid", "user_id"):
            for candidate in query.get(key, ()):
                stripped = candidate.strip()
                if re.fullmatch(r"\d{5,}", stripped):
                    return stripped
        for candidate in query.get("containerid", ()):
            matched = re.fullmatch(r"(?:100505|230413|107603)(\d{5,})", candidate.strip())
            if matched:
                return matched.group(1)
        return ""
    m = re.search(r"(?<!\d)(\d{5,})(?!\d)", raw)
    return m.group(1) if m else ""


def _parse_weibo_url(value: str) -> urllib.parse.SplitResult | None:
    raw = (value or "").strip()
    if not raw:
        return None
    candidate = raw
    if "://" not in candidate and re.match(
        r"^(?:[A-Za-z0-9-]+\.)*weibo\.(?:com|cn)(?:/|$)",
        candidate,
        re.IGNORECASE,
    ):
        candidate = "https://" + candidate
    try:
        parsed = urllib.parse.urlsplit(candidate)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host != "weibo.com" and not host.endswith(".weibo.com"):
        if host != "weibo.cn" and not host.endswith(".weibo.cn"):
            return None
    return parsed


def is_obvious_single_post_url(value: str) -> bool:
    parsed = _parse_weibo_url(value)
    if parsed is None:
        return False
    segments = _path_segments(parsed)
    folded = {segment.casefold() for segment in segments}
    if folded & {"detail", "status"}:
        return True
    if len(segments) == 2 and re.fullmatch(r"\d{5,13}", segments[0]):
        second = segments[1]
        if second.casefold() not in _PROFILE_TAB_SEGMENTS:
            if (
                re.fullmatch(r"\d{14,}", second)
                or re.fullmatch(r"[A-Za-z0-9]{8,12}", second)
                or (re.fullmatch(r"[A-Za-z0-9]{7}", second) and any(ch.isdigit() for ch in second))
            ):
                return True
    return False


def is_weibo_url_without_uid(value: str) -> bool:
    parsed = _parse_weibo_url(value)
    if parsed is None or is_obvious_single_post_url(value):
        return False
    return not extract_uid(value)


def parse_fetch_range(
    mode_value: str | RangeMode,
    recent_raw: str = "",
    since_raw: str = "",
    *,
    trial: bool = False,
) -> FetchRange:
    if trial:
        return FetchRange.trial(TEST_EXPORT_LIMIT)

    try:
        mode = (
            mode_value
            if isinstance(mode_value, RangeMode)
            else RangeMode(mode_value)
        )
    except ValueError as exc:
        raise ValueError("未知导出范围。") from exc

    if mode is RangeMode.TRIAL:
        return FetchRange.trial(TEST_EXPORT_LIMIT)
    if mode is RangeMode.ALL:
        return FetchRange.all()
    if mode is RangeMode.RECENT:
        try:
            count = int(str(recent_raw).strip())
        except ValueError:
            raise ValueError("“最近 N 条”必须填写整数。") from None
        if not 1 <= count <= 50000:
            raise ValueError("最近条数请输入 1～50000。")
        return FetchRange.recent(count)
    if mode is RangeMode.SINCE:
        raw = str(since_raw).strip()
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            raise ValueError(
                "起始日期请按 YYYY-MM-DD 填写，例如 2024-01-01。"
            ) from None
        if d > date.today():
            raise ValueError("起始日期不能晚于今天。")
        return FetchRange.since_date(d)
    raise ValueError("未知导出范围。")


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


def ai_size_text(stats: dict) -> str:
    """Size of an AI output as a reader will meet it; the token figure is only a rough guide."""
    chars = int(stats["output_chars"])
    tokens = int(stats["estimated_tokens"])
    if chars < 10000:
        return f"约 {chars:,} 字符，粗估 {tokens:,} token"
    return f"约 {chars / 10000:.1f} 万字符，粗估 {tokens / 10000:.1f} 万 token"


def ai_variant_summary(results: list) -> str:
    """Describe freshly written smaller AI files for the variant dialog."""
    if len(results) <= 4:
        return "\n".join(
            f"{path.name}\n    {stats['count']:,} 条 · {ai_size_text(stats)}"
            for path, stats in results
        )
    total = {
        "output_chars": sum(stats["output_chars"] for _, stats in results),
        "estimated_tokens": sum(stats["estimated_tokens"] for _, stats in results),
    }
    largest = max((stats for _, stats in results), key=lambda s: s["output_chars"])
    return (
        f"已生成 {len(results)} 个文件，合计 {ai_size_text(total)}。\n"
        f"其中最大的一个：{ai_size_text(largest)}。\n"
        f"第一个文件：{results[0][0].name}"
    )


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


def render_export_selections(
    archive: Archive,
    export_selections: tuple[ExportSelection, ...],
    output_dir: Path,
    fallback_dir: Path | None,
    *,
    before_commit=None,
    log_fn=None,
) -> tuple[list[dict], list[dict], Path]:
    """Filter and write all requested Markdown export selections for an Archive."""
    outputs: list[dict] = []
    failures: list[dict] = []
    current_dir = output_dir
    current_fallback = fallback_dir

    for selection in export_selections:
        if before_commit is not None:
            before_commit()

        render_archive = archive
        filter_report: CustomFilterReport | None = None
        visibility_report = None
        selection_notice = None
        if selection.preset is ExportPreset.AI_COMPACT:
            render_archive, visibility_report = filter_archive_visibility(
                archive,
                selection.visibility_states,
            )
        elif selection.preset is ExportPreset.CUSTOM:
            render_archive, filter_report = filter_archive(
                archive,
                selection.custom_filter,
            )
            selection_notice = filter_report_notice(
                filter_report,
                english=selection.options.layout is ExportLayout.AI,
            )

        unknown_excluded = (
            visibility_report.unknown_excluded_count
            if visibility_report is not None
            else filter_report.unknown_visibility_count
            if filter_report is not None
            else 0
        )

        def write_selection(target_dir: Path):
            return export_markdown(
                render_archive,
                target_dir,
                selection.options,
                selection.filename_suffix,
                before_commit=before_commit,
                selection_notice=selection_notice,
                visibility_scope=visibility_scope_text(selection.visibility_states),
                unknown_visibility_excluded_count=unknown_excluded,
            )

        try:
            try:
                output_path, stats = write_selection(current_dir)
            except PermissionError:
                if current_fallback is None:
                    raise
                current_dir = prepare_output_dir(current_fallback)
                current_fallback = None
                if log_fn is not None:
                    log_fn(f"应用目录不可写，改用备用保存位置：{current_dir}")
                output_path, stats = write_selection(current_dir)
        except Cancelled:
            raise
        except Exception as exc:
            safe_error = redact_text(exc)
            failures.append({"label": selection.label, "error": safe_error})
            if log_fn is not None:
                log_fn(f"{selection.label} 本地输出失败：{safe_error}")
            continue

        current_fallback = None
        outputs.append(
            {
                "label": selection.label,
                "path": output_path,
                "options": selection.options,
                "stats": stats,
                "filter_report": filter_report,
                "visibility_report": visibility_report,
                # Lets the completion window write a smaller AI file
                # from this same snapshot without fetching again.
                "variant_source": (
                    {
                        "archive": render_archive,
                        "filename_suffix": selection.filename_suffix,
                        "selection_notice": selection_notice,
                        "visibility_scope": visibility_scope_text(
                            selection.visibility_states
                        ),
                        "unknown_visibility_excluded_count": unknown_excluded,
                    }
                    if selection.options.layout is ExportLayout.AI
                    else None
                ),
            }
        )
        if log_fn is not None:
            log_fn(f"已生成 {selection.label}：{output_path.name}")

    if not outputs:
        failed_labels = "、".join(item["label"] for item in failures)
        raise RuntimeError(f"所有本地输出均失败：{failed_labels}")
    return outputs, failures, current_dir


class ActivityIndicator(tk.Canvas):
    """Small indeterminate data-eater indicator driven only by Tk's event loop."""

    _INTERVAL_MS = 45
    _PASS_FRAMES = 56
    _LAST_FRAME = _PASS_FRAMES * 2
    _DOT_X = (46, 72, 98, 124, 150, 176, 202)

    def __init__(self, master):
        background = ttk.Style(master).lookup("TFrame", "background") or "#f0f0f0"
        self._scale = _ui_scale(master)
        super().__init__(
            master,
            width=round(240 * self._scale),
            height=round(16 * self._scale),
            background=background,
            borderwidth=0,
            highlightthickness=0,
        )
        self._after_id: str | None = None
        self._frame = 0
        self._running = False
        self.bind("<Destroy>", self._on_destroy, add="+")

    def start(self) -> None:
        if self._running:
            return
        try:
            if not self.winfo_exists():
                return
            self._running = True
            self._frame = 0
            self._draw_frame()
            self._schedule()
        except tk.TclError:
            self._running = False
            self._after_id = None

    def stop(self) -> None:
        self._running = False
        after_id, self._after_id = self._after_id, None
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
        try:
            self.delete("activity_item")
        except tk.TclError:
            pass

    def _schedule(self) -> None:
        if self._running and self._after_id is None:
            self._after_id = self.after(self._INTERVAL_MS, self._tick)

    def _tick(self) -> None:
        self._after_id = None
        if not self._running:
            return
        try:
            self._frame = (self._frame + 1) % self._LAST_FRAME
            self._draw_frame()
            self._schedule()
        except tk.TclError:
            self._running = False
            self._after_id = None

    def _draw_frame(self) -> None:
        pass_frame = self._frame % self._PASS_FRAMES
        moving_right = self._frame < self._PASS_FRAMES
        progress = pass_frame / (self._PASS_FRAMES - 1)
        character_x = round(12 + 216 * progress) if moving_right else round(228 - 216 * progress)
        center_y = 8
        radius = 5
        background = self.cget("background")

        self.delete("activity_item")
        visible_dots = (
            (x for x in self._DOT_X if x > character_x + radius)
            if moving_right
            else (x for x in self._DOT_X if x < character_x - radius)
        )
        for x in visible_dots:
            self.create_oval(
                x - 2,
                center_y - 2,
                x + 2,
                center_y + 2,
                fill="#888888",
                outline="",
                tags="activity_item",
            )

        self.create_oval(
            character_x - radius,
            center_y - radius,
            character_x + radius,
            center_y + radius,
            fill="#444444",
            outline="",
            tags="activity_item",
        )
        mouth_direction = 1 if moving_right else -1
        if (self._frame // 3) % 2 == 0:
            mouth_edge = character_x + mouth_direction * (radius + 1)
            self.create_polygon(
                character_x,
                center_y,
                mouth_edge,
                center_y - 4,
                mouth_edge,
                center_y + 4,
                fill=background,
                outline=background,
                tags="activity_item",
            )
        else:
            self.create_line(
                character_x,
                center_y,
                character_x + mouth_direction * (radius + 1),
                center_y,
                fill=background,
                width=1,
                tags="activity_item",
            )
        if self._scale != 1.0:
            self.scale("activity_item", 0, 0, self._scale, self._scale)

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self.stop()


class App(tk.Tk):
    def __init__(self, *, settings_path: Path | None = None):
        super().__init__()
        self._settings_path = settings_path
        self.ui_scale = _ui_scale(self)
        self._app_icon_image = _load_app_icon(self)
        self.title(f"{APP_TITLE} · {VERSION_DISPLAY}")
        self.geometry(
            f"{self._px(DEFAULT_WINDOW_WIDTH)}x{self._px(DEFAULT_COMPACT_HEIGHT)}"
        )
        self.minsize(self._px(780), self._px(MIN_WINDOW_HEIGHT))

        self.events: queue.Queue = queue.Queue()
        self.tasks = TaskManager()
        self.worker: threading.Thread | None = None

        self.qr_window: tk.Toplevel | None = None
        self.qr_canvas: tk.Canvas | None = None
        self.qr_status_var = tk.StringVar(value="正在获取二维码…")

        self.uid_var = tk.StringVar()
        self.default_output_dir = default_output_dir()
        self.fallback_output_dir = fallback_output_dir()
        prefs = load_ui_settings(self._settings_path)
        self.output_var = tk.StringVar(
            value=prefs.output_dir or str(self.default_output_dir)
        )
        self.range_mode_var = tk.StringVar(value=prefs.range_mode)
        self.recent_count_var = tk.StringVar(value=prefs.recent_count)
        self.since_date_var = tk.StringVar(value=prefs.since_date)
        self.full_output_var = tk.BooleanVar(value=prefs.full_output)
        self.ai_output_var = tk.BooleanVar(value=prefs.ai_output)
        self.custom_output_var = tk.BooleanVar(value=prefs.custom_output)
        self.custom_options = prefs.custom_options
        self.custom_filter = prefs.custom_filter
        self.ai_followers_var = tk.BooleanVar(value=prefs.ai_followers)
        self.ai_friends_var = tk.BooleanVar(value=prefs.ai_friends)
        self.ai_private_var = tk.BooleanVar(value=prefs.ai_private)
        self.content_summary_var = tk.StringVar()
        self.login_var = tk.StringVar()
        self._logged_in_uid = ""
        self._logged_in_name = ""
        self.status_var = tk.StringVar(value="就绪")
        self.progress_detail_var = tk.StringVar(value="等待任务")
        self.activity_status_var = tk.StringVar(value="")
        self.range_hint_var = tk.StringVar(value="默认：抓取当前接口能访问到的全部微博。")
        self.logs_visible = False
        self._compact_geometry: tuple[int, int, int, int] | None = None
        self._poll_after_id: str | None = None
        self._update_after_id: str | None = None
        self._activity_after_id: str | None = None
        self._activity_started_at: float | None = None
        self._activity_read_count = 0
        self._activity_timer_running = False
        self._pending_export_request: ExportRequest | None = None
        self._update_check_started = False
        self._reporting_callback_error = False

        self._configure_style()
        self._build_ui()
        self.update_idletasks()
        compact_height = _compact_window_height(
            self._px(DEFAULT_COMPACT_HEIGHT),
            self.winfo_reqheight(),
        )
        self._place_initial_window(compact_height)
        self._refresh_login_status()
        self._update_range_controls()
        self._update_content_controls()
        self._poll_after_id = self.after(100, self._poll_events)
        self._update_after_id = self.after(500, self._start_update_check)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -------------------- UI --------------------

    def _px(self, design_pixels: int) -> int:
        return round(design_pixels * self.ui_scale)

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
        style.configure("Primary.TButton", padding=(self._px(16), self._px(8)))
        style.configure("Quiet.TButton", padding=(self._px(10), self._px(6)))
        style.configure(
            "CompletionAction.TButton", padding=(self._px(16), self._px(8))
        )
        style.configure("Card.TFrame", relief="solid", borderwidth=1)
        style.configure("Muted.TLabel", foreground="#666666")
        style.configure(
            "Update.TLabel",
            foreground="#005fb8",
            font=("Microsoft YaHei UI", 9, "underline"),
        )

    def _build_ui(self):
        self.root_frame = ttk.Frame(self, padding=(24, 20, 24, 18))
        self.root_frame.pack(fill="both", expand=True)

        # Hero
        hero = ttk.Frame(self.root_frame)
        hero.pack(fill="x")
        ttk.Label(hero, text=APP_TITLE, style="Hero.TLabel").pack(side="left")
        version_panel = ttk.Frame(hero)
        version_panel.pack(side="right", anchor="n")
        ttk.Label(
            version_panel,
            text=VERSION_DISPLAY,
            style="Muted.TLabel",
        ).pack(anchor="e", pady=(8, 0))
        self.update_notice_label = ttk.Label(
            version_panel,
            text="",
            style="Update.TLabel",
            cursor="hand2",
            takefocus=True,
        )
        def open_release_page(_event=None):
            if not launch_official_release_page():
                messagebox.showerror(
                    "无法打开",
                    "Windows 无法打开官方发布页面，请手动访问：\n"
                    + GITHUB_LATEST_RELEASE_PAGE,
                    parent=self.root_frame,
                )

        for seq in ("<Button-1>", "<Return>", "<space>"):
            self.update_notice_label.bind(seq, open_release_page)
        ttk.Label(
            self.root_frame,
            text=APP_SUBTITLE,
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(0, 18))

        # Packed before the settings so that a window shorter than its content
        # keeps the actions and status visible and scrolls only the settings.
        self.footer_frame = ttk.Frame(self.root_frame)
        self.footer_frame.pack(side="bottom", fill="x")

        self.settings_area = ttk.Frame(self.root_frame)
        self.settings_area.pack(fill="both", expand=True)
        self.settings_canvas = tk.Canvas(
            self.settings_area,
            background=ttk.Style(self).lookup("TFrame", "background") or "#f0f0f0",
            borderwidth=0,
            highlightthickness=0,
            yscrollincrement=self._px(24),
        )
        self.settings_scroll = ttk.Scrollbar(
            self.settings_area,
            orient="vertical",
            command=self.settings_canvas.yview,
        )
        self.settings_canvas.configure(yscrollcommand=self.settings_scroll.set)
        self.settings_canvas.pack(side="left", fill="both", expand=True)
        self.settings_frame = ttk.Frame(self.settings_canvas)
        self._settings_window = self.settings_canvas.create_window(
            0, 0, anchor="nw", window=self.settings_frame
        )
        self.settings_canvas.bind("<Configure>", self._on_settings_canvas_configure)
        self.bind("<MouseWheel>", self._on_settings_mousewheel, add="+")
        self.bind("<FocusIn>", self._on_settings_focus_in, add="+")

        # Account
        self._section_label("登录账号")
        account = ttk.Frame(self.settings_frame, style="Card.TFrame", padding=12)
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
        self.my_account_btn = ttk.Button(
            account,
            text="填入我的账号",
            style="Quiet.TButton",
            command=self.identify_and_fill_my_account,
        )
        self.my_account_btn.pack(side="right", padx=(0, 8))

        # Target
        self._section_label("目标账号")
        target = ttk.Frame(self.settings_frame, style="Card.TFrame", padding=12)
        target.pack(fill="x", pady=(4, 14))

        ttk.Label(
            target,
            text="数字 UID、账号主页链接或个性域名链接（如 weibo.com/n/...）",
            style="Muted.TLabel",
        ).pack(anchor="w")
        self.uid_entry = ttk.Entry(target, textvariable=self.uid_var)
        self.uid_entry.pack(fill="x", pady=(7, 0))
        self.uid_entry.bind(
            "<Return>",
            lambda _event: self.start_export(trial=False),
            add="+",
        )

        # Range
        self._section_label("导出范围")
        range_card = ttk.Frame(self.settings_frame, style="Card.TFrame", padding=12)
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
            command=lambda: self._select_range_mode(
                RangeMode.RECENT, self.recent_entry
            ),
        ).pack(side="left")
        self.recent_entry = ttk.Entry(
            recent_row,
            width=8,
            textvariable=self.recent_count_var,
        )
        self.recent_entry.pack(side="left", padx=(8, 6))
        self.recent_entry.bind(
            "<Button-1>",
            lambda _event: self._select_range_mode(
                RangeMode.RECENT, self.recent_entry
            )
            if self.range_mode_var.get() != RangeMode.RECENT.value
            else None,
            add="+",
        )
        self.recent_entry.bind(
            "<Return>",
            lambda _event: self.start_export(trial=False),
            add="+",
        )
        self.recent_suffix_label = ttk.Label(recent_row, text="条")
        self.recent_suffix_label.pack(side="left")

        since_row = ttk.Frame(range_card)
        since_row.pack(fill="x", pady=2)
        ttk.Radiobutton(
            since_row,
            text="从",
            variable=self.range_mode_var,
            value=RangeMode.SINCE.value,
            command=lambda: self._select_range_mode(
                RangeMode.SINCE, self.since_entry
            ),
        ).pack(side="left")
        self.since_entry = ttk.Entry(
            since_row,
            width=14,
            textvariable=self.since_date_var,
        )
        self.since_entry.pack(side="left", padx=(8, 6))
        self.since_entry.bind(
            "<Button-1>",
            lambda _event: self._select_range_mode(
                RangeMode.SINCE, self.since_entry
            )
            if self.range_mode_var.get() != RangeMode.SINCE.value
            else None,
            add="+",
        )
        self.since_entry.bind(
            "<Return>",
            lambda _event: self.start_export(trial=False),
            add="+",
        )
        self.since_suffix_label = ttk.Label(since_row, text="起（YYYY-MM-DD）")
        self.since_suffix_label.pack(side="left")

        ttk.Label(
            range_card,
            textvariable=self.range_hint_var,
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(7, 0))

        # Output selection
        self._section_label("导出内容")
        content_card = ttk.Frame(self.settings_frame, style="Card.TFrame", padding=12)
        content_card.pack(fill="x", pady=(4, 14))

        preset_row = ttk.Frame(content_card)
        preset_row.pack(fill="x")
        self.output_buttons = []
        for text, variable in (
            ("AI 分析版", self.ai_output_var),
            ("完整归档", self.full_output_var),
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

        ai_scope_row = ttk.Frame(content_card)
        ai_scope_row.pack(fill="x", pady=(8, 0))
        ttk.Label(ai_scope_row, text="AI 分析范围：公开（默认）").pack(side="left")
        ttk.Label(ai_scope_row, text="另含：", style="Muted.TLabel").pack(
            side="left", padx=(16, 4)
        )
        self.ai_visibility_buttons = []
        for text, variable in (
            ("粉丝可见", self.ai_followers_var),
            ("好友圈", self.ai_friends_var),
            ("仅自己可见", self.ai_private_var),
        ):
            button = ttk.Checkbutton(
                ai_scope_row,
                text=text,
                variable=variable,
                command=self._update_content_controls,
            )
            button.pack(side="left", padx=(0, 12))
            self.ai_visibility_buttons.append(button)

        ttk.Label(
            content_card,
            text=(
                "限制可见内容可能属于不同受众语境。AI 分析版默认仅包含公开微博；"
                "完整归档仍保存本次登录会话返回的全部记录。"
            ),
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 0))

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
        self._section_label("保存位置")
        output_card = ttk.Frame(self.settings_frame, style="Card.TFrame", padding=12)
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
        actions = ttk.Frame(self.footer_frame)
        actions.pack(fill="x", pady=(2, 10))
        self.trial_btn = ttk.Button(
            actions,
            text="测试导出",
            style="Quiet.TButton",
            command=lambda: self.start_export(trial=True),
        )
        self.trial_btn.pack(side="left")
        self.trial_hint_label = ttk.Label(
            actions,
            text=f"快速验证 · 最近 {TEST_EXPORT_LIMIT} 条",
            style="Muted.TLabel",
        )
        self.trial_hint_label.pack(side="left", padx=(10, 0))

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
        self.image_backup_btn = ttk.Button(
            actions, text="图片备份…", style="Quiet.TButton",
            command=lambda: open_image_window(self),
        )
        self.image_backup_btn.pack(side="right", padx=(0, 8))
        self.offline_tools_btn = ttk.Button(
            actions,
            text="离线缓存…",
            style="Quiet.TButton",
            command=self._open_offline_tools_dialog,
        )
        self.offline_tools_btn.pack(side="right", padx=(0, 8))

        # Status
        status_card = ttk.Frame(self.footer_frame, style="Card.TFrame", padding=12)
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

        activity_row = ttk.Frame(status_card)
        activity_row.pack(fill="x", pady=(9, 6))
        self.activity = ActivityIndicator(activity_row)
        self.activity.pack(side="left")
        self.activity_status_label = ttk.Label(
            activity_row,
            textvariable=self.activity_status_var,
            style="Muted.TLabel",
        )
        self.activity_status_label.pack(side="left", padx=(10, 0))
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

        _scale_layout(self, self.ui_scale)
        # The canvas asks for the full settings height, so a tall enough
        # window looks exactly as it would without the scroll container.
        self.settings_frame.update_idletasks()
        self.settings_canvas.configure(
            width=self.settings_frame.winfo_reqwidth(),
            height=self.settings_frame.winfo_reqheight(),
            scrollregion=(
                0,
                0,
                self.settings_frame.winfo_reqwidth(),
                self.settings_frame.winfo_reqheight(),
            ),
        )

    def _section_label(self, text: str):
        ttk.Label(self.settings_frame, text=text, style="Section.TLabel").pack(anchor="w")

    def _on_settings_canvas_configure(self, event) -> None:
        self.settings_canvas.itemconfigure(self._settings_window, width=event.width)
        needed = self.settings_frame.winfo_reqheight() > event.height
        shown = bool(self.settings_scroll.winfo_manager())
        if needed and not shown:
            self.settings_scroll.pack(side="right", fill="y", padx=(self._px(6), 0))
        elif shown and not needed:
            self.settings_scroll.pack_forget()
            self.settings_canvas.yview_moveto(0)

    def _on_settings_mousewheel(self, event) -> None:
        if not self.settings_scroll.winfo_manager():
            return
        widget_path = str(event.widget)
        if widget_path != str(self.settings_canvas) and not widget_path.startswith(
            str(self.settings_frame)
        ):
            return
        self.settings_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _on_settings_focus_in(self, event) -> None:
        if not self.settings_scroll.winfo_manager():
            return
        widget = event.widget
        if not isinstance(widget, tk.Misc) or not str(widget).startswith(
            str(self.settings_frame)
        ):
            return
        try:
            total_height = self.settings_frame.winfo_height()
            canvas_height = self.settings_canvas.winfo_height()
            if total_height <= canvas_height or total_height <= 0:
                return
            widget_top = widget.winfo_rooty() - self.settings_frame.winfo_rooty()
            widget_bottom = widget_top + widget.winfo_height()
            first, last = self.settings_canvas.yview()
            view_top = first * total_height
            view_bottom = last * total_height
            if widget_top < view_top:
                self.settings_canvas.yview_moveto(max(0.0, widget_top / total_height))
            elif widget_bottom > view_bottom:
                self.settings_canvas.yview_moveto(
                    min(1.0, (widget_bottom - canvas_height) / total_height)
                )
        except tk.TclError:
            pass

    def _place_initial_window(self, wanted_height: int) -> None:
        """Fit the first window into the current monitor's usable area."""
        left, top, right, bottom = self._current_monitor_work_area()
        usable_height = max(1, bottom - top - self._px(WINDOW_FRAME_ALLOWANCE))
        height = min(wanted_height, usable_height)
        width = min(self._px(DEFAULT_WINDOW_WIDTH), max(1, right - left))
        self.minsize(
            min(self._px(780), width),
            min(self._px(MIN_WINDOW_HEIGHT), height),
        )
        x = left + (right - left - width) // 2
        y = top + (usable_height - height) // 2
        self.geometry(_geometry_spec(width, height, x, y))

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
            self.settings_area.pack_configure(expand=False)
            self.log_frame.pack(
                side="bottom",
                fill="both",
                expand=True,
                pady=(self._px(10), 0),
                before=self.footer_frame,
            )
            self.details_btn.configure(text="运行详情 ⌄")
            self.update_idletasks()

            width, compact_height, x, y = self._compact_geometry
            requested_height = max(
                self.winfo_reqheight(),
                compact_height + self.log_frame.winfo_reqheight() + self._px(10),
            )
            _, work_top, _, work_bottom = self._current_monitor_work_area()
            height, y = _fit_expanded_height(
                compact_height,
                requested_height,
                y,
                work_top,
                work_bottom - self._px(WINDOW_FRAME_ALLOWANCE),
            )
            self.geometry(_geometry_spec(width, height, x, y))
        else:
            self.log_frame.pack_forget()
            self.settings_area.pack_configure(expand=True)
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
        if not getattr(child, "_layout_scaled", False):
            child._layout_scaled = True
            _scale_layout(child, self.ui_scale)
        child.update_idletasks()
        work_left, work_top, work_right, work_bottom = self._current_monitor_work_area()
        width, height, x, y = _centered_child_geometry(
            self.winfo_rootx(),
            self.winfo_rooty(),
            self.winfo_width(),
            self.winfo_height(),
            max(self._px(minimum_width), child.winfo_reqwidth()),
            max(self._px(minimum_height), child.winfo_reqheight()),
            work_left,
            work_top,
            work_right,
            work_bottom,
        )
        child.geometry(_geometry_spec(width, height, x, y))
        child.deiconify()

    def _select_range_mode(
        self, mode: RangeMode, entry: ttk.Entry | None = None
    ) -> None:
        if self.tasks.state in (
            TaskState.AUTHENTICATING,
            TaskState.FETCHING,
            TaskState.EXPORTING,
        ):
            return
        self.range_mode_var.set(mode.value)
        self._update_range_controls()
        if entry is not None:
            try:
                entry.focus_set()
            except tk.TclError:
                pass

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
        if self.ai_output_var.get():
            extra_visibility = []
            if self.ai_followers_var.get():
                extra_visibility.append("粉丝可见")
            if self.ai_friends_var.get():
                extra_visibility.append("好友圈")
            if self.ai_private_var.get():
                extra_visibility.append("仅自己可见")
            ai_scope = "公开"
            if extra_visibility:
                ai_scope += "+" + "+".join(extra_visibility)
            selected.append(f"AI 分析版（{ai_scope}）")
        if self.full_output_var.get():
            selected.append("完整归档")
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
        ai_state = "normal" if self.ai_output_var.get() and not running else "disabled"
        for button in self.ai_visibility_buttons:
            button.configure(state=ai_state)

    def _open_custom_settings(self):
        current = self.custom_options
        current_filter = self.custom_filter
        win = tk.Toplevel(self)
        win.withdraw()
        win.title("自定义导出内容")
        win.transient(self)
        win.resizable(False, False)

        outer = ttk.Frame(win, padding=18)
        outer.pack(fill="both", expand=True)

        actions = ttk.Frame(outer)
        actions.pack(side="bottom", fill="x", pady=(16, 0))
        for column in range(2):
            actions.columnconfigure(column, weight=1, uniform="custom_actions")

        scroll_row = ttk.Frame(outer)
        scroll_row.pack(side="top", fill="both", expand=True)
        surface_bg = ttk.Style(win).lookup("TFrame", "background")
        canvas_kwargs = {"highlightthickness": 0, "borderwidth": 0}
        if surface_bg:
            canvas_kwargs["background"] = surface_bg
        canvas = tk.Canvas(scroll_row, **canvas_kwargs)
        scrollbar = ttk.Scrollbar(
            scroll_row, orient="vertical", command=canvas.yview
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = ttk.Frame(canvas, padding=(0, 0, 8, 0))
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")

        def sync_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def sync_canvas_width(event):
            canvas.itemconfigure(body_window, width=event.width)

        def on_mousewheel(event):
            if not canvas.winfo_exists():
                return
            delta = -int(event.delta / 120) if event.delta else 0
            if delta == 0 and event.delta:
                delta = -1 if event.delta > 0 else 1
            if delta:
                canvas.yview_scroll(delta, "units")

        body.bind("<Configure>", sync_scrollregion, add="+")
        canvas.bind("<Configure>", sync_canvas_width, add="+")
        win.bind("<MouseWheel>", on_mousewheel, add="+")

        layout_var = tk.StringVar(value=current.layout.value)
        source_var = tk.BooleanVar(value=current.include_source)
        location_var = tk.BooleanVar(value=current.include_location)
        engagement_var = tk.BooleanVar(value=current.include_engagement)
        date_var = tk.StringVar(value=current.date_format.value)
        original_var = tk.BooleanVar(value=current_filter.include_original)
        repost_var = tk.BooleanVar(value=current_filter.include_reposts)
        visibility_enabled_var = tk.BooleanVar(
            value=current_filter.visibility_filter_enabled
        )
        visibility_vars = {
            state: tk.BooleanVar(value=state in current_filter.visibilities)
            for state in (
                VisibilityState.PUBLIC,
                VisibilityState.FOLLOWERS,
                VisibilityState.FRIENDS,
                VisibilityState.PRIVATE,
                VisibilityState.UNKNOWN,
            )
        }
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
            text="AI 分析版",
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

        ttk.Label(body, text="可见范围（可选）", style="Section.TLabel").pack(
            anchor="w", pady=(12, 0)
        )
        visibility_enable = ttk.Checkbutton(
            body,
            text="按可见范围筛选",
            variable=visibility_enabled_var,
        )
        visibility_enable.pack(anchor="w", pady=(4, 0))
        visibility_row = ttk.Frame(body)
        visibility_row.pack(fill="x", pady=(4, 0))
        visibility_buttons = []
        for state, text in (
            (VisibilityState.PUBLIC, "公开"),
            (VisibilityState.FOLLOWERS, "粉丝可见"),
            (VisibilityState.FRIENDS, "好友圈"),
            (VisibilityState.PRIVATE, "仅自己可见"),
            (VisibilityState.UNKNOWN, "未知"),
        ):
            button = ttk.Checkbutton(
                visibility_row,
                text=text,
                variable=visibility_vars[state],
            )
            button.pack(side="left", padx=(0, 12))
            visibility_buttons.append(button)

        def update_visibility_controls():
            state = "normal" if visibility_enabled_var.get() else "disabled"
            for button in visibility_buttons:
                button.configure(state=state)

        visibility_enable.configure(command=update_visibility_controls)
        update_visibility_controls()

        ttk.Label(body, text="关键词（可选）", style="Section.TLabel").pack(
            anchor="w", pady=(12, 0)
        )
        keyword_text = tk.Text(
            body,
            height=3,
            width=52,
            wrap="word",
            font=("Microsoft YaHei UI", 9),
            relief="solid",
            borderwidth=1,
            highlightthickness=0,
        )
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
                    visibility_filter_enabled=visibility_enabled_var.get(),
                    visibilities=frozenset(
                        state
                        for state, variable in visibility_vars.items()
                        if variable.get()
                    ),
                )
            except (TypeError, ValueError) as exc:
                messagebox.showwarning("自定义设置有误", str(exc), parent=win)
                return

            self.custom_options = custom_options
            self.custom_filter = custom_filter
            self.custom_output_var.set(True)
            self._update_content_controls()
            self._save_preferences()
            win.destroy()

        ttk.Button(
            actions,
            text="保存设置",
            style="Primary.TButton",
            command=save,
        ).grid(row=0, column=0, sticky="ew", padx=4)
        ttk.Button(
            actions,
            text="取消",
            style="Primary.TButton",
            command=win.destroy,
        ).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=4,
        )
        win.bind("<Escape>", lambda _event: win.destroy(), add="+")
        if not getattr(win, "_layout_scaled", False):
            win._layout_scaled = True
            _scale_layout(win, self.ui_scale)
        win.update_idletasks()
        canvas.configure(
            width=body.winfo_reqwidth(),
            height=body.winfo_reqheight(),
        )
        sync_scrollregion()
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
            self.image_backup_btn,
            self.offline_tools_btn,
            self.login_btn,
            self.my_account_btn,
            self.clear_login_btn,
            *self.output_buttons,
            *self.ai_visibility_buttons,
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
            self.activity.start()
        else:
            self.activity.stop()
            self._stop_activity_timer()

    def _start_activity_timer(self) -> None:
        if self._activity_timer_running:
            return
        self._activity_started_at = time.monotonic()
        self._activity_read_count = 0
        self._activity_timer_running = True
        self._render_activity_status()
        self._schedule_activity_timer()

    def _schedule_activity_timer(self) -> None:
        if self._activity_timer_running and self._activity_after_id is None:
            self._activity_after_id = self.after(500, self._activity_timer_tick)

    def _activity_timer_tick(self) -> None:
        self._activity_after_id = None
        if not self._activity_timer_running:
            return
        try:
            self._render_activity_status()
            self._schedule_activity_timer()
        except tk.TclError:
            self._activity_timer_running = False
            self._activity_after_id = None

    def _render_activity_status(self) -> None:
        if self._activity_started_at is None:
            return
        elapsed = time.monotonic() - self._activity_started_at
        self.activity_status_var.set(
            activity_status_text(self._activity_read_count, elapsed)
        )

    def _set_activity_read_count(self, count: object) -> None:
        if self._activity_started_at is None:
            return
        try:
            value = max(0, int(count))
        except (TypeError, ValueError):
            return
        self._activity_read_count = max(self._activity_read_count, value)
        self._render_activity_status()

    def _stop_activity_timer(self) -> None:
        if self._activity_started_at is not None:
            self._render_activity_status()
        self._activity_timer_running = False
        after_id, self._activity_after_id = self._activity_after_id, None
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass

    # -------------------- Event channel --------------------

    def _start_update_check(self) -> None:
        self._update_after_id = None
        if self._update_check_started:
            return
        self._update_check_started = True
        threading.Thread(
            target=self._update_check_worker,
            daemon=True,
        ).start()

    def _update_check_worker(self) -> None:
        latest = find_newer_github_version(VERSION_DISPLAY)
        if latest is not None:
            self.events.put((None, "update_available", latest))

    def _emit(self, generation: int, kind: str, payload=None):
        self.events.put((generation, kind, payload))

    def _worker_log(self, generation: int, text: object):
        self._emit(generation, "log", redact_text(text))

    def _append_log(self, text: str):
        if text and not text.endswith("\n"):
            text += "\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        # Tk reports a phantom trailing line, so subtract it before counting.
        lines = int(self.log_text.index("end-1c").split(".")[0]) - 1
        excess = lines - MAX_LOG_LINES
        if excess > 0:
            self.log_text.delete("1.0", f"{excess + 1}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_events(self):
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
            self._poll_after_id = None
        try:
            while True:
                generation, kind, payload = self.events.get_nowait()

                if kind == "update_available":
                    self.update_notice_label.configure(text=f"发现新版本 {payload}")
                    self.update_notice_label.pack(anchor="e", pady=(self._px(2), 0))
                    continue

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
                        self._set_activity_read_count(data["posts"])
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
                        self.status_var.set("正在读取 · " + " · ".join(bits))

                elif kind == "qr_matrix":
                    self._draw_qr(payload)
                    if getattr(self, "qr_refresh_btn", None) is not None:
                        try:
                            self.qr_refresh_btn.configure(state="disabled")
                        except tk.TclError:
                            pass

                elif kind == "qr_status":
                    self.qr_status_var.set(payload)

                elif kind == "qr_expired":
                    self.tasks.terminal(generation, TaskState.ERROR)
                    self._set_running(False)
                    self.activity_status_var.set("")
                    self.status_var.set("二维码已过期")
                    self.progress_detail_var.set(
                        payload or "二维码已过期，请重新获取。"
                    )
                    self.qr_status_var.set("二维码已过期，请点击“重新获取”刷新。")
                    if getattr(self, "qr_refresh_btn", None) is not None:
                        try:
                            self.qr_refresh_btn.configure(state="normal")
                        except tk.TclError:
                            pass
                    self.tasks.transition(
                        TaskState.READY if has_saved_login() else TaskState.IDLE
                    )

                elif kind == "phase_export":
                    self.tasks.transition(TaskState.EXPORTING)
                    self.status_var.set("正在生成归档")
                    self.progress_detail_var.set("正在生成 Markdown 并验证输出…")

                elif kind == "ready":
                    self.tasks.terminal(generation, TaskState.READY)
                    self._close_qr_window()
                    self._set_running(False)
                    account_uid = str((payload or {}).get("uid") or "").strip()
                    account_name = str((payload or {}).get("screen_name") or "").strip()
                    recovery = bool(payload and payload.get("recovery"))
                    if account_uid:
                        self._logged_in_uid = account_uid
                        self._logged_in_name = account_name
                        if not recovery and not self.uid_var.get().strip():
                            self.uid_var.set(account_uid)
                    self._refresh_login_status()
                    self.activity_status_var.set("")
                    pending = self._pending_export_request if recovery else None
                    if pending is not None:
                        self._pending_export_request = None
                        resumed = replace(
                            pending,
                            auth_retry_count=pending.auth_retry_count + 1,
                        )
                        self.status_var.set("正在重新开始")
                        self.progress_detail_var.set(
                            "登录已更新，正在重新开始刚才的导出…"
                        )
                        self._launch_export_request(resumed)
                    else:
                        self.status_var.set("就绪")
                        self.progress_detail_var.set("登录成功，可以开始导出。")
                        messagebox.showinfo("登录成功", "微博登录状态已保存到本机。")

                elif kind == "account_identified":
                    self.tasks.terminal(generation, TaskState.READY)
                    self._set_running(False)
                    self.activity_status_var.set("")
                    account_uid = str((payload or {}).get("uid") or "").strip()
                    account_name = str((payload or {}).get("screen_name") or "").strip()
                    if account_uid:
                        self._logged_in_uid = account_uid
                        self._logged_in_name = account_name
                        self.uid_var.set(account_uid)
                    self._refresh_login_status()
                    self.status_var.set("就绪")
                    desc = (
                        f"{account_name} (UID {account_uid})"
                        if account_name
                        else f"UID {account_uid}"
                    )
                    self.progress_detail_var.set(f"已填入当前登录账号：{desc}")

                elif kind == "account_auth_expired":
                    self.tasks.terminal(generation, TaskState.ERROR)
                    self._stop_activity_timer()
                    self._set_running(False)
                    self._logged_in_uid = ""
                    self._logged_in_name = ""
                    self.login_var.set("○ 登录已过期，请重新扫码")
                    self.status_var.set("登录已过期")
                    self.progress_detail_var.set("当前会话已失效，请重新扫码登录。")
                    messagebox.showwarning(
                        "登录已过期",
                        "微博登录已过期，无法识别当前账号。请重新扫码登录。",
                    )
                    self.tasks.transition(TaskState.READY if has_saved_login() else TaskState.IDLE)

                elif kind == "resolved_uid":
                    resolved_uid = str(payload or "").strip()
                    if resolved_uid:
                        self.uid_var.set(resolved_uid)

                elif kind == "auth_expired":
                    self.tasks.terminal(generation, TaskState.ERROR)
                    self._stop_activity_timer()
                    self._set_running(False)
                    self._pending_export_request = payload
                    self.login_var.set("○ 登录已过期，请重新扫码")
                    self.status_var.set("登录已过期")
                    self.progress_detail_var.set("需要重新扫码；完成后将从头重新导出。")
                    messagebox.showinfo(
                        "登录已过期",
                        "微博登录已过期，需要重新扫码。\n\n"
                        "完成登录后，将自动重新开始刚才的导出。\n"
                        "已读取但尚未完成的内容不会被当作成功结果。",
                    )
                    self.tasks.transition(TaskState.READY)
                    self.start_login(recovery=True)

                elif kind == "auth_retry_failed":
                    self.tasks.terminal(generation, TaskState.ERROR)
                    self._pending_export_request = None
                    self._set_running(False)
                    self.status_var.set("登录更新失败")
                    self.progress_detail_var.set("重新登录后会话仍未被接受；本次导出已停止。")
                    messagebox.showerror(
                        "导出已停止",
                        "重新登录后微博仍未接受当前会话，本次导出已停止。\n"
                        "请稍后再试。",
                    )
                    self.tasks.transition(
                        TaskState.READY if has_saved_login() else TaskState.IDLE
                    )

                elif kind == "done":
                    self.tasks.terminal(generation, TaskState.DONE)
                    self._set_running(False)
                    if payload.get("output_dir"):
                        self.output_var.set(str(payload["output_dir"]))
                    integrity = payload["integrity"]
                    if payload["failures"]:
                        self.status_var.set("导出完成 · 输出提醒")
                        self.progress_detail_var.set(
                            f"已生成 {len(payload['outputs'])} 个文件；"
                            f"{len(payload['failures'])} 个本地输出失败。"
                        )
                    elif integrity.incomplete_records:
                        self.status_var.set("导出完成 · 完整性提醒")
                        self.progress_detail_var.set(
                            "导出已完成；"
                            f"其中 {integrity.incomplete_records:,} 条记录"
                            "含无法验证的历史内容。"
                        )
                    else:
                        self.status_var.set("导出完成")
                        self.progress_detail_var.set("本次导出已完成并写入 Markdown。")
                    self._show_completion(payload)
                    self.tasks.transition(TaskState.READY if has_saved_login() else TaskState.IDLE)

                elif kind == "error":
                    self.tasks.terminal(generation, TaskState.ERROR)
                    self._pending_export_request = None
                    self._close_qr_window()
                    self._set_running(False)
                    self.status_var.set("失败")
                    if self._activity_started_at is None:
                        self.activity_status_var.set("")
                    self.progress_detail_var.set(
                        "本次导出未完成，没有生成不完整的成功结果。"
                    )
                    self.toggle_logs(True)
                    friendly, detail_path = payload
                    messagebox.showerror(
                        "任务失败",
                        friendly
                        + "\n\n详细错误日志：\n"
                        + detail_path
                        + "\n\n请不要发送本机保存的登录凭据文件。",
                    )
                    self.tasks.transition(TaskState.READY if has_saved_login() else TaskState.IDLE)

                elif kind == "cancelled":
                    self.tasks.terminal(generation, TaskState.CANCELLED)
                    self._pending_export_request = None
                    self._close_qr_window()
                    self._set_running(False)
                    self.status_var.set("已取消")
                    if self._activity_started_at is None:
                        self.activity_status_var.set("")
                    self.progress_detail_var.set(
                        "任务已取消；未完成内容不会保存为成功归档。"
                    )
                    self.tasks.transition(TaskState.READY if has_saved_login() else TaskState.IDLE)

        except queue.Empty:
            pass
        finally:
            # A failing handler must not end polling: the window would look frozen.
            try:
                self._poll_after_id = self.after(100, self._poll_events)
            except tk.TclError:
                self._poll_after_id = None

    def report_callback_exception(self, exc_type, exc, tb):
        """A windowed build has no console: record callback failures and say so."""
        detail = save_detailed_error("界面回调", exc)
        if self._reporting_callback_error:
            return
        self._reporting_callback_error = True
        try:
            messagebox.showerror(
                "程序内部错误",
                "界面处理出现意外错误，当前任务状态可能不准确，建议重新启动程序。"
                "\n\n详细错误日志：\n" + detail,
            )
        except tk.TclError:
            pass
        finally:
            self._reporting_callback_error = False

    # -------------------- Login --------------------

    def _refresh_login_status(self):
        if has_saved_login():
            if self._logged_in_uid:
                display_name = self._logged_in_name or self._logged_in_uid
                self.login_var.set(f"● 已登录：{display_name} (UID {self._logged_in_uid})")
            else:
                self.login_var.set("● 已保存登录信息")
            if self.tasks.state is TaskState.IDLE:
                self.tasks.transition(TaskState.READY)
        else:
            self._logged_in_uid = ""
            self._logged_in_name = ""
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
            width=self._px(300),
            height=self._px(300),
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
        qr_actions = ttk.Frame(win)
        qr_actions.pack(pady=(0, 8))
        self.qr_refresh_btn = ttk.Button(
            qr_actions,
            text="重新获取",
            style="Primary.TButton",
            state="disabled",
            command=lambda: self.start_login(
                recovery=self._pending_export_request is not None
            ),
        )
        self.qr_refresh_btn.pack(side="left", padx=(0, 8))
        ttk.Button(
            qr_actions,
            text="取消",
            style="Quiet.TButton",
            command=self.stop_current,
        ).pack(side="left")
        win.bind("<Escape>", lambda _event: self.stop_current(), add="+")
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

        total = self._px(284)
        cell = max(1, total // size)
        qr_px = cell * size
        offset = (self._px(300) - qr_px) // 2

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
        self.qr_refresh_btn = None
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

    def start_login(self, *, recovery: bool = False):
        if image_task_active(self):
            messagebox.showinfo("任务正在运行", "图片备份正在运行，请完成或取消后再开始其他微博任务。")
            return
        if not recovery:
            self._pending_export_request = None
        try:
            generation, cancel = self.tasks.start(TaskState.AUTHENTICATING)
        except RuntimeError:
            return

        self._open_qr_window()
        self._stop_activity_timer()
        self._activity_started_at = None
        self._activity_read_count = 0
        self.activity_status_var.set("登录处理中")
        self._set_running(True)
        self.status_var.set("正在登录")
        self.progress_detail_var.set("正在连接微博登录服务…")
        self._append_log("\n=== WEIBO TEXT ARCHIVER QR AUTH ===\n")

        self.worker = threading.Thread(
            target=self._login_worker,
            args=(generation, cancel, recovery),
            daemon=True,
        )
        self.worker.start()

    def _login_worker(
        self,
        generation: int,
        cancel: threading.Event,
        recovery: bool = False,
    ):
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

            legacy_retired = save_cookies(cookies)
            self._worker_log(generation, "扫码登录成功；凭据已保存。")
            if not legacy_retired:
                self._worker_log(
                    generation,
                    f"警告：旧版明文登录文件 {legacy_cookie_file()} 无法清除，请手动删除。",
                )
            account_uid = ""
            account_name = ""
            try:
                identify_client = WeiboClient(
                    cookie_header=load_cookie_header(),
                    cancel_event=cancel,
                )
                account_uid, account_name = identify_client.identify_current_account()
                if account_uid:
                    self._worker_log(
                        generation,
                        f"已识别当前登录账号：{account_name} (UID {account_uid})"
                        if account_name
                        else f"已识别当前登录账号 UID：{account_uid}",
                    )
            except Exception:
                account_uid, account_name = "", ""
            self._emit(
                generation,
                "ready",
                {
                    "recovery": recovery,
                    "uid": account_uid,
                    "screen_name": account_name,
                },
            )
            terminal_sent = True

        except Cancelled:
            self._emit(generation, "cancelled", None)
            terminal_sent = True

        except QrCodeExpired as exc:
            self._worker_log(generation, str(exc))
            self._emit(generation, "qr_expired", str(exc))
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

    def identify_and_fill_my_account(self):
        if image_task_active(self):
            messagebox.showinfo("任务正在运行", "图片备份正在运行，请完成或取消后再试。")
            return
        if not has_saved_login():
            messagebox.showwarning("需要登录", "请先扫码登录，再识别当前登录账号。")
            return
        if self._logged_in_uid:
            self.uid_var.set(self._logged_in_uid)
            desc = (
                f"{self._logged_in_name} (UID {self._logged_in_uid})"
                if self._logged_in_name
                else f"UID {self._logged_in_uid}"
            )
            self.progress_detail_var.set(f"已填入当前登录账号：{desc}")
            return
        try:
            generation, cancel = self.tasks.start(TaskState.AUTHENTICATING)
        except RuntimeError:
            messagebox.showinfo("任务正在运行", "已有任务正在运行，请等待完成。")
            return

        self._stop_activity_timer()
        self._activity_started_at = None
        self._activity_read_count = 0
        self.activity_status_var.set("账号识别中")
        self._set_running(True)
        self.status_var.set("正在识别当前登录账号")
        self.progress_detail_var.set("正在查询已保存 Cookie 对应的微博账号…")
        self._append_log("\n=== 识别当前登录账号 ===\n")

        self.worker = threading.Thread(
            target=self._identify_account_worker,
            args=(generation, cancel),
            daemon=True,
        )
        self.worker.start()

    def _identify_account_worker(self, generation: int, cancel: threading.Event):
        terminal_sent = False
        try:
            cookie = load_cookie_header()
            client = WeiboClient(cookie_header=cookie, cancel_event=cancel)
            uid, screen_name = client.identify_current_account()
            self._worker_log(
                generation,
                f"已识别当前登录账号：{screen_name} (UID {uid})"
                if screen_name
                else f"已识别当前登录账号 UID：{uid}",
            )
            self._emit(
                generation,
                "account_identified",
                {"uid": uid, "screen_name": screen_name},
            )
            terminal_sent = True
        except Cancelled:
            self._emit(generation, "cancelled", None)
            terminal_sent = True
        except AuthenticationExpired:
            self._emit(generation, "account_auth_expired", None)
            terminal_sent = True
        except Exception as exc:
            detail = save_detailed_error("识别当前登录账号", exc)
            friendly = "未能识别当前登录账号。\n\n" + redact_text(exc)
            self._emit(generation, "error", (friendly, detail))
            terminal_sent = True
        finally:
            if not terminal_sent and self.tasks.accepts(generation):
                exc = RuntimeError("账号识别任务异常结束，未产生终态。")
                detail = save_detailed_error("账号识别终态保护", exc)
                self._emit(generation, "error", (str(exc), detail))

    def clear_login(self):
        if image_task_active(self):
            messagebox.showinfo("任务正在运行", "请先完成或取消图片备份，再清除登录信息。")
            return
        if not messagebox.askyesno(
            "清除登录信息",
            "删除本工具保存的微博登录状态？\n\n不会影响手机微博或浏览器登录。",
        ):
            return
        try:
            clear_saved_login()
            self._logged_in_uid = ""
            self._logged_in_name = ""
            if self.tasks.state is TaskState.READY:
                self.tasks.transition(TaskState.IDLE)
            self._refresh_login_status()
            messagebox.showinfo("完成", "登录状态已清除。")
        except Exception as exc:
            messagebox.showerror("清除失败", str(exc))

    # -------------------- Export --------------------

    def _selected_range(self, trial: bool) -> FetchRange:
        if trial:
            return parse_fetch_range(RangeMode.TRIAL, trial=True)

        mode = self.range_mode_var.get()
        recent_raw = (
            self.recent_count_var.get() if mode == RangeMode.RECENT.value else ""
        )
        since_raw = (
            self.since_date_var.get() if mode == RangeMode.SINCE.value else ""
        )
        return parse_fetch_range(mode, recent_raw, since_raw)

    def _current_preferences(self) -> UiPreferences:
        raw_output = self.output_var.get().strip()
        if not raw_output or raw_output == str(self.default_output_dir):
            saved_output = None
        else:
            saved_output = raw_output
        recent_raw = self.recent_count_var.get().strip()
        recent_count = recent_raw if recent_raw.isdigit() and int(recent_raw) > 0 else "1000"
        since_raw = self.since_date_var.get().strip()
        try:
            since_date = date.fromisoformat(since_raw).isoformat() if since_raw else ""
        except ValueError:
            since_date = ""
        return UiPreferences(
            output_dir=saved_output,
            range_mode=self.range_mode_var.get(),
            recent_count=recent_count,
            since_date=since_date,
            full_output=self.full_output_var.get(),
            ai_output=self.ai_output_var.get(),
            custom_output=self.custom_output_var.get(),
            ai_followers=self.ai_followers_var.get(),
            ai_friends=self.ai_friends_var.get(),
            ai_private=self.ai_private_var.get(),
            custom_options=self.custom_options,
            custom_filter=self.custom_filter,
        )

    def _save_preferences(self) -> None:
        save_ui_settings(self._current_preferences(), self._settings_path)

    def choose_output(self):
        folder = filedialog.askdirectory(
            title="选择 Markdown 保存位置",
            initialdir=self.output_var.get() or str(self.default_output_dir),
        )
        if folder:
            self.output_var.set(folder)
            self._save_preferences()

    def _selected_export_selections(self) -> tuple[ExportSelection, ...]:
        return build_export_selections(
            include_full=self.full_output_var.get(),
            include_ai=self.ai_output_var.get(),
            include_custom=self.custom_output_var.get(),
            custom_options=self.custom_options,
            custom_filter=self.custom_filter,
            ai_visibility=AIVisibilityOptions(
                include_followers=self.ai_followers_var.get(),
                include_friends=self.ai_friends_var.get(),
                include_private=self.ai_private_var.get(),
            ),
        )

    def _prepare_target_output_dir(self, *, parent=None) -> tuple[Path, Path | None] | None:
        requested_output_dir = Path(
            self.output_var.get().strip() or self.default_output_dir
        ).expanduser()
        is_default_output = requested_output_dir == self.default_output_dir
        fallback_dir = self.fallback_output_dir if is_default_output else None
        try:
            output_dir = prepare_output_dir(
                requested_output_dir,
                fallback=fallback_dir,
            )
        except OSError as exc:
            messagebox.showerror("保存位置不可用", str(exc), parent=parent)
            return None
        if output_dir != requested_output_dir:
            self.output_var.set(str(output_dir))
            fallback_dir = None
        return output_dir, fallback_dir

    def start_export(self, *, trial: bool):
        if image_task_active(self):
            messagebox.showinfo("任务正在运行", "图片备份正在运行，请完成或取消后再开始其他微博任务。")
            return
        target_value = self.uid_var.get()
        if is_obvious_single_post_url(target_value):
            messagebox.showwarning(
                "需要账号主页",
                "这里需要微博账号的数字 UID 或账号主页链接，\n"
                "不是单条微博链接。",
            )
            return
        uid = extract_uid(target_value)
        target_url: str | None = None
        if not uid:
            if is_weibo_url_without_uid(target_value):
                target_url = target_value.strip()
            else:
                messagebox.showwarning(
                    "请输入 UID",
                    "请输入微博数字 UID，或粘贴微博账号主页链接。",
                )
                return

        if not has_saved_login():
            messagebox.showwarning("需要登录", "本工具不再匿名尝试抓取。请先扫码登录。")
            return

        try:
            fetch_range = self._selected_range(trial)
            export_selections = self._selected_export_selections()
        except ValueError as exc:
            messagebox.showwarning("导出设置有误", str(exc))
            return

        prepared = self._prepare_target_output_dir()
        if prepared is None:
            return
        output_dir, fallback_dir = prepared

        request = ExportRequest(
            uid=uid,
            fetch_range=fetch_range,
            output_dir=output_dir,
            export_selections=export_selections,
            fallback_dir=fallback_dir,
            target_url=target_url,
        )
        self._save_preferences()
        self._launch_export_request(request)

    def _launch_export_request(self, request: ExportRequest) -> None:
        if image_task_active(self):
            return
        try:
            generation, cancel = self.tasks.start(TaskState.FETCHING)
        except RuntimeError:
            return

        if request.uid:
            self.uid_var.set(request.uid)
        self._start_activity_timer()
        self._set_running(True)
        mode_note = "（基于本地缓存增量更新）" if request.incremental_cache_path else ""
        if request.auth_retry_count:
            self.status_var.set("正在重新开始")
            self.progress_detail_var.set("登录已更新，正在重新开始刚才的导出…")
        elif not request.uid and request.target_url:
            self.status_var.set("正在解析账号链接")
            self.progress_detail_var.set(
                f"{request.fetch_range.label()} · 正在从个性主页链接识别 UID…"
            )
        else:
            self.status_var.set("正在读取")
            self.progress_detail_var.set(
                f"{request.fetch_range.label()}{mode_note} · 正在开始读取微博…"
            )
        target_desc = f"UID {request.uid}" if request.uid else (request.target_url or "")
        self._append_log(
            f"\n=== WEIBO TEXT ARCHIVER · {target_desc} · "
            f"{request.fetch_range.label()}{mode_note} ===\n"
        )

        self.worker = threading.Thread(
            target=self._export_worker,
            args=(generation, cancel, request),
            daemon=True,
        )
        self.worker.start()

    def _export_worker(
        self,
        generation: int,
        cancel: threading.Event,
        request: ExportRequest,
    ):
        terminal_sent = False
        output_dir = request.output_dir
        fallback_dir = request.fallback_dir
        try:
            existing_archive: Archive | None = None
            if request.incremental_cache_path is not None:
                existing_archive = load_normalized_archive_file(
                    request.incremental_cache_path
                )

            cookie = load_cookie_header()

            def progress(message: str, data=None):
                self._worker_log(generation, message)
                self._emit(generation, "progress", (message, data or {}))

            client = WeiboClient(
                cookie_header=cookie,
                cancel_event=cancel,
                progress=progress,
            )
            target_uid = request.uid
            if not target_uid and request.target_url:
                target_uid, resolved_name = client.resolve_profile_url(request.target_url)
                self._worker_log(
                    generation,
                    f"已从主页链接识别账号：{resolved_name} (UID {target_uid})"
                    if resolved_name
                    else f"已从主页链接识别 UID：{target_uid}",
                )
                self._emit(generation, "resolved_uid", target_uid)
                if cancel.is_set():
                    raise Cancelled("任务已取消。")

            if (
                existing_archive is not None
                and existing_archive.profile.id != target_uid
            ):
                raise OfflineArchiveError(
                    f"增量更新账号不一致：缓存为 {existing_archive.profile.id}，"
                    f"目标为 {target_uid}。"
                )

            try:
                fetched_archive = client.fetch(target_uid, request.fetch_range)
            finally:
                # Timing and restriction counters matter most when the fetch failed.
                performance = getattr(client, "performance", None)
                if performance is not None:
                    self._worker_log(generation, "\n" + performance.render())

            if cancel.is_set():
                raise Cancelled("任务已取消。")

            if existing_archive is not None:
                archive = merge_incremental_archives(existing_archive, fetched_archive)
                self._worker_log(
                    generation,
                    f"增量拉取完成（新扫 {len(fetched_archive.posts):,} 条）；"
                    f"已与本地缓存合并，共 {len(archive.posts):,} 条微博。",
                )
            else:
                archive = fetched_archive

            self._emit(generation, "phase_export", None)
            try:
                save_normalized_archive(archive)
            except OSError as exc:
                # The cache is never read back automatically without user intent;
                # losing it must not cost the export.
                self._worker_log(
                    generation,
                    f"本机规范化缓存未能保存，不影响 Markdown 导出：{exc}",
                )
            if cancel.is_set():
                raise Cancelled("任务已取消。")

            def before_commit():
                if cancel.is_set():
                    raise Cancelled("任务已取消。")

            outputs, failures, output_dir = render_export_selections(
                archive,
                request.export_selections,
                output_dir,
                fallback_dir,
                before_commit=before_commit,
                log_fn=lambda msg: self._worker_log(generation, msg),
            )

            report = archive.report
            summary = {
                "archive": archive,
                "profile": archive.profile,
                "report": report,
                "integrity": archive.integrity,
                "count": len(archive.posts),
                "outputs": outputs,
                "failures": failures,
                "output_dir": output_dir,
            }
            self._emit(generation, "done", summary)
            terminal_sent = True

        except AuthenticationExpired:
            if request.auth_retry_count == 0:
                self._emit(generation, "auth_expired", request)
            else:
                self._emit(generation, "auth_retry_failed", None)
            terminal_sent = True

        except Cancelled:
            self._emit(generation, "cancelled", None)
            terminal_sent = True

        except RateLimited as exc:
            detail = save_detailed_error("微博访问限制", exc)
            safe_error = redact_text(exc)
            self._emit(
                generation,
                "error",
                (
                    safe_error
                    + "\n\n本工具不会在这种情况下生成“看起来完整”的导出文件。",
                    detail,
                ),
            )
            terminal_sent = True

        except NetworkError as exc:
            detail = save_detailed_error("网络请求", exc)
            safe_error = redact_text(exc)
            self._emit(
                generation,
                "error",
                (safe_error + "\n\n请检查网络或稍后重试。", detail),
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
            if self.qr_window is not None:
                self._pending_export_request = None
                self._close_qr_window()
                self.status_var.set("已取消")
                self.progress_detail_var.set(
                    "任务已取消；未完成内容不会保存为成功归档。"
                )
            return

        # Invalidate the current generation immediately. Any later HTTP/worker
        # event is structurally incapable of touching the new UI state.
        draining = [w for w in getattr(self, "_image_text_draining", ()) if w.is_alive()]
        if getattr(self, "worker", None) is not None:
            draining.append(self.worker)
        self._image_text_draining = draining
        self.tasks.cancel()
        self._pending_export_request = None
        self._close_qr_window()
        self._set_running(False)
        self.status_var.set("已取消")
        self.progress_detail_var.set(
            "任务已取消；正在结束当前操作，未完成内容不会保存。"
        )
        self.tasks.transition(TaskState.READY if has_saved_login() else TaskState.IDLE)

    # -------------------- Completion / exit --------------------

    def _show_completion(self, result: dict):
        profile = result["profile"]
        report = result["report"]
        integrity: ArchiveIntegrity = result["integrity"]
        outputs = result["outputs"]
        failures = result["failures"]

        termination = report.termination.label()

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
            if "estimated_tokens" in stats:
                output_line += " · " + ai_size_text(stats)
            filter_report: CustomFilterReport | None = output.get("filter_report")
            if filter_report is not None:
                output_line += " · " + filter_report_notice(filter_report)
            visibility_report = output.get("visibility_report")
            if visibility_report is not None:
                output_line += (
                    f" · 可见范围匹配 {visibility_report.matched_count} / "
                    f"本次抓取 {visibility_report.fetched_count}"
                )
                if visibility_report.unknown_excluded_count:
                    output_line += (
                        f"；{visibility_report.unknown_excluded_count} 条可见范围未知，未纳入"
                    )
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
            wraplength=560,
        ).pack(anchor="w", pady=(10, 0))
        def launch(target: Path):
            ok, error = launch_with_system(target)
            if not ok:
                messagebox.showerror("无法打开", error, parent=win)

        variant_outputs = [
            output
            for output in outputs
            if output.get("variant_source") and "estimated_tokens" in output["stats"]
        ]
        archive_obj = result.get("archive")
        if variant_outputs or archive_obj is not None:
            smaller = ttk.Frame(body)
            smaller.pack(fill="x", pady=(14, 0))
            if variant_outputs:
                variant_arg = (
                    variant_outputs[0]
                    if len(variant_outputs) == 1
                    else variant_outputs
                )
                ttk.Button(
                    smaller,
                    text="生成更小的 AI 版…",
                    style="Quiet.TButton",
                    command=lambda: self._open_ai_variant_dialog(win, variant_arg),
                ).pack(side="left", padx=(0, 8))
            if archive_obj is not None:
                default_bundle_dir = result.get("output_dir") or outputs[0]["path"].parent
                ttk.Button(
                    smaller,
                    text="导出 JSON / HTML 图文版…",
                    style="Quiet.TButton",
                    command=lambda: self._open_bundle_export_dialog(
                        win, archive_obj, default_output_dir=default_bundle_dir
                    ),
                ).pack(side="left")
            if variant_outputs:
                ttk.Label(
                    smaller,
                    text="均直接基于本次抓取结果生成，不会重新联网。",
                    style="Muted.TLabel",
                ).pack(side="left", padx=(10, 0))

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(18, 0))
        action_count = len(outputs) + 2
        for column in range(action_count):
            actions.columnconfigure(column, weight=1, uniform="completion_actions")

        for index, output in enumerate(outputs):
            path = output["path"]
            text = "打开文件" if len(outputs) == 1 else f"打开{output['label']}"
            ttk.Button(
                actions,
                text=text,
                style="CompletionAction.TButton",
                command=lambda target=path: launch(target),
            ).grid(row=0, column=index, sticky="ew", padx=4)
        ttk.Button(
            actions,
            text="打开归档文件夹",
            style="CompletionAction.TButton",
            command=lambda: launch(outputs[0]["path"].parent),
        ).grid(row=0, column=len(outputs), sticky="ew", padx=4)
        ttk.Button(
            actions,
            text="关闭",
            style="CompletionAction.TButton",
            command=win.destroy,
        ).grid(
            row=0,
            column=len(outputs) + 1,
            sticky="ew",
            padx=4,
        )
        win.bind("<Escape>", lambda _event: win.destroy(), add="+")
        self._center_child_window(win)

    def _open_ai_variant_dialog(self, parent, output: dict | list[dict]) -> None:
        candidates = list(output) if isinstance(output, (list, tuple)) else [output]
        selected_index_var = tk.IntVar(value=0)
        current_output = candidates[0]
        win = tk.Toplevel(parent)
        win.withdraw()
        win.title("生成更小的 AI 版")
        win.transient(parent)
        win.resizable(False, False)

        body = ttk.Frame(win, padding=18)
        body.pack(fill="both", expand=True)
        choices = []
        size_var = tk.StringVar(
            value=f"当前 {current_output['label']}：{ai_size_text(current_output['stats'])}"
        )

        def select_candidate():
            nonlocal current_output
            current_output = candidates[selected_index_var.get()]
            size_var.set(
                f"当前 {current_output['label']}：{ai_size_text(current_output['stats'])}"
            )

        if len(candidates) > 1:
            ttk.Label(body, text="基于以下输出生成", style="Section.TLabel").pack(
                anchor="w"
            )
            source_row = ttk.Frame(body)
            source_row.pack(fill="x", pady=(4, 10))
            for idx, item in enumerate(candidates):
                btn = ttk.Radiobutton(
                    source_row,
                    text=item["label"],
                    variable=selected_index_var,
                    value=idx,
                    command=select_candidate,
                )
                btn.pack(side="left", padx=(0, 16))
                choices.append(btn)

        ttk.Label(
            body,
            textvariable=size_var,
        ).pack(anchor="w")
        ttk.Label(
            body,
            text="token 数只是粗略估计，不同模型的实际计数不同。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        limit_var = tk.StringVar(value="full")
        ttk.Label(body, text="转发原文（已确认的本人原文除外）", style="Section.TLabel").pack(
            anchor="w", pady=(14, 0)
        )
        for value, text in (
            ("full", "完整保留"),
            ("400", "每条最多保留前 400 字"),
            ("150", "每条最多保留前 150 字"),
            ("0", "不收录完整正文（未验证预览保留）"),
        ):
            button = ttk.Radiobutton(body, text=text, variable=limit_var, value=value)
            button.pack(anchor="w", pady=(3, 0))
            choices.append(button)

        part_var = tk.StringVar(value="none")
        custom_part_var = tk.StringVar(value="80000")
        ttk.Label(body, text="拆分成多个文件", style="Section.TLabel").pack(
            anchor="w", pady=(14, 0)
        )
        part_choices = []
        for value, text in (
            ("none", "不拆分"),
            ("200000", "每个文件约 20 万字符"),
            ("100000", "每个文件约 10 万字符"),
            ("50000", "每个文件约 5 万字符"),
        ):
            button = ttk.Radiobutton(body, text=text, variable=part_var, value=value)
            button.pack(anchor="w", pady=(3, 0))
            choices.append(button)
            part_choices.append(button)
        custom_part_row = ttk.Frame(body)
        custom_part_row.pack(anchor="w", pady=(3, 0))
        custom_part_radio = ttk.Radiobutton(
            custom_part_row,
            text="自定义字符上限",
            variable=part_var,
            value="custom",
        )
        custom_part_radio.pack(side="left")
        choices.append(custom_part_radio)
        part_choices.append(custom_part_radio)
        custom_part_entry = ttk.Entry(
            custom_part_row,
            textvariable=custom_part_var,
            width=10,
            state="disabled",
        )
        custom_part_entry.pack(side="left", padx=(8, 4))
        ttk.Label(custom_part_row, text="字/卷").pack(side="left")
        ttk.Label(
            body,
            text=(
                "顶层文字、转发链和经 UID 确认的本人原文完整保留；未知身份原文仍按设置处理。"
                "优先按月份拆分，单月过大时按记录拆分；单条过大时完整保留并标注。"
            ),
            style="Muted.TLabel",
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=(12, 0))

        result_var = tk.StringVar(value="")
        ttk.Label(
            body,
            textvariable=result_var,
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=(12, 0))

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(14, 0))
        results_queue = queue.Queue()
        cancel = threading.Event()
        running = False
        poll_id = None
        latest_generated_paths: list[Path] = []

        def sync_custom_part_state(*_args):
            if not running and part_var.get() == "custom":
                custom_part_entry.configure(state="normal")
            else:
                custom_part_entry.configure(state="disabled")

        for btn in part_choices:
            btn.configure(command=sync_custom_part_state)
        custom_part_entry.bind(
            "<Button-1>",
            lambda _event: (part_var.set("custom"), sync_custom_part_state())
            if not running
            else None,
            add="+",
        )

        def check_cancelled():
            if cancel.is_set():
                raise Cancelled("窗口已关闭，后续生成已取消。")

        def destroyed(event):
            nonlocal poll_id
            if event.widget is not win:
                return
            cancel.set()
            if poll_id is not None:
                win.after_cancel(poll_id)
                poll_id = None

        win.bind("<Destroy>", destroyed, add="+")

        def poll_result():
            nonlocal running, poll_id
            poll_id = None
            try:
                kind, payload = results_queue.get_nowait()
            except queue.Empty:
                poll_id = win.after(100, poll_result)
                return
            running = False
            for button in [generate_btn, *choices]:
                button.configure(state="normal")
            sync_custom_part_state()
            if kind == "error":
                latest_generated_paths.clear()
                result_var.set("生成失败；如已有部分文件生成，将保留在归档文件夹。")
                messagebox.showerror("生成失败", payload, parent=win)
                open_file_btn.configure(text="打开文件", state="disabled")
            else:
                latest_generated_paths[:] = [path for path, _ in payload]
                result_var.set("已生成：\n" + ai_variant_summary(payload))
                if latest_generated_paths:
                    open_file_btn.configure(
                        text="打开第 1 卷" if len(latest_generated_paths) > 1 else "打开文件",
                        state="normal",
                    )
                else:
                    open_file_btn.configure(text="打开文件", state="disabled")
            open_btn.configure(state="normal")
            self._center_child_window(win)

        def generate():
            nonlocal running, poll_id
            if running:
                return
            limit = None if limit_var.get() == "full" else int(limit_var.get())
            if part_var.get() == "none":
                part_chars = None
            elif part_var.get() == "custom":
                raw_part = custom_part_var.get().strip()
                if not raw_part.isdigit() or int(raw_part) < 2000:
                    messagebox.showwarning(
                        "拆分数设置有误",
                        "请输入不少于 2000 的整数字符上限。",
                        parent=win,
                    )
                    return
                part_chars = int(raw_part)
            else:
                part_chars = int(part_var.get())
            if limit is None and part_chars is None:
                messagebox.showinfo(
                    "无需生成",
                    "当前选择与已经导出的文件相同。",
                    parent=win,
                )
                return
            running = True
            latest_generated_paths.clear()
            for button in [generate_btn, open_btn, open_file_btn, *choices]:
                button.configure(state="disabled")
            sync_custom_part_state()
            result_var.set("正在生成…关闭此窗口可停止后续生成，已生成文件会保留。")

            target_output = current_output
            target_source = target_output["variant_source"]

            def worker():
                try:
                    results = export_ai_variant(
                        target_source["archive"], target_output["path"].parent, target_output["options"],
                        target_source["filename_suffix"], source_body_limit=limit,
                        max_part_chars=part_chars, selection_notice=target_source["selection_notice"],
                        visibility_scope=target_source["visibility_scope"],
                        unknown_visibility_excluded_count=target_source["unknown_visibility_excluded_count"],
                        before_commit=check_cancelled,
                    )
                except Cancelled:
                    return
                except Exception as exc:
                    detail = redact_text(exc)
                    if not isinstance(exc, OSError):
                        detail += "\n详细错误日志：\n" + save_detailed_error("AI 精简导出", exc)
                    results_queue.put(("error", detail))
                else:
                    results_queue.put(("done", results))

            # No Tk calls from this thread. On window/app destruction, cancellation
            # stops future commits; a non-daemon worker can clean up its temporary file.
            try:
                threading.Thread(target=worker, daemon=False).start()
            except Exception as exc:
                results_queue.put(("error", redact_text(exc)))
            poll_id = win.after(100, poll_result)

        generate_btn = ttk.Button(
            actions,
            text="生成",
            style="Primary.TButton",
            command=generate,
        )
        generate_btn.pack(side="right")
        ttk.Button(
            actions,
            text="关闭",
            style="Quiet.TButton",
            command=win.destroy,
        ).pack(side="right", padx=(0, 8))
        def open_output_dir():
            ok, error = launch_with_system(current_output["path"].parent)
            if not ok:
                messagebox.showerror("无法打开", error, parent=win)

        def open_first_file():
            if not latest_generated_paths:
                return
            ok, error = launch_with_system(latest_generated_paths[0])
            if not ok:
                messagebox.showerror("无法打开", error, parent=win)

        open_btn = ttk.Button(
            actions,
            text="打开归档文件夹",
            style="Quiet.TButton",
            state="disabled",
            command=open_output_dir,
        )
        open_btn.pack(side="left")
        open_file_btn = ttk.Button(
            actions,
            text="打开文件",
            style="Quiet.TButton",
            state="disabled",
            command=open_first_file,
        )
        open_file_btn.pack(side="left", padx=(8, 0))
        win.bind("<Escape>", lambda _event: win.destroy(), add="+")
        self._center_child_window(win)

    def _build_offline_variant_output(
        self,
        archive: Archive,
        output_dir: Path,
    ) -> dict:
        vis = AIVisibilityOptions(
            include_followers=self.ai_followers_var.get(),
            include_friends=self.ai_friends_var.get(),
            include_private=self.ai_private_var.get(),
        )
        visibility_states = vis.included_states
        filtered_archive, vis_report = filter_archive_visibility(
            archive, visibility_states
        )
        ai_options = ExportOptions(layout=ExportLayout.AI)
        text, _username, stats = render_ai_markdown(filtered_archive, ai_options)
        summary_stats = dict(stats)
        summary_stats.setdefault("output_chars", len(text))
        summary_stats.setdefault("estimated_tokens", estimate_tokens(text))
        summary_stats["count"] = len(filtered_archive.posts)
        return {
            "label": "AI 分析版（缓存）",
            "path": output_dir / f"{archive.profile.id}.md",
            "options": ai_options,
            "stats": summary_stats,
            "variant_source": {
                "archive": filtered_archive,
                "filename_suffix": "AI分析版",
                "selection_notice": None,
                "visibility_scope": visibility_scope_text(visibility_states),
                "unknown_visibility_excluded_count": vis_report.unknown_excluded_count,
            },
        }

    def _open_bundle_export_dialog(
        self,
        parent,
        archive: Archive,
        *,
        default_output_dir: Path | None = None,
    ) -> None:
        win = tk.Toplevel(parent)
        win.withdraw()
        win.title("导出 JSON / HTML 图文版")
        win.transient(parent)
        win.resizable(False, False)

        body = ttk.Frame(win, padding=18)
        body.pack(fill="both", expand=True)

        ttk.Label(
            body,
            text=f"{archive.profile.screen_name} · UID {archive.profile.id} · {len(archive.posts):,} 条微博",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w")

        initial_out = (
            default_output_dir
            or Path(self.output_var.get().strip() or self.default_output_dir).expanduser()
        )
        json_var = tk.BooleanVar(value=True)
        html_var = tk.BooleanVar(value=True)
        out_dir_var = tk.StringVar(value=str(initial_out))
        img_dir_var = tk.StringVar(value="")
        img_status_var = tk.StringVar()
        result_var = tk.StringVar(value="")
        latest_files: list[Path] = []

        ttk.Checkbutton(
            body,
            text="导出结构化 JSON 备份 (.json)",
            variable=json_var,
        ).pack(anchor="w", pady=(10, 0))
        ttk.Checkbutton(
            body,
            text="导出离线 HTML 图文浏览版 (.html，支持本地搜索/过滤与大图预览)",
            variable=html_var,
        ).pack(anchor="w", pady=(4, 0))

        ttk.Label(body, text="保存位置", style="Section.TLabel").pack(
            anchor="w", pady=(12, 0)
        )
        out_row = ttk.Frame(body)
        out_row.pack(fill="x", pady=(4, 0))
        ttk.Entry(out_row, textvariable=out_dir_var, width=48).pack(
            side="left", fill="x", expand=True
        )

        def browse_out():
            chosen = filedialog.askdirectory(
                parent=win,
                initialdir=out_dir_var.get().strip() or str(initial_out),
            )
            if chosen:
                out_dir_var.set(chosen)

        ttk.Button(
            out_row, text="浏览…", style="Quiet.TButton", command=browse_out
        ).pack(side="left", padx=(8, 0))

        ttk.Label(
            body,
            text="本地图片目录（留空则自动查找保存位置下的 WeiboImages_<UID>）",
            style="Section.TLabel",
        ).pack(anchor="w", pady=(12, 0))
        img_row = ttk.Frame(body)
        img_row.pack(fill="x", pady=(4, 0))
        ttk.Entry(img_row, textvariable=img_dir_var, width=48).pack(
            side="left", fill="x", expand=True
        )

        def browse_img():
            chosen = filedialog.askdirectory(
                parent=win,
                initialdir=out_dir_var.get().strip() or str(initial_out),
            )
            if chosen:
                img_dir_var.set(chosen)

        ttk.Button(
            img_row, text="浏览…", style="Quiet.TButton", command=browse_img
        ).pack(side="left", padx=(8, 0))

        ttk.Label(
            body,
            textvariable=img_status_var,
            style="Muted.TLabel",
            wraplength=480,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        def refresh_image_status(*_args):
            custom_img = img_dir_var.get().strip()
            base_out = Path(out_dir_var.get().strip() or initial_out).expanduser()
            candidate = (
                Path(custom_img).expanduser()
                if custom_img
                else base_out / f"WeiboImages_{archive.profile.id}"
            )
            if (candidate / "manifest.json").is_file():
                img_status_var.set(f"● 已检测到本地图片清单：{candidate}")
            else:
                img_status_var.set(
                    f"○ 未在 {candidate} 找到图片清单；HTML 仍可正常导出（仅显示文字与图片占位）。"
                )

        out_dir_var.trace_add("write", refresh_image_status)
        img_dir_var.trace_add("write", refresh_image_status)
        refresh_image_status()

        ttk.Label(
            body,
            textvariable=result_var,
            wraplength=480,
            justify="left",
        ).pack(anchor="w", pady=(10, 0))

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(14, 0))

        def open_first():
            if latest_files:
                ok, error = launch_with_system(latest_files[0])
                if not ok:
                    messagebox.showerror("无法打开", error, parent=win)

        open_btn = ttk.Button(
            actions,
            text="打开文件",
            style="Quiet.TButton",
            state="disabled",
            command=open_first,
        )
        open_btn.pack(side="left")

        def run_bundle_export():
            if not json_var.get() and not html_var.get():
                messagebox.showwarning(
                    "请选择格式", "请至少勾选 JSON 或 HTML 中的一种格式。", parent=win
                )
                return
            requested_dir = Path(
                out_dir_var.get().strip() or initial_out
            ).expanduser()
            try:
                target_dir = prepare_output_dir(requested_dir)
            except OSError as exc:
                messagebox.showerror("保存位置不可用", str(exc), parent=win)
                return

            generated: list[Path] = []
            notes: list[str] = []
            try:
                if html_var.get():
                    custom_img = img_dir_var.get().strip()
                    img_root = Path(custom_img).expanduser() if custom_img else None
                    html_path, html_stats = export_offline_html(
                        archive, target_dir, image_root=img_root
                    )
                    generated.append(html_path)
                    notes.append(
                        f"HTML 图文版：{html_path.name}（已关联本地图片 {html_stats['linked_images']} 张）"
                    )
                if json_var.get():
                    json_path, _json_stats = export_portable_json(archive, target_dir)
                    generated.append(json_path)
                    notes.append(f"结构化 JSON：{json_path.name}")
            except Exception as exc:
                messagebox.showerror("导出失败", redact_text(exc), parent=win)
                return

            latest_files[:] = generated
            open_btn.configure(state="normal")
            result_var.set("已生成：\n" + "\n".join(notes))
            self._center_child_window(win)

        ttk.Button(
            actions, text="开始导出", style="Primary.TButton", command=run_bundle_export
        ).pack(side="right")
        ttk.Button(
            actions, text="关闭", style="Quiet.TButton", command=win.destroy
        ).pack(side="right", padx=(0, 8))

        win.bind("<Escape>", lambda _event: win.destroy(), add="+")
        self._center_child_window(win)
        win.grab_set()

    def _open_offline_tools_dialog(self) -> None:
        win = tk.Toplevel(self)
        win.withdraw()
        win.title("本地缓存管理与图文导出")
        win.transient(self)
        win.resizable(True, True)
        win.minsize(self._px(680), self._px(440))

        body = ttk.Frame(win, padding=18)
        body.pack(fill="both", expand=True)

        header = ttk.Frame(body)
        header.pack(fill="x")
        ttk.Label(
            header,
            text="本机已保存的 Schema-5 归档快照",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left")

        list_frame = ttk.Frame(body)
        list_frame.pack(fill="both", expand=True, pady=(8, 0))
        tree = ttk.Treeview(
            list_frame,
            columns=("uid", "name", "count", "time", "range"),
            show="headings",
            selectmode="browse",
            height=6,
        )
        for col, title, width in (
            ("uid", "UID", 110),
            ("name", "昵称", 150),
            ("count", "微博数", 70),
            ("time", "快照时间", 145),
            ("range", "抓取范围", 130),
        ):
            tree.heading(col, text=title)
            tree.column(col, width=self._px(width), anchor="w")
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        detail_var = tk.StringVar(value="请选择一条本地缓存记录，或浏览外部备份的 last_success.json 文件。")
        ttk.Label(
            body,
            textvariable=detail_var,
            style="Muted.TLabel",
            wraplength=640,
            justify="left",
        ).pack(anchor="w", pady=(10, 0))

        path_by_iid: dict[str, Path] = {}
        loaded_by_path: dict[Path, Archive] = {}

        def populate_rows(extra_paths: list[Path] | None = None):
            tree.delete(*tree.get_children())
            path_by_iid.clear()
            rows = list_cached_archives()
            seen = {item.path.resolve() for item in rows}
            for item in rows:
                iid = tree.insert(
                    "",
                    "end",
                    values=(
                        item.uid,
                        item.screen_name,
                        f"{item.post_count:,}",
                        f"{item.fetched_at:%Y-%m-%d %H:%M}",
                        item.fetch_range_label,
                    ),
                )
                path_by_iid[iid] = item.path
            for custom in extra_paths or []:
                resolved = custom.resolve()
                if resolved in seen:
                    continue
                arch = loaded_by_path.get(custom)
                if arch is None:
                    continue
                seen.add(resolved)
                iid = tree.insert(
                    "",
                    0,
                    values=(
                        arch.profile.id,
                        arch.profile.screen_name,
                        f"{len(arch.posts):,}",
                        f"{arch.fetched_at:%Y-%m-%d %H:%M}",
                        arch.fetch_range.label(),
                    ),
                )
                path_by_iid[iid] = custom
            children = tree.get_children()
            if children:
                tree.selection_set(children[0])
                tree.focus(children[0])
                on_select()

        def current_selection() -> tuple[Path, Archive] | None:
            selected = tree.selection()
            if not selected:
                return None
            cache_path = path_by_iid.get(selected[0])
            if cache_path is None:
                return None
            arch = loaded_by_path.get(cache_path)
            if arch is None:
                try:
                    arch = load_normalized_archive_file(cache_path)
                except OfflineArchiveError as exc:
                    messagebox.showerror("缓存读取失败", str(exc), parent=win)
                    return None
                loaded_by_path[cache_path] = arch
            return cache_path, arch

        def on_select(_event=None):
            pair = current_selection()
            if pair is None:
                return
            cache_path, arch = pair
            since_hint = incremental_since_date(arch)
            integrity = arch.integrity
            detail_var.set(
                f"当前选中：{arch.profile.screen_name} (UID {arch.profile.id}) · "
                f"共 {len(arch.posts):,} 条（完整 {integrity.complete_records:,}，"
                f"不完整 {integrity.incomplete_records:,}）· "
                f"建议增量起始日：{since_hint:%Y-%m-%d}\n"
                f"缓存路径：{cache_path}"
            )

        tree.bind("<<TreeviewSelect>>", on_select, add="+")

        extra_loaded_paths: list[Path] = []

        def browse_external_json():
            chosen = filedialog.askopenfilename(
                parent=win,
                title="选择缓存 JSON 文件",
                filetypes=[("Schema-5 归档 JSON", "*.json"), ("所有文件", "*.*")],
            )
            if not chosen:
                return
            candidate = Path(chosen)
            try:
                arch = load_normalized_archive_file(candidate)
            except OfflineArchiveError as exc:
                messagebox.showerror("无法打开缓存", str(exc), parent=win)
                return
            loaded_by_path[candidate] = arch
            extra_loaded_paths.append(candidate)
            populate_rows(extra_loaded_paths)

        ttk.Button(
            header,
            text="浏览本地缓存 JSON…",
            style="Quiet.TButton",
            command=browse_external_json,
        ).pack(side="right")

        def reexport_markdown():
            pair = current_selection()
            if pair is None:
                messagebox.showinfo("未选择缓存", "请先选择一条缓存记录。", parent=win)
                return
            _path, arch = pair
            try:
                selections = self._selected_export_selections()
            except ValueError as exc:
                messagebox.showwarning("导出设置有误", str(exc), parent=win)
                return
            prepared = self._prepare_target_output_dir(parent=win)
            if prepared is None:
                return
            out_dir, fallback_dir = prepared
            try:
                outputs, failures, final_dir = render_export_selections(
                    arch,
                    selections,
                    out_dir,
                    fallback_dir,
                    log_fn=self._append_log,
                )
            except Exception as exc:
                messagebox.showerror("重新导出失败", redact_text(exc), parent=win)
                return
            summary = {
                "archive": arch,
                "profile": arch.profile,
                "report": arch.report,
                "integrity": arch.integrity,
                "count": len(arch.posts),
                "outputs": outputs,
                "failures": failures,
                "output_dir": final_dir,
            }
            self._show_completion(summary)

        def open_variant():
            pair = current_selection()
            if pair is None:
                messagebox.showinfo("未选择缓存", "请先选择一条缓存记录。", parent=win)
                return
            _path, arch = pair
            prepared = self._prepare_target_output_dir(parent=win)
            if prepared is None:
                return
            out_dir, _ = prepared
            variant_out = self._build_offline_variant_output(arch, out_dir)
            self._open_ai_variant_dialog(win, variant_out)

        def open_bundle():
            pair = current_selection()
            if pair is None:
                messagebox.showinfo("未选择缓存", "请先选择一条缓存记录。", parent=win)
                return
            _path, arch = pair
            prepared = self._prepare_target_output_dir(parent=win)
            if prepared is None:
                return
            out_dir, _ = prepared
            self._open_bundle_export_dialog(win, arch, default_output_dir=out_dir)

        def launch_incremental():
            pair = current_selection()
            if pair is None:
                messagebox.showinfo("未选择缓存", "请先选择一条缓存记录。", parent=win)
                return
            if image_task_active(self) or self.tasks.state not in (
                TaskState.IDLE,
                TaskState.READY,
                TaskState.CANCELLED,
            ):
                messagebox.showinfo("任务正在运行", "当前有其他任务正在运行，请等待完成。", parent=win)
                return
            if not has_saved_login():
                messagebox.showwarning("需要登录", "增量抓取需要微博登录凭据，请先扫码登录。", parent=win)
                return
            cache_path, arch = pair
            since_date = incremental_since_date(arch)
            if not messagebox.askyesno(
                "确认增量更新",
                f"将从 {since_date:%Y-%m-%d} 起抓取 {arch.profile.screen_name} (UID {arch.profile.id}) "
                "的最新微博（含 1 天重叠窗口防漏抓），并与当前缓存合并去重。\n\n确定开始吗？",
                parent=win,
            ):
                return
            try:
                selections = self._selected_export_selections()
            except ValueError as exc:
                messagebox.showwarning("导出设置有误", str(exc), parent=win)
                return
            prepared = self._prepare_target_output_dir(parent=win)
            if prepared is None:
                return
            out_dir, fallback_dir = prepared
            self._save_preferences()
            request = ExportRequest(
                uid=arch.profile.id,
                fetch_range=FetchRange.since_date(since_date),
                output_dir=out_dir,
                export_selections=selections,
                fallback_dir=fallback_dir,
                incremental_cache_path=cache_path,
            )
            win.destroy()
            self._launch_export_request(request)

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(14, 0))
        ttk.Button(
            actions, text="重导 Markdown", style="Quiet.TButton", command=reexport_markdown
        ).pack(side="left")
        ttk.Button(
            actions, text="AI 分卷 / 精简版…", style="Quiet.TButton", command=open_variant
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            actions, text="JSON / HTML 图文版…", style="Quiet.TButton", command=open_bundle
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            actions, text="增量更新 →", style="Primary.TButton", command=launch_incremental
        ).pack(side="right")
        ttk.Button(
            actions, text="关闭", style="Quiet.TButton", command=win.destroy
        ).pack(side="right", padx=(0, 8))

        populate_rows()
        win.bind("<Escape>", lambda _event: win.destroy(), add="+")
        self._center_child_window(win)
        win.grab_set()

    def _on_close(self):
        if image_task_active(self):
            self._image_window.request_close(close_app=True)
            return
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
        self._save_preferences()
        self.destroy()

    def destroy(self):
        image_window = getattr(self, "_image_window", None)
        if image_window is not None:
            image_window.shutdown()
        if hasattr(self, "activity"):
            self.activity.stop()
        self._stop_activity_timer()
        after_id, self._poll_after_id = self._poll_after_id, None
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
        update_after_id, self._update_after_id = self._update_after_id, None
        if update_after_id is not None:
            try:
                self.after_cancel(update_after_id)
            except tk.TclError:
                pass
        self._app_icon_image = None
        super().destroy()
        for value in self.__dict__.values():
            if isinstance(value, tk.Variable):
                value._tk = None


def main():
    _enable_system_dpi_awareness()
    App().mainloop()


if __name__ == "__main__":
    main()
