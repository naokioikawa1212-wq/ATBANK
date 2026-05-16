import json
import os
import re
import subprocess
from pathlib import Path


def run_ffmpeg(args: list[str], timeout: int = 600) -> tuple[str, str]:
    result = subprocess.run(
        ["ffmpeg", "-y"] + args,
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout, result.stderr


def get_video_info(input_path: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", input_path],
        capture_output=True, text=True,
    )
    info = json.loads(result.stdout)
    duration = float(info["format"]["duration"])
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    width = int(video["width"]) if video else 1920
    height = int(video["height"]) if video else 1080
    fps_str = video.get("r_frame_rate", "30/1") if video else "30/1"
    num, den = fps_str.split("/")
    fps = float(num) / float(den)
    return {"duration": duration, "width": width, "height": height, "fps": fps}


# ──────────────────────────────────────────
# Scene change detection
# ──────────────────────────────────────────

def detect_scene_changes(
    input_path: str,
    threshold: float = 8.0,
) -> list[tuple[float, float]]:
    """
    Return list of (timestamp_sec, score) for each detected scene change.
    Higher score = more dramatic visual change (before→after reveals etc.)
    threshold: 1-100, lower = more sensitive
    """
    _, stderr = run_ffmpeg([
        "-i", input_path,
        "-vf", f"scdet=threshold={threshold}",
        "-f", "null", "-",
    ])

    changes: list[tuple[float, float]] = []
    for line in stderr.splitlines():
        m_t = re.search(r"pts_time:([\d.]+)", line)
        m_s = re.search(r"lavfi\.scene_score=([\d.]+)", line)
        if m_t and m_s:
            changes.append((float(m_t.group(1)), float(m_s.group(1))))
    return changes


def find_highlights(
    changes: list[tuple[float, float]],
    total_duration: float,
    clip_duration: int = 60,
    num_clips: int = 3,
    min_gap: float = 20.0,
) -> list[float]:
    """
    Select N non-overlapping clip start times that contain the most/highest
    scene changes. For restoration videos, these are the dramatic before→after moments.
    """
    half = clip_duration / 4  # start slightly before the change point

    if not changes:
        step = total_duration / (num_clips + 1)
        return [max(0.0, step * (i + 1) - clip_duration / 2) for i in range(num_clips)]

    # Score each change as potential clip center
    candidates: list[tuple[float, float]] = []
    for ts, score in changes:
        start = max(0.0, ts - half)
        if start + clip_duration > total_duration:
            start = max(0.0, total_duration - clip_duration)
        # Total visual energy within the window
        window_score = sum(s for t, s in changes if start <= t <= start + clip_duration)
        candidates.append((start, window_score))

    candidates.sort(key=lambda x: -x[1])

    selected: list[float] = []
    for start, _ in candidates:
        if all(abs(start - s) >= min_gap for s in selected):
            selected.append(start)
        if len(selected) >= num_clips:
            break

    # Fill gaps with evenly spaced clips if not enough change-based highlights
    if len(selected) < num_clips:
        step = total_duration / (num_clips + 1)
        for i in range(num_clips):
            c = max(0.0, step * (i + 1) - clip_duration / 2)
            if all(abs(c - s) >= min_gap for s in selected):
                selected.append(c)
            if len(selected) >= num_clips:
                break

    return sorted(selected[:num_clips])


# ──────────────────────────────────────────
# Clip extraction & processing
# ──────────────────────────────────────────

def extract_clip(
    input_path: str,
    output_path: str,
    start: float,
    duration: int,
) -> None:
    run_ffmpeg([
        "-ss", str(start),
        "-i", input_path,
        "-t", str(duration),
        "-c", "copy",
        output_path,
    ])


def crop_to_vertical(input_path: str, output_path: str) -> None:
    """Convert any aspect ratio to 9:16 (1080×1920) with center crop."""
    info = get_video_info(input_path)
    w, h = info["width"], info["height"]
    target = 9 / 16

    if w / h > target:
        # Wider → crop left/right
        crop_w = int(h * target)
        crop_h = h
        x = (w - crop_w) // 2
        y = 0
    else:
        # Taller → crop top/bottom
        crop_w = w
        crop_h = int(w / target)
        x = 0
        y = (h - crop_h) // 2

    run_ffmpeg([
        "-i", input_path,
        "-vf", f"crop={crop_w}:{crop_h}:{x}:{y},scale=1080:1920:flags=lanczos",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ])


def mix_bgm(
    video_path: str,
    bgm_path: str,
    output_path: str,
    volume: float = 0.25,
) -> None:
    """Loop BGM and mix into video at specified volume."""
    run_ffmpeg([
        "-i", video_path,
        "-stream_loop", "-1", "-i", bgm_path,
        "-filter_complex",
        f"[1:a]volume={volume}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=3[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-shortest",
        output_path,
    ])


# ──────────────────────────────────────────
# Main pipeline
# ──────────────────────────────────────────

def create_shorts(
    input_path: str,
    output_dir: str,
    num_clips: int = 3,
    clip_duration: int = 60,
    scene_threshold: float = 8.0,
    bgm_path: str | None = None,
    bgm_volume: float = 0.25,
    progress_callback=None,
) -> dict:

    os.makedirs(output_dir, exist_ok=True)

    def progress(pct: int, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    progress(5, "動画情報を取得中...")
    info = get_video_info(input_path)
    duration = info["duration"]

    progress(12, "映像の変化を解析中（シーン検出）...")
    changes = detect_scene_changes(input_path, scene_threshold)

    progress(30, f"{len(changes)}箇所のシーン変化を検出。ハイライトを選定中...")
    starts = find_highlights(changes, duration, clip_duration, num_clips)

    clips = []
    n = len(starts)

    for idx, start in enumerate(starts):
        base = 30 + int((idx / n) * 65)
        label = f"クリップ {idx + 1}/{n}"

        progress(base + 2, f"{label}: 切り出し中...")
        raw = os.path.join(output_dir, f"_raw_{idx:02d}.mp4")
        actual_dur = int(min(clip_duration, duration - start))
        extract_clip(input_path, raw, start, actual_dur)

        progress(base + 8, f"{label}: 縦型（9:16）に変換中...")
        vertical = os.path.join(output_dir, f"_v_{idx:02d}.mp4")
        crop_to_vertical(raw, vertical)
        current = vertical

        if bgm_path and os.path.exists(bgm_path):
            progress(base + 16, f"{label}: BGMをミックス中...")
            bgm_out = os.path.join(output_dir, f"_bgm_{idx:02d}.mp4")
            mix_bgm(current, bgm_path, bgm_out, bgm_volume)
            current = bgm_out

        final_name = f"short_{idx + 1:02d}.mp4"
        final_path = os.path.join(output_dir, final_name)
        os.rename(current, final_path)

        # Collect scene changes that fall within this clip
        clip_changes = [
            {"time": round(t - start, 2), "score": round(s, 3)}
            for t, s in changes
            if start <= t <= start + actual_dur
        ]

        clips.append({
            "index": idx,
            "filename": final_name,
            "start": round(start, 1),
            "duration": actual_dur,
            "scene_changes": clip_changes,
        })

        for tmp in [raw, vertical]:
            if os.path.exists(tmp):
                os.remove(tmp)

    progress(100, f"完了！{len(clips)}本のショート動画を生成しました")

    # Downsample scene changes for waveform display
    sampled = [
        {"time": round(t, 1), "score": round(s, 3)}
        for t, s in changes[::max(1, len(changes) // 200)]
    ]

    return {
        "duration": duration,
        "total_scene_changes": len(changes),
        "clips": clips,
        "scene_data": sampled,
    }
