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
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    width = int(video["width"]) if video else 1920
    height = int(video["height"]) if video else 1080
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "has_audio": audio is not None,
    }


# ──────────────────────────────────────────
# Highlight detection via audio energy
# ──────────────────────────────────────────

def analyze_audio_energy(input_path: str, window: float = 1.0) -> list[tuple[float, float]]:
    """
    Return list of (timestamp_sec, rms_db) for each window.
    Uses ffmpeg astats to measure per-second loudness.
    """
    _, stderr = run_ffmpeg([
        "-i", input_path,
        "-af", f"astats=length={window}:metadata=1,"
               "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
        "-f", "null", "-",
    ])

    points: list[tuple[float, float]] = []
    ts = None
    for line in stderr.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            ts = float(m.group(1))
        m = re.search(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+)", line)
        if m and ts is not None:
            rms = float(m.group(1))
            if rms > -100:  # ignore near-silence / -inf
                points.append((ts, rms))
            ts = None
    return points


def _smooth(values: list[float], k: int = 3) -> list[float]:
    """Simple moving average smoothing."""
    out = []
    for i in range(len(values)):
        window = values[max(0, i - k): i + k + 1]
        out.append(sum(window) / len(window))
    return out


def find_highlights(
    energy: list[tuple[float, float]],
    total_duration: float,
    clip_duration: int = 60,
    num_clips: int = 3,
    min_gap: float = 30.0,
) -> list[float]:
    """
    Return up to num_clips start timestamps for highlight clips.
    Prefers segments with highest average energy.
    """
    if not energy:
        # fallback: evenly spaced
        step = total_duration / (num_clips + 1)
        return [max(0, step * (i + 1) - clip_duration / 2) for i in range(num_clips)]

    times = [t for t, _ in energy]
    rms = _smooth([r for _, r in energy], k=5)

    # Sliding window: score = mean rms over clip_duration window
    half = clip_duration / 2
    scores: list[tuple[float, float]] = []  # (start, score)
    for i, (t, r) in enumerate(zip(times, rms)):
        start = t - half
        end = t + half
        if start < 0 or end > total_duration:
            continue
        window_rms = [
            rv for tv, rv in zip(times, rms)
            if start <= tv <= end
        ]
        if window_rms:
            scores.append((max(0.0, start), sum(window_rms) / len(window_rms)))

    if not scores:
        step = total_duration / (num_clips + 1)
        return [max(0, step * (i + 1) - clip_duration / 2) for i in range(num_clips)]

    scores.sort(key=lambda x: -x[1])  # highest energy first

    selected: list[float] = []
    for start, _ in scores:
        # enforce min_gap between clips
        if all(abs(start - s) >= min_gap for s in selected):
            selected.append(start)
        if len(selected) >= num_clips:
            break

    return sorted(selected)


# ──────────────────────────────────────────
# Video processing steps
# ──────────────────────────────────────────

def extract_clip(input_path: str, output_path: str, start: float, duration: int) -> None:
    run_ffmpeg([
        "-ss", str(start),
        "-i", input_path,
        "-t", str(duration),
        "-c", "copy",
        output_path,
    ])


def crop_to_vertical(input_path: str, output_path: str) -> None:
    """
    Convert any aspect ratio to 9:16 (1080x1920).
    Strategy: crop center column, then scale up.
    """
    info = get_video_info(input_path)
    w, h = info["width"], info["height"]
    src_ratio = w / h
    target_ratio = 9 / 16

    if src_ratio > target_ratio:
        # Wider than 9:16 → crop sides
        crop_w = int(h * target_ratio)
        crop_h = h
        x = (w - crop_w) // 2
        y = 0
    else:
        # Taller than 9:16 → crop top/bottom
        crop_w = w
        crop_h = int(w / target_ratio)
        x = 0
        y = (h - crop_h) // 2

    vf = f"crop={crop_w}:{crop_h}:{x}:{y},scale=1080:1920:flags=lanczos"
    run_ffmpeg([
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ])


def transcribe_and_make_srt(input_path: str, srt_path: str, language: str = "ja") -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(input_path, language=language, vad_filter=True,
                                   word_timestamps=False)

    lines = []
    for i, seg in enumerate(segments, 1):
        lines += [
            str(i),
            f"{_srt_time(seg.start)} --> {_srt_time(seg.end)}",
            seg.text.strip(),
            "",
        ]
    srt = "\n".join(lines)
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt)
    return srt


