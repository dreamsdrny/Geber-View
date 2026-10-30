# -*- mode: python ; coding: utf-8 -*-
# Evan_Gerber_View: 独立 Gerber / NC Drill 查看器 打包配置

a = Analysis(
    ['Evan_Gerber_View.py'],
    pathex=[],
    binaries=[
        # Rust 原生解析内核 (可选, 缺失时自动回退纯 Python)
        # 必须是 .pyd 后缀, 否则 importlib 不会把它当扩展模块
        ('rust/gerber_core/gerber_core.pyd', '.'),
    ],
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
        'gerber_native',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 这些都没被程序用到, 但会被自动收集进 _internal (尤其 numpy),
        # 白白拖慢启动 (杀软扫描 + 模块索引) 并让体积膨胀几十 MB
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
    [],
    exclude_binaries=True,
    name='Evan_Gerber_View',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['Evan_Gerber_View.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Evan_Gerber_View',
)