import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import AsyncGenerator

import aiofiles
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from video_processor import create_shorts

app = FastAPI(title="Bike Restore Shorts Generator")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# When packaged with PyInstaller, store user data in home dir to stay writable
if getattr(sys, "frozen", False):
    _DATA_DIR = Path.home() / ".bike-shorts-gen"
else:
    _DATA_DIR = Path(__file__).parent

UPLOAD_DIR = _DATA_DIR / "uploads"
JOBS_DIR = _DATA_DIR / "jobs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

jobs: dict[str, dict] = {}

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}


def _find_upload(uid: str, exts: set, label: str = "File") -> Path:
    for ext in exts:
        p = UPLOAD_DIR / f"{uid}{ext}"
        if p.exists():
            return p
    raise HTTPException(404, f"{label} not found")


@app.post("/api/upload/video")
async def upload_video(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in VIDEO_EXTS:
        raise HTTPException(400, f"非対応フォーマット: {ext}")
    uid = str(uuid.uuid4())
    dest = UPLOAD_DIR / f"{uid}{ext}"
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(2 * 1024 * 1024):
            await f.write(chunk)
    return {"upload_id": uid, "filename": file.filename, "size": dest.stat().st_size}


@app.post("/api/upload/bgm")
async def upload_bgm(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in AUDIO_EXTS:
        raise HTTPException(400, f"非対応フォーマット: {ext}")
    uid = str(uuid.uuid4())
    dest = UPLOAD_DIR / f"{uid}{ext}"
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            await f.write(chunk)
    return {"upload_id": uid, "filename": file.filename}


class ProcessRequest(BaseModel):
    upload_id: str
    num_clips: int = 3
    clip_duration: int = 60
    scene_threshold: float = 8.0   # 1-30: lower = more sensitive
    bgm_upload_id: str | None = None
    bgm_volume: float = 0.25


async def _run(job_id: str, req: ProcessRequest):
    jobs[job_id]["status"] = "processing"

    def cb(pct: int, msg: str):
        jobs[job_id]["progress"] = pct
        jobs[job_id]["message"] = msg

    try:
        input_path = str(_find_upload(req.upload_id, VIDEO_EXTS, "Video"))
        bgm_path = None
        if req.bgm_upload_id:
            try:
                bgm_path = str(_find_upload(req.bgm_upload_id, AUDIO_EXTS, "BGM"))
            except HTTPException:
                pass

        output_dir = str(JOBS_DIR / job_id)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: create_shorts(
                input_path=input_path,
                output_dir=output_dir,
                num_clips=req.num_clips,
                clip_duration=min(req.clip_duration, 60),
                scene_threshold=req.scene_threshold,
                bgm_path=bgm_path,
                bgm_volume=req.bgm_volume,
                progress_callback=cb,
            ),
        )
        jobs[job_id].update({"status": "done", "progress": 100, "result": result})
    except Exception as e:
        jobs[job_id].update({"status": "error", "error": str(e)})


@app.post("/api/process")
async def start_processing(req: ProcessRequest):
    _find_upload(req.upload_id, VIDEO_EXTS, "Video")
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "queued", "progress": 0, "message": "待機中", "result": None, "error": None}
    asyncio.create_task(_run(job_id, req))
    return {"job_id": job_id}


async def _sse(job_id: str) -> AsyncGenerator[str, None]:
    if job_id not in jobs:
        yield f"data: {json.dumps({'error': 'not found'})}\n\n"
        return
    while True:
        j = jobs[job_id]
        data: dict = {"status": j["status"], "progress": j["progress"], "message": j.get("message", "")}
        if j["status"] == "done":
            data["result"] = j["result"]
        elif j["status"] == "error":
            data["error"] = j["error"]
        yield f"data: {json.dumps(data)}\n\n"
        if j["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.5)


@app.get("/api/progress/{job_id}")
async def progress(job_id: str):
    return StreamingResponse(
        _sse(job_id), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/download/{job_id}/{filename}")
async def download(job_id: str, filename: str):
    p = JOBS_DIR / job_id / filename
    if not p.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(str(p), media_type="video/mp4", filename=filename)


@app.post("/api/open-folder/{job_id}")
async def open_folder(job_id: str):
    """Open the output folder in the native file manager (desktop mode)."""
    import platform, subprocess
    folder = JOBS_DIR / job_id
    if not folder.exists():
        raise HTTPException(404, "Folder not found")
    system = platform.system()
    if system == "Windows":
        subprocess.Popen(["explorer", str(folder)])
    elif system == "Darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])
    return {"path": str(folder)}


# PyInstaller: bundled assets live in sys._MEIPASS
if getattr(sys, "frozen", False):
    _asset_base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
else:
    _asset_base = Path(__file__).parent.parent

frontend_dist = _asset_base / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
