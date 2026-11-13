# -*- mode: python ; coding: utf-8 -*-
# Evan_Gerber_View: 单文件打包配置 (所有依赖打进 exe, 不再需要 _internal)
#
# 构建:
#   python -m PyInstaller --clean --noconfirm Evan_Gerber_View_onefile.spec
# 产物:
#   dist\Evan_Gerber_View.exe
#
# 说明: 单文件运行时 PyInstaller 会先解压到 %TEMP%, 首次启动比目录版稍慢 (~1-2s)。
import os

# Rust 原生解析内核: 可选。编译产物 gerber_core.pyd 被 .gitignore 忽略,
# 干净检出(例如 CI)里没有 —— 此时不打包它, 程序自动回退纯 Python (见 gerber_native.py)。
_NATIVE_PYD = os.path.join('rust', 'gerber_core', 'gerber_core.pyd')
if os.path.isfile(_NATIVE_PYD):
    # 必须保持 .pyd 后缀, 否则 importlib 不会把它当扩展模块
    _BINARIES = [(_NATIVE_PYD, '.')]
else:
    _BINARIES = []
    print('WARNING: %s 不存在, 本次打包使用纯 Python 解析内核' % _NATIVE_PYD)

a = Analysis(
    ['Evan_Gerber_View.py'],
    pathex=[],
    binaries=_BINARIES,
    datas=[
        ('Evan_Gerber_View.ico', '.'),
        ('Evan_Gerber_View.png', '.'),
    ],
    hiddenimports=[
        'tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox',
        'tkinter.colorchooser', 'tkinter.simpledialog',
        'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageTk', 'PIL.ImageFont',
        'PIL.ImageFilter',
        'gerber_viewer', 'gerber_reader', 'gerber_layer', 'win_dpi', 'branding',
        'gerber_native', 'gerber_diff', 'gerber_project', 'gerber_report',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'numpy', 'scipy', 'pandas', 'matplotlib', 'IPython', 'pytest',
        'unittest', 'pydoc', 'doctest', 'asyncio', 'ssl', 'decimal', '_decimal',
        'bz2', '_bz2', 'lzma', '_lzma', 'multiprocessing', 'concurrent',
        'xml', 'email', 'http', 'html', 'wmi', 'win32com', 'pythoncom',
        'pywintypes', 'setuptools', 'pkg_resources',
        'PIL.ImageQt', 'PIL.ImageShow', 'PIL.ImageGrab', 'PIL.ImageCms',
    ],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Evan_Gerber_View',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['Evan_Gerber_View.ico'],
)
