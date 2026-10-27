# -*- mode: python ; coding: utf-8 -*-
# Evan_Gerber_View: 独立 Gerber / NC Drill 查看器 打包配置

a = Analysis(
    ['Evan_Gerber_View.py'],
    pathex=[],
    binaries=[],
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
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
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
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name='Evan_Gerber_View',
)