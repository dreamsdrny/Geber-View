"""
Evan_DFM: Windows 高 DPI 支持
=============================================
解决程序在高 DPI 显示器上「模糊」的问题:
  - 开启 Per-Monitor V2 DPI 感知 (在创建窗口前调用), 使 Windows 不再位图拉伸
  - 设置 AppUserModelID, 任务栏正确显示程序图标并分组
  - 按当前 DPI 设置 Tk 缩放, 并按比例放大窗口尺寸, 保证文字清晰且大小合适
"""
import ctypes
import sys

APP_USER_MODEL_ID = "Evan.DFM.App"


def enable_dpi_awareness() -> None:
    """必须在创建任何窗口之前调用 (建议程序入口第一行)"""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        try:
            # Windows 10 1703+ : Per-Monitor Aware V2
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return
        except Exception:
            pass
        try:
            # Windows 8.1+ : Per-Monitor Aware
            if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
                return
        except Exception:
            pass
        try:
            user32.SetProcessDPIAware()   # Vista+ : System Aware
        except Exception:
            pass
    except Exception:
        pass


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> None:
    """任务栏图标分组/显示, 避免显示为通用 Python 图标"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def get_dpi(root=None) -> int:
    if sys.platform != "win32":
        return 96
    try:
        if root is not None:
            try:
                hwnd = root.winfo_id()
                dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                if dpi:
                    return int(dpi)
            except Exception:
                pass
        dc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, dc)
        return int(dpi or 96)
    except Exception:
        return 96


_SCALING_DONE = set()


def apply_tk_scaling(root) -> float:
    """按 DPI 设置 Tk 缩放, 返回相对于 96 DPI 的比例系数"""
    try:
        dpi = get_dpi(root)
        factor = dpi / 72.0
        try:
            root.tk.call("tk", "scaling", factor)
        except Exception:
            pass
        return dpi / 96.0
    except Exception:
        return 1.0


def apply_window(root, width: int, height: int, min_ratio: float = 1.0) -> float:
    """设置 Tk 缩放并按 DPI 比例放大窗口几何尺寸"""
    s = apply_tk_scaling(root)
    s = max(s, min_ratio)
    try:
        root.geometry(f"{int(width * s)}x{int(height * s)}")
    except Exception:
        pass
    return s