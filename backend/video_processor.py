import subprocess
import json
import re
import os
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
    has_audio = any(s["codec_type"] == "audio" for s in info.get("streams", []))
    video_stream = next((s for s in info.get("streams", []) if s["codec_type"] == "video"), None)
    width = int(video_stream["width"]) if video_stream else 1920
    height = int(video_stream["height"]) if video_stream else 1080
    return {"duration": duration, "has_audio": has_audio, "width": width, "height": height}


# ──────────────────────────────
# Silence detection
# ──────────────────────────────

def detect_silence(input_path: str, noise_db: float = -35.0, min_duration: float = 0.5) -> list[dict]:
    _, stderr = run_ffmpeg([
        "-i", input_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-",
    ])
    silences, start = [], None
    for line in stderr.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            start = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            silences.append({"start": start, "end": float(m.group(1))})
            start = None
    if start is not None:
        duration = get_video_info(input_path)["duration"]
        silences.append({"start": start, "end": duration})
    return silences


def silence_to_keep_segments(silences: list[dict], duration: float, padding: float = 0.1) -> list[dict]:
    keep, cursor = [], 0.0
    for s in silences:
        end = max(0.0, s["start"] - padding)
        if end - cursor > 0.05:
            keep.append({"start": round(cursor, 3), "end": round(end, 3)})
        cursor = s["end"] + padding
    if duration - cursor > 0.05:
        keep.append({"start": round(cursor, 3), "end": round(duration, 3)})
    return keep


# ──────────────────────────────
# Merge segments
# ──────────────────────────────

def merge_segments(input_path: str, segments: list[dict], output_path: str) -> None:
    if not segments:
        return
    inputs, filter_parts = [], []
    for i, seg in enumerate(segments):
        inputs += ["-ss", str(seg["start"]), "-t", str(seg["end"] - seg["start"]), "-i", input_path]
        filter_parts.append(f"[{i}:v][{i}:a]")
    concat = "".join(filter_parts) + f"concat=n={len(segments)}:v=1:a=1[v][a]"
    run_ffmpeg(inputs + ["-filter_complex", concat, "-map", "[v]", "-map", "[a]", output_path], timeout=900)


# ──────────────────────────────
# BGM mixing
# ──────────────────────────────

def mix_bgm(video_path: str, bgm_path: str, output_path: str, bgm_volume: float = 0.15) -> None:
    """Mix BGM into video. BGM loops if shorter than video."""
    run_ffmpeg([
        "-i", video_path,
        "-stream_loop", "-1", "-i", bgm_path,
        "-filter_complex",
        f"[1:a]volume={bgm_volume}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-shortest",
        output_path,
    ])


# ──────────────────────────────
# Subtitle generation (Whisper)
# ──────────────────────────────

def generate_subtitles(input_path: str, output_srt: str, language: str = "ja") -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(input_path, language=language, vad_filter=True)

    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt_time(seg.start)} --> {_fmt_srt_time(seg.end)}")
        lines.append(seg.text.strip())
        lines.append("")

    srt_content = "\n".join(lines)
    with open(output_srt, "w", encoding="utf-8") as f:
        f.write(srt_content)
    return srt_content


def _fmt_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def burn_subtitles(video_path: str, srt_path: str, output_path: str) -> None:
    """Burn subtitles into video."""
    # Escape path for ffmpeg filter
    escaped = srt_path.replace("\\", "/").replace(":", "\\:")
    run_ffmpeg([
        "-i", video_path,
        "-vf", f"subtitles='{escaped}':force_style='FontSize=18,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Bold=1'",
        "-c:a", "copy",
        output_path,
    ])


# ──────────────────────────────
# Thumbnail generation
# ──────────────────────────────

def extract_frame(input_path: str, output_path: str, timestamp: float) -> None:
    run_ffmpeg([
        "-ss", str(timestamp),
        "-i", input_path,
        "-frames:v", "1",
        "-q:v", "2",
        output_path,
    ])


