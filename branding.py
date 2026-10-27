"""
Evan_Gerber_View: 品牌 / 命名 / 资源路径 (单一来源)
===================================================
所有用户可见的程序名 / 窗口标题 / 图标均取自此模块。
"""
import os
import sys

APP_NAME = "Evan_Gerber_View"
APP_TITLE = "Gerber Viewer"
APP_USER_MODEL_ID = "Evan.GerberView.App"

ICON_ICO = "Evan_Gerber_View.ico"
ICON_PNG = "Evan_Gerber_View.png"


def win_title(sub: str = "") -> str:
    """窗口标题: 有子标题 -> '<APP> - <sub>'; 否则主标题"""
    return f"{APP_NAME} - {sub}" if sub else f"{APP_NAME}: {APP_TITLE}"


def resource_path(name: str) -> str:
    """兼容 PyInstaller 打包 (sys._MEIPASS) 与源码运行"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def icon_ico() -> str:
    return resource_path(ICON_ICO)


def icon_png() -> str:
    return resource_path(ICON_PNG)