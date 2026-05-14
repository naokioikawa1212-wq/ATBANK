import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import AsyncGenerator

import aiofiles
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from video_processor import process_video

app = FastAPI(title="Video Auto-Cut API")

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

# In-memory job store: job_id -> {"status", "progress", "result", "error"}
jobs: dict[str, dict] = {}

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}


class ProcessRequest(BaseModel):
    upload_id: str
    mode: str = "both"           # silence | scene | both
    noise_db: float = -35.0
    min_silence_duration: float = 0.5
    scene_threshold: float = 0.4
    silence_padding: float = 0.1


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    upload_id = str(uuid.uuid4())
    dest = UPLOAD_DIR / f"{upload_id}{ext}"

    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            await f.write(chunk)

    return {"upload_id": upload_id, "filename": file.filename, "size": dest.stat().st_size}


def _find_upload(upload_id: str) -> Path:
    for ext in ALLOWED_EXTENSIONS:
        p = UPLOAD_DIR / f"{upload_id}{ext}"
        if p.exists():
            return p
    raise HTTPException(404, "Upload not found")


async def _run_processing(job_id: str, req: ProcessRequest):
    jobs[job_id]["status"] = "processing"
    jobs[job_id]["progress"] = 5

    try:
        input_path = str(_find_upload(req.upload_id))
        output_dir = str(JOBS_DIR / job_id)

        jobs[job_id]["progress"] = 15
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: process_video(
                input_path=input_path,
                output_dir=output_dir,
                mode=req.mode,
                noise_db=req.noise_db,
                min_silence_duration=req.min_silence_duration,
                scene_threshold=req.scene_threshold,
                silence_padding=req.silence_padding,
            ),
        )
        jobs[job_id]["progress"] = 100
        jobs[job_id]["status"] = "done"
        jobs[job_id]["result"] = result
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)


@app.post("/api/process")
async def start_processing(req: ProcessRequest):
    _find_upload(req.upload_id)  # validate early

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "queued", "progress": 0, "result": None, "error": None}

    asyncio.create_task(_run_processing(job_id, req))
    return {"job_id": job_id}


async def _progress_stream(job_id: str) -> AsyncGenerator[str, None]:
    if job_id not in jobs:
        yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
        return

    while True:
        job = jobs[job_id]
        payload = {
            "status": job["status"],
            "progress": job["progress"],
        }
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


@app.get("/api/result/{job_id}")
async def get_result(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    job = jobs[job_id]
    if job["status"] != "done":
        raise HTTPException(400, f"Job status: {job['status']}")
    return job["result"]


@app.get("/api/download/{job_id}/{filename}")
async def download_file(job_id: str, filename: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    file_path = JOBS_DIR / job_id / filename
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(
        str(file_path),
        media_type="video/mp4",
        filename=filename,
    )


# Serve React frontend build
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
