# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['d:\\项目类\\CLI_lite应用\\青稞lite\\main.py'],
    pathex=[],
    binaries=[],
    datas=[('config', 'config'), ('web', 'web'), ('core', 'core'), ('util', 'util'), ('skill', 'skill')],
    hiddenimports=['engine', 'skill_manager', 'tools', 'context_manager', 'llm_gateway', 'agentic_loop', 'web.routes.api', 'web.routes.main', 'flask', 'flask_cors', 'openai', 'requests', 'httpx'],
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
    name='QingkeLite',
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
