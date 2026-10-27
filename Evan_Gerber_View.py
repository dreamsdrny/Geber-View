"""
Evan_Gerber_View: 程序入口 (独立 Gerber 查看器)
==============================================
仅包含 Gerber / NC Drill 查看器, 不依赖 BRD-AI 的其它模块。

运行:
    python Evan_Gerber_View.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

from branding import APP_USER_MODEL_ID, icon_ico, icon_png, win_title
from win_dpi import (apply_tk_scaling, enable_dpi_awareness,
                     set_app_user_model_id)


def apply_icon(root) -> None:
    """任务栏 / 标题栏图标: 多尺寸 ico + png"""
    try:
        ico = icon_ico()
        if os.path.exists(ico):
            for fn in ("iconbitmap", "wm_iconbitmap"):
                try:
                    getattr(root, fn)(ico)
                except Exception:  # noqa: BLE001
                    pass
            try:
                root.iconbitmap(default=ico)
            except Exception:  # noqa: BLE001
                pass
        png = icon_png()
        if os.path.exists(png):
            from tkinter import PhotoImage
            try:
                img = PhotoImage(file=png)
                root.iconphoto(True, img)
                root._ebv_icon = img      # 保留引用防 GC
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def main():
    try:
        enable_dpi_awareness()
        set_app_user_model_id(APP_USER_MODEL_ID)
    except Exception:  # noqa: BLE001
        pass

    from gerber_viewer import GerberViewer

    root = tk.Tk()
    root.withdraw()                       # 主窗口隐藏, 只显示查看器 Toplevel
    root.title(win_title())
    try:
        apply_tk_scaling(root)
    except Exception:  # noqa: BLE001
        pass
    apply_icon(root)

    viewer = GerberViewer(root)

    def _close():
        """关闭查看器窗口时同时退出程序"""
        try:
            viewer._on_close()
        finally:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass

    viewer.win.protocol("WM_DELETE_WINDOW", _close)
    root.mainloop()


if __name__ == "__main__":
    main()