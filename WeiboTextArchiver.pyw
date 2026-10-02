from __future__ import annotations


def _show_startup_error() -> None:
    """Show a credential-safe fallback when pythonw has no visible console."""
    root = None
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Weibo Text Archiver",
            "程序启动失败。\n\n"
            "请使用诊断启动方式查看详细信息：\n"
            "python -m weibo_archive.app",
            parent=root,
        )
    except Exception:
        # If Tk itself cannot start, the documented console command remains available.
        pass
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def main() -> int:
    try:
        from weibo_archive.app import main as run_app

        run_app()
    except Exception:
        _show_startup_error()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
