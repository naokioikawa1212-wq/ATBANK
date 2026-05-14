import subprocess
import json
import re
import os
import uuid
from pathlib import Path
from typing import Optional


def run_ffmpeg(args: list[str], timeout: int = 300) -> tuple[str, str]:
    result = subprocess.run(
        ["ffmpeg", "-y"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout, result.stderr


def get_video_duration(input_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", input_path],
        capture_output=True,
        text=True,
    )
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def detect_silence(
    input_path: str,
    noise_db: float = -35.0,
    min_duration: float = 0.5,
) -> list[dict]:
    """Return list of silence intervals: [{"start": float, "end": float}]"""
    _, stderr = run_ffmpeg([
        "-i", input_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-",
    ])

    silences = []
    start = None
    for line in stderr.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            start = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            silences.append({"start": start, "end": float(m.group(1))})
            start = None
    # handle silence that runs to end of file
    if start is not None:
        duration = get_video_duration(input_path)
        silences.append({"start": start, "end": duration})
    return silences


def detect_scene_changes(
    input_path: str,
    threshold: float = 0.4,
) -> list[float]:
    """Return list of timestamps (seconds) where scene changes occur."""
    _, stderr = run_ffmpeg([
        "-i", input_path,
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-vsync", "vfr",
        "-f", "null", "-",
    ])

    timestamps = [0.0]
    for line in stderr.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            if t > 0.01:
                timestamps.append(t)
    return sorted(set(timestamps))


def silence_to_keep_segments(
    silences: list[dict],
    duration: float,
    padding: float = 0.1,
) -> list[dict]:
    """Convert silence intervals to keep (non-silent) segments."""
    keep = []
    cursor = 0.0
    for s in silences:
        seg_end = max(0.0, s["start"] - padding)
        if seg_end - cursor > 0.05:
            keep.append({"start": round(cursor, 3), "end": round(seg_end, 3)})
        cursor = s["end"] + padding
    if duration - cursor > 0.05:
        keep.append({"start": round(cursor, 3), "end": round(duration, 3)})
    return keep


def scene_changes_to_segments(
    timestamps: list[float],
    duration: float,
) -> list[dict]:
    """Convert scene change timestamps to segments."""
    segments = []
    times = sorted(set(timestamps + [duration]))
    for i in range(len(times) - 1):
        segments.append({"start": round(times[i], 3), "end": round(times[i + 1], 3)})
    return segments


def extract_segment(
    input_path: str,
    output_path: str,
    start: float,
    end: float,
) -> None:
    duration = end - start
    run_ffmpeg([
        "-ss", str(start),
        "-i", input_path,
        "-t", str(duration),
        "-c", "copy",
        output_path,
    ])


def merge_segments(
    input_path: str,
    segments: list[dict],
    output_path: str,
    tmp_dir: str,
) -> None:
    """Merge multiple segments into a single file using concat filter."""
    if not segments:
        return

    # Build complex filter for seamless concat
    filter_parts = []
    inputs = []
    for i, seg in enumerate(segments):
        inputs += ["-ss", str(seg["start"]), "-t", str(seg["end"] - seg["start"]), "-i", input_path]
        filter_parts.append(f"[{i}:v][{i}:a]")

    concat_filter = "".join(filter_parts) + f"concat=n={len(segments)}:v=1:a=1[v][a]"

    run_ffmpeg(
        inputs + [
            "-filter_complex", concat_filter,
            "-map", "[v]",
            "-map", "[a]",
            output_path,
        ],
        timeout=600,
    )


def process_video(
    input_path: str,
    output_dir: str,
    mode: str = "both",          # "silence" | "scene" | "both"
    noise_db: float = -35.0,
    min_silence_duration: float = 0.5,
    scene_threshold: float = 0.4,
    silence_padding: float = 0.1,
) -> dict:
    """
    Process video and return result metadata.
    mode:
      - "silence": remove silent parts, output merged video + segments
      - "scene"  : split by scene changes, output segments
      - "both"   : remove silence then split by scene changes
    """
    os.makedirs(output_dir, exist_ok=True)
    duration = get_video_duration(input_path)
    ext = Path(input_path).suffix

    result = {
        "duration": duration,
        "mode": mode,
        "segments": [],
        "merged_path": None,
    }

    if mode in ("silence", "both"):
        silences = detect_silence(input_path, noise_db, min_silence_duration)
        keep_segments = silence_to_keep_segments(silences, duration, silence_padding)
        result["silences"] = silences
        result["keep_segments"] = keep_segments
    else:
        keep_segments = [{"start": 0.0, "end": duration}]
        result["silences"] = []
        result["keep_segments"] = keep_segments

    if mode in ("scene", "both"):
        scene_times = detect_scene_changes(input_path, scene_threshold)
        result["scene_times"] = scene_times

        # Find scene changes within keep_segments
        all_segments = []
        for ks in keep_segments:
            # scene times that fall within this keep segment
            cuts = [t for t in scene_times if ks["start"] < t < ks["end"]]
            boundaries = [ks["start"]] + cuts + [ks["end"]]
            for i in range(len(boundaries) - 1):
                if boundaries[i + 1] - boundaries[i] > 0.1:
                    all_segments.append({
                        "start": round(boundaries[i], 3),
                        "end": round(boundaries[i + 1], 3),
                    })
        segments = all_segments
    else:
        segments = keep_segments
        result["scene_times"] = []

    # Extract individual segment files
    seg_meta = []
    for i, seg in enumerate(segments):
        seg_duration = seg["end"] - seg["start"]
        filename = f"segment_{i:03d}{ext}"
        out_path = os.path.join(output_dir, filename)
        extract_segment(input_path, out_path, seg["start"], seg["end"])
        seg_meta.append({
            "index": i,
            "start": seg["start"],
            "end": seg["end"],
            "duration": round(seg_duration, 3),
            "filename": filename,
        })

    result["segments"] = seg_meta

    # Create merged output (only for silence mode / both)
    if mode in ("silence", "both") and len(segments) > 1:
        merged_filename = f"merged{ext}"
        merged_path = os.path.join(output_dir, merged_filename)
        merge_segments(input_path, segments, merged_path, output_dir)
        result["merged_path"] = merged_filename

    return result
