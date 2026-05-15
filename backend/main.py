import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import AsyncGenerator

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from video_processor import process_video

app = FastAPI(title="Video Auto-Edit API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("uploads")
JOBS_DIR = Path("jobs")
UPLOAD_DIR.mkdir(exist_ok=True)
JOBS_DIR.mkdir(exist_ok=True)

jobs: dict[str, dict] = {}

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}


def _save_upload_path(upload_id: str, dir: Path, allowed_exts: set) -> Path:
    for ext in allowed_exts:
        p = dir / f"{upload_id}{ext}"
        if p.exists():
            return p
    raise HTTPException(404, "Upload not found")


@app.post("/api/upload/video")
async def upload_video(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in VIDEO_EXTS:
        raise HTTPException(400, f"Unsupported video format: {ext}")
    upload_id = str(uuid.uuid4())
    dest = UPLOAD_DIR / f"{upload_id}{ext}"
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            await f.write(chunk)
    return {"upload_id": upload_id, "filename": file.filename, "size": dest.stat().st_size}


@app.post("/api/upload/bgm")
async def upload_bgm(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in AUDIO_EXTS:
        raise HTTPException(400, f"Unsupported audio format: {ext}")
    upload_id = str(uuid.uuid4())
    dest = UPLOAD_DIR / f"{upload_id}{ext}"
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            await f.write(chunk)
    return {"upload_id": upload_id, "filename": file.filename}


class ProcessRequest(BaseModel):
    upload_id: str
    # Silence
    do_silence_cut: bool = True
    noise_db: float = -35.0
    min_silence_duration: float = 0.5
    silence_padding: float = 0.1
    # BGM
    bgm_upload_id: str | None = None
    bgm_volume: float = 0.15
    # Subtitles
    do_subtitles: bool = False
    subtitle_language: str = "ja"
    burn_subs: bool = False
    # Thumbnail
    do_thumbnail: bool = False
    thumbnail_title: str = ""
    thumbnail_timestamp: float = 5.0


async def _run_processing(job_id: str, req: ProcessRequest):
    jobs[job_id]["status"] = "processing"

    def progress(pct: int, msg: str):
        jobs[job_id]["progress"] = pct
        jobs[job_id]["message"] = msg

    try:
        input_path = str(_save_upload_path(req.upload_id, UPLOAD_DIR, VIDEO_EXTS))
        bgm_path = None
        if req.bgm_upload_id:
            try:
                bgm_path = str(_save_upload_path(req.bgm_upload_id, UPLOAD_DIR, AUDIO_EXTS))
            except HTTPException:
                pass

        output_dir = str(JOBS_DIR / job_id)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: process_video(
                input_path=input_path,
                output_dir=output_dir,
                do_silence_cut=req.do_silence_cut,
                noise_db=req.noise_db,
                min_silence_duration=req.min_silence_duration,
                silence_padding=req.silence_padding,
                bgm_path=bgm_path,
                bgm_volume=req.bgm_volume,
                do_subtitles=req.do_subtitles,
                subtitle_language=req.subtitle_language,
                burn_subs=req.burn_subs,
                do_thumbnail=req.do_thumbnail,
                thumbnail_title=req.thumbnail_title,
                thumbnail_timestamp=req.thumbnail_timestamp,
                progress_callback=progress,
            ),
        )
        jobs[job_id]["status"] = "done"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["result"] = result
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)


@app.post("/api/process")
async def start_processing(req: ProcessRequest):
    _save_upload_path(req.upload_id, UPLOAD_DIR, VIDEO_EXTS)
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "queued", "progress": 0, "message": "待機中", "result": None, "error": None}
    asyncio.create_task(_run_processing(job_id, req))
    return {"job_id": job_id}


async def _progress_stream(job_id: str) -> AsyncGenerator[str, None]:
    if job_id not in jobs:
        yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
        return
    while True:
        job = jobs[job_id]
        payload = {"status": job["status"], "progress": job["progress"], "message": job.get("message", "")}
        if job["status"] == "done":
            payload["result"] = job["result"]
        elif job["status"] == "error":
            payload["error"] = job["error"]
        yield f"data: {json.dumps(payload)}\n\n"
        if job["status"] in ("done", "error"):
            break
        await asyncio.sleep(0.5)


@app.get("/api/progress/{job_id}")
async def progress(job_id: str):
    return StreamingResponse(
        _progress_stream(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/download/{job_id}/{filename}")
async def download_file(job_id: str, filename: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    file_path = JOBS_DIR / job_id / filename
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    media = "image/jpeg" if filename.endswith(".jpg") else "text/plain" if filename.endswith(".srt") else "video/mp4"
    return FileResponse(str(file_path), media_type=media, filename=filename)


# Serve React frontend
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
