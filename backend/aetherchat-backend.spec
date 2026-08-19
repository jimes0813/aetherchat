# -*- mode: python ; coding: utf-8 -*-
import sys

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = [
    'uvicorn.loops.auto',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan.on',
    'app.main',
    'app.routers.voice',
    'app.services.voice_runtime',
    'app.services.rate_limit',
    'app.services.chat_references',
    'app.services.x11_desktop',
    'app.services.chat_database',
    'app.services.db',
    'app.services.db._shared',
    'app.services.db.connection',
    'app.services.db.schema',
    'app.services.db.sessions',
    'app.services.db.messages',
    'app.services.db.tasks',
    'app.services.db.agent_runs',
    'app.services.db.event_journal',
    'app.services.db.workspaces',
    'app.services.db.automation',
    'app.services.orchestrator',
    'app.services.orchestrator._constants',
    'app.services.orchestrator.context',
    'app.services.orchestrator.plan',
    'app.services.orchestrator.receipts',
    'app.services.orchestrator.run',
    'app.services.orchestrator.tools',
    'mcp_servers.aetherchat_local_audit',
]
if sys.platform == 'win32':
    hiddenimports += [
        'win32api', 'win32con', 'win32crypt', 'win32gui',
        'win32process', 'win32ui', 'pythoncom', 'pywintypes',
    ]
tmp_ret = collect_all('fastapi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('starlette')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('uvicorn')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pydantic')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
packages = ['docx', 'openpyxl', 'pptx', 'pypdf', 'reportlab', 'PIL', 'psutil', 'mcp']
if sys.platform == 'win32':
    packages += ['pywinauto', 'comtypes']
else:
    packages += ['keyring', 'secretstorage']
for package in packages:
    tmp_ret = collect_all(package)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['aetherchat_backend.py'],
    pathex=['..'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='aetherchat-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # PyWin32 and document libraries ship native extensions. Keeping UPX off
    # avoids loader/antivirus issues caused by compressing those binaries.
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='aetherchat-backend',
)
