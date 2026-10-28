"""
Evan_Gerber_View: 程序入口 (独立 Gerber 查看器)
==============================================
仅包含 Gerber / NC Drill 查看器, 不依赖 BRD-AI 的其它模块。

运行:
    python Evan_Gerber_View.py
"""
import sys                       # 提供 sys.path，用于修改模块搜索路径
import os                        # 提供 os.path 系列函数，用于解析文件路径

# ---------------------------------------------------------------------------
# 把“当前脚本所在目录”插入到模块搜索路径的最前面（索引 0）。
#   __file__            : 当前脚本自身的路径（可能是相对路径）
#   os.path.abspath(..) : 转成绝对路径，避免受“当前工作目录”影响
#   os.path.dirname(..) : 取该路径的父目录，即本脚本所在文件夹
#   sys.path.insert(0,..): 放在最前面，保证优先从本目录导入同目录模块
# 目的：无论用户从哪个目录启动脚本，都能 import 到与本文件同级的
#       branding.py / win_dpi.py 等本地模块。
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入 Tkinter（Python 标准库的 GUI 工具包），别名 tk 便于简写
import tkinter as tk

# ---------------------------------------------------------------------------
# 从本地模块 branding 导入“品牌/外观”相关常量：
#   APP_USER_MODEL_ID : Windows 的 AppUserModelID 字符串，
#                       用于任务栏图标分组、跳转列表(jump list)、通知归属等
#   icon_ico          : 窗口图标 .ico 文件路径（Windows 用）
#   icon_png          : 图标 .png 文件路径（跨平台/其他用途）
#   win_title         : 主窗口标题文字
# ---------------------------------------------------------------------------
from branding import APP_USER_MODEL_ID, icon_ico, icon_png, win_title

# ---------------------------------------------------------------------------
# 从本地模块 win_dpi 导入 Windows 高 DPI 相关的工具函数：
#   apply_tk_scaling      : 根据系统 DPI 调整 Tk 的缩放比例(避免界面模糊/过小)
#   enable_dpi_awareness  : 声明进程为 DPI 感知(让 Windows 不自动位图拉伸)
#   set_app_user_model_id : 在运行时设置进程的 AppUserModelID
#                           (通常需在创建窗口前调用)
# 括号内换行只是为了让这一行不要过长，功能与单行 import 相同。
# ---------------------------------------------------------------------------
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