def generate_thumbnail(
    frame_path: str,
    output_path: str,
    title: str = "",
    font_size: int = 72,
    text_color: tuple = (255, 255, 255),
    shadow_color: tuple = (0, 0, 0),
) -> None:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import textwrap

    img = Image.open(frame_path).convert("RGB")
    # Resize to standard YouTube thumbnail size
    img = img.resize((1280, 720), Image.LANCZOS)

    if not title:
        img.save(output_path, "JPEG", quality=95)
        return

    draw = ImageDraw.Draw(img)

    # Try to use a font with Japanese support, fall back to default
    font = None
    font_candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJKjp-Bold.otf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for fp in font_candidates:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, font_size)
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()

    # Gradient overlay at bottom
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    for y in range(img.height // 2, img.height):
        alpha = int(180 * (y - img.height // 2) / (img.height // 2))
        overlay_draw.line([(0, y), (img.width, y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Wrap text
    wrapped = textwrap.fill(title, width=20)
    lines = wrapped.split("\n")
    line_height = font_size + 10
    total_height = line_height * len(lines)
    y_start = img.height - total_height - 40

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (img.width - text_w) // 2
        # Shadow
        for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2)]:
            draw.text((x + dx, y_start + dy), line, font=font, fill=shadow_color)
        draw.text((x, y_start), line, font=font, fill=text_color)
        y_start += line_height

    img.save(output_path, "JPEG", quality=95)


# ──────────────────────────────
# Main pipeline
# ──────────────────────────────

def process_video(
    input_path: str,
    output_dir: str,
    # Silence cut
    do_silence_cut: bool = True,
    noise_db: float = -35.0,
    min_silence_duration: float = 0.5,
    silence_padding: float = 0.1,
    # BGM
    bgm_path: str | None = None,
    bgm_volume: float = 0.15,
    # Subtitles
    do_subtitles: bool = False,
    subtitle_language: str = "ja",
    burn_subs: bool = False,
    # Thumbnail
    do_thumbnail: bool = False,
    thumbnail_title: str = "",
    thumbnail_timestamp: float = 5.0,
    progress_callback=None,
) -> dict:

    os.makedirs(output_dir, exist_ok=True)
    ext = Path(input_path).suffix
    info = get_video_info(input_path)
    duration = info["duration"]
    result = {"duration": duration, "files": {}, "segments": [], "subtitles": None}

    def progress(pct: int, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    current_video = input_path
    progress(5, "開始")

    # ── Step 1: Silence cut ──
    if do_silence_cut:
        progress(10, "無音区間を検出中...")
        silences = detect_silence(current_video, noise_db, min_silence_duration)
        keep = silence_to_keep_segments(silences, duration, silence_padding)
        result["silences"] = silences
        result["keep_segments"] = keep
        progress(30, f"無音除去: {len(silences)}箇所検出。結合中...")
        if len(keep) > 1:
            cut_path = os.path.join(output_dir, f"cut{ext}")
            merge_segments(current_video, keep, cut_path)
            current_video = cut_path
            result["files"]["silence_cut"] = f"cut{ext}"
        elif len(keep) == 1:
            # Single keep segment — trim only
            from shutil import copy2
            cut_path = os.path.join(output_dir, f"cut{ext}")
            copy2(current_video, cut_path)
            current_video = cut_path
            result["files"]["silence_cut"] = f"cut{ext}"
        progress(40, "無音除去完了")
    else:
        result["silences"] = []
        result["keep_segments"] = [{"start": 0.0, "end": duration}]

    # ── Step 2: BGM mixing ──
    if bgm_path and os.path.exists(bgm_path):
        progress(45, "BGMをミックス中...")
        bgm_out = os.path.join(output_dir, f"bgm{ext}")
        mix_bgm(current_video, bgm_path, bgm_out, bgm_volume)
        current_video = bgm_out
        result["files"]["bgm"] = f"bgm{ext}"
        progress(55, "BGMミックス完了")

    # ── Step 3: Subtitles ──
    if do_subtitles:
        progress(60, "音声認識中（Whisper）...")
        srt_path = os.path.join(output_dir, "subtitles.srt")
        generate_subtitles(current_video, srt_path, subtitle_language)
        result["files"]["srt"] = "subtitles.srt"
        result["subtitles"] = srt_path

        if burn_subs:
            progress(75, "字幕を動画に焼き込み中...")
            sub_out = os.path.join(output_dir, f"subtitled{ext}")
            burn_subtitles(current_video, srt_path, sub_out)
            current_video = sub_out
            result["files"]["subtitled"] = f"subtitled{ext}"
        progress(80, "字幕生成完了")

    # ── Step 4: Thumbnail ──
    if do_thumbnail:
        progress(85, "サムネイルを生成中...")
        thumb_ts = min(thumbnail_timestamp, duration - 0.1)
        frame_path = os.path.join(output_dir, "thumb_frame.jpg")
        extract_frame(current_video, frame_path, thumb_ts)
        thumb_path = os.path.join(output_dir, "thumbnail.jpg")
        generate_thumbnail(frame_path, thumb_path, thumbnail_title)
        result["files"]["thumbnail"] = "thumbnail.jpg"
        progress(95, "サムネイル生成完了")

    # Final output is current_video
    result["files"]["output"] = os.path.basename(current_video)
    progress(100, "完了")
    return result
