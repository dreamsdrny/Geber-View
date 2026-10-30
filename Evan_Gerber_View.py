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
        # ---- 第一步：优先用 .ico（Windows 原生支持多尺寸，任务栏/标题栏自适应）----
        ico = icon_ico()                      # 外部函数：返回 ico 文件的绝对路径
        if os.path.exists(ico):               # 文件不存在就跳过，不报错
            # iconbitmap 是 Tk 的窗口图标接口；不同 Tk 版本方法名略有差异，
            # 所以 iconbitmap / wm_iconbitmap 两个名字都试一遍，失败就忽略
            for fn in ("iconbitmap", "wm_iconbitmap"):
                try:
                    getattr(root, fn)(ico)    # 给当前 root 窗口设置标题栏/任务栏图标
                except Exception:  # noqa: BLE001
                    pass                      # 某个名字不支持就算了，继续试下一个
            # default=ico 表示把该 ico 设为 Tk 的“默认图标”，
            # 之后新建的 Toplevel 窗口不单独设置也会自动继承
            try:
                root.iconbitmap(default=ico)
            except Exception:  # noqa: BLE001
                pass

        # ---- 第二步：再用 .png 兜底（跨平台更可靠，Linux 下尤其有用）----
        png = icon_png()                      # 外部函数：返回 png 文件路径
        if os.path.exists(png):
            from tkinter import PhotoImage     # 延迟导入，避免顶层依赖 tkinter
            try:
                img = PhotoImage(file=png)     # 读成 Tk 图像对象
                root.iconphoto(True, img)      # True=同时作用于后续所有窗口
                root._ebv_icon = img           # 关键：把引用挂到 root 上，
                                               # 否则 img 被 GC 回收后图标会变空白
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass                                   # 整个函数静默失败：图标只是锦上添花，
                                               # 绝不能因为它导致程序启动崩溃


def _selftest(files):
    """无界面自检: 解析指定文件并把后端/耗时写到 exe 旁的 selftest.txt

    用法: Evan_Gerber_View.exe --selftest a.gbr b.gbr
    """
    import time
    lines = []
    try:
        import gerber_native as _n
        lines.append("native=%s (%s)" % (_n.available(), _n.status()[1] or "ok"))
    except Exception as exc:                       # noqa: BLE001
        lines.append("native 加载失败: %s" % exc)
    import gerber_reader
    for p in files:
        t0 = time.perf_counter()
        d = gerber_reader.parse_gerber(p)
        dt = (time.perf_counter() - t0) * 1000.0
        tag = "?"
        try:
            import gerber_native as _n2
            t1 = time.perf_counter()
            nd = _n2.parse(p, None)
            tag = ("NATIVE %.1fms" % ((time.perf_counter() - t1) * 1000.0)) \
                if nd is not None else ("fallback(%s)" % _n2._LAST)
        except Exception as exc:                   # noqa: BLE001
            tag = "err %s" % exc
        lines.append("%-24s %7.1f ms  prims=%-7d %-18s bbox=%s" % (
            os.path.basename(p), dt, d.get("feature_count"), tag, d.get("bbox")))
    out = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                       "selftest.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    if "--selftest" in sys.argv:
        _selftest([a for a in sys.argv[1:] if a != "--selftest"])
        return

    # ---- 启动前的“平台初始化”：必须在创建 Tk() 之前完成 ----
    try:
        # Windows 下声明 DPI 感知，否则高分屏上窗口/字体被系统拉伸、发虚
        enable_dpi_awareness()
        # 设置 AppUserModelID：让 Windows 任务栏把进程归到“自己的应用”下，
        # 这样自定义图标、任务栏分组、跳转列表才按预期生效（不设容易被归到 python.exe）
        set_app_user_model_id(APP_USER_MODEL_ID)
    except Exception:  # noqa: BLE001
        pass          # 非 Windows / 老系统 / 调用失败都无所谓，静默降级

    # 延迟导入：放在初始化之后、创建窗口之前。
    # 好处是 --help、导入期报错、甚至上面的初始化失败时都不会牵连 tkinter 的加载；
    # 也避免了 gerber_viewer 在模块顶层就 import tkinter 造成的循环/重量级依赖
    from gerber_viewer import GerberViewer

    root = tk.Tk()                 # 创建 Tk 根窗口（所有 Toplevel 的父级）
    root.withdraw()                # 立刻隐藏根窗口：本程序不显示主窗体，
                                   # 只把 GerberViewer 自己的 Toplevel 作为可见界面
    root.title(win_title())        # 给 root 设标题：部分平台上 Toplevel 未设标题时
                                   # 会回退到父窗口标题，也算一层兜底
    try:
        apply_tk_scaling(root)     # 按系统 DPI 调整 Tk 的 scaling（字体/控件尺寸），
                                   # 让界面在高分屏上比例正常
    except Exception:  # noqa: BLE001
        pass                       # 缩放失败不影响功能，只是观感差一点
    apply_icon(root)               # 设置标题栏/任务栏图标（内部已静默容错）

    viewer = GerberViewer(root)    # 构建查看器：内部会基于 root 创建自己的 Toplevel（win）

    # 命令行 / 右键“打开方式” / 拖到 exe 上：把传入的文件直接加进来
    _files = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if _files:
        def _open_argv():
            for _i, _p in enumerate(_files):
                try:
                    viewer.add_layer(_p, fit_now=(_i == len(_files) - 1))
                except Exception:      # noqa: BLE001
                    pass
        viewer.win.after(60, _open_argv)

    def _close():
        """关闭查看器窗口时同时退出程序"""
        try:
            viewer._on_close()     # 先走查看器自己的清理（保存状态、停定时器、释放资源等）
        finally:
            try:
                root.destroy()     # 无论 _on_close 是否抛异常，都销毁根窗口 → 结束 mainloop
            except Exception:  # noqa: BLE001
                pass

    # 关键：用户点查看器窗口右上角 × 时，走 _close 而不是只关掉那个 Toplevel。
    # 因为 root 是隐藏的、mainloop 还挂在 root 上，只关 Toplevel 程序不会退出
    viewer.win.protocol("WM_DELETE_WINDOW", _close)

    root.mainloop()                # 进入事件循环，直到 root.destroy() 才返回


if __name__ == "__main__":
    # 仅当作为脚本直接运行时执行 main()；
    # 被 import（如测试、打包入口调用）时不自动启动 GUI
    main()