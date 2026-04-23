# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller --onedir spec for the multi-file binary example.

Produces ``dist/example-multifile-tool/`` which contains the launcher
binary, all bundled .so/.dylib/.pyd files, and the bundled ``data/``
directory. ``build.sh`` then arranges these into the standard
Anna multi-file binary layout::

    bin/example-multifile-tool       (the PyInstaller launcher)
    lib/                              (Python runtime + extension modules)
    data/greeting.txt                 (bundled data)
    manifest.json                     (declares entrypoint)

and packs the whole tree into a platform-keyed ``tar.gz``.
"""

block_cipher = None

a = Analysis(
    ['plugin.py'],
    pathex=[],
    binaries=[],
    datas=[('data/greeting.txt', 'data')],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='example-multifile-tool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='example-multifile-tool',
)