def _srt_time(s: float) -> str:
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    ms = int((sec % 1) * 1000)
    return f"{int(h):02d}:{int(m):02d}:{int(sec):02d},{ms:03d}"


def burn_telop(input_path: str, srt_path: str, output_path: str) -> None:
    """Burn YouTube-Shorts-style captions (large, bold, centered, bottom)."""
    escaped = srt_path.replace("\\", "/").replace(":", "\\:")

    # Try Japanese font first
    font = "Noto Sans CJK JP Bold"
    for candidate in [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ]:
        if os.path.exists(candidate):
            break
    else:
        font = "Arial"

    style = (
        f"FontName={font},"
        "FontSize=22,"
        "Bold=1,"
        "PrimaryColour=&H00FFFFFF,"   # white
        "OutlineColour=&H00000000,"   # black
        "BackColour=&H80000000,"      # semi-transparent bg
        "BorderStyle=3,"
        "Outline=3,"
        "Shadow=0,"
        "Alignment=2,"                # bottom center
        "MarginV=80"
    )
    run_ffmpeg([
        "-i", input_path,
        "-vf", f"subtitles='{escaped}':force_style='{style}'",
        "-c:a", "copy",
        output_path,
    ], timeout=300)


def mix_bgm(video_path: str, bgm_path: str, output_path: str, volume: float = 0.2) -> None:
    run_ffmpeg([
        "-i", video_path,
        "-stream_loop", "-1", "-i", bgm_path,
        "-filter_complex",
        f"[1:a]volume={volume}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
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
    do_telop: bool = True,
    telop_language: str = "ja",
    bgm_path: str | None = None,
    bgm_volume: float = 0.2,
    progress_callback=None,
) -> dict:
    os.makedirs(output_dir, exist_ok=True)

    def progress(pct: int, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    progress(5, "動画情報を解析中...")
    info = get_video_info(input_path)
    duration = info["duration"]

    progress(10, "音声エネルギーを解析中（ハイライト検出）...")
    energy = analyze_audio_energy(input_path, window=1.0)

    progress(20, f"ハイライト区間を選定中...")
    starts = find_highlights(
        energy, duration,
        clip_duration=clip_duration,
        num_clips=num_clips,
        min_gap=max(clip_duration * 0.8, 10),
    )

    clips = []
    total_steps = len(starts)
    for idx, start in enumerate(starts):
        base_pct = 20 + int((idx / total_steps) * 70)
        clip_label = f"クリップ {idx + 1}/{total_steps}"

        progress(base_pct + 2, f"{clip_label}: 切り出し中...")
        raw = os.path.join(output_dir, f"raw_{idx:02d}.mp4")
        actual_dur = min(clip_duration, duration - start)
        extract_clip(input_path, raw, start, int(actual_dur))

        progress(base_pct + 5, f"{clip_label}: 縦型(9:16)に変換中...")
        vertical = os.path.join(output_dir, f"vertical_{idx:02d}.mp4")
        crop_to_vertical(raw, vertical)
        current = vertical

        if do_telop:
            progress(base_pct + 10, f"{clip_label}: テロップ生成中（Whisper）...")
            srt_path = os.path.join(output_dir, f"sub_{idx:02d}.srt")
            transcribe_and_make_srt(current, srt_path, telop_language)

            progress(base_pct + 15, f"{clip_label}: テロップ焼き込み中...")
            teloped = os.path.join(output_dir, f"telop_{idx:02d}.mp4")
            burn_telop(current, srt_path, teloped)
            current = teloped
        else:
            srt_path = None

        if bgm_path and os.path.exists(bgm_path):
            progress(base_pct + 18, f"{clip_label}: BGMをミックス中...")
            bgm_out = os.path.join(output_dir, f"short_{idx:02d}.mp4")
            mix_bgm(current, bgm_path, bgm_out, bgm_volume)
            current = bgm_out

        final_name = f"short_{idx + 1:02d}.mp4"
        final_path = os.path.join(output_dir, final_name)
        if current != final_path:
            os.rename(current, final_path)

        clips.append({
            "index": idx,
            "filename": final_name,
            "start": round(start, 1),
            "duration": int(actual_dur),
            "srt": os.path.basename(srt_path) if srt_path and os.path.exists(srt_path) else None,
        })

        # cleanup temp files
        for tmp in [raw, vertical]:
            if os.path.exists(tmp) and tmp != final_path:
                os.remove(tmp)

    progress(100, f"完了！{len(clips)}本のショート動画を生成しました")
    return {"duration": duration, "clips": clips, "energy": energy[::5]}  # sample every 5pts
