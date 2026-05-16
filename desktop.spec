# PyInstaller spec — run: pyinstaller desktop.spec
from pathlib import Path
import sys

ROOT = Path(SPECPATH)
BACKEND = ROOT / "backend"
FRONTEND_DIST = ROOT / "frontend" / "dist"

a = Analysis(
    [str(ROOT / "desktop.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=[
        # Bundle the built React app
        (str(FRONTEND_DIST), "frontend/dist"),
    ],
    hiddenimports=[
        # FastAPI / uvicorn internals that PyInstaller misses
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "fastapi",
        "multipart",
        "aiofiles",
        # faster-whisper / ctranslate2
        "faster_whisper",
        "ctranslate2",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "wx"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BikeRestoreShorts",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,   # no terminal window on Windows/Mac
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BikeRestoreShorts",
)
