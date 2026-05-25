# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['launch_client.py'],
    pathex=['.'],
    binaries=[],
    datas=[('config\\online.json', 'config')],
    hiddenimports=['src.client.settings', 'src.client.network', 'src.common.protocol'],
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
    a.binaries,
    a.datas,
    [],
    name='网络五子棋',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
