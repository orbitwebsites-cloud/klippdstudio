"""FFmpeg-based video processing pipeline.
- Extract audio
- Cut filler segments
- Burn animated ASS captions (TikTok, YouTube, or Luxury style)
- Overlay B-roll clips
- Add SFX on cuts
"""
import os
import subprocess
import json
import logging
import shlex
import re
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


_AUDIO_CONTAINER_FORMATS = {
    ".aac": frozenset({"aac"}),
    ".flac": frozenset({"flac"}),
    ".m4a": frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}),
    ".mp3": frozenset({"mp3"}),
    ".ogg": frozenset({"ogg"}),
    ".wav": frozenset({"wav"}),
}
_AUDIO_FORMAT_WHITELIST = ",".join(
    sorted({name for names in _AUDIO_CONTAINER_FORMATS.values() for name in names})
)


def _local_audio_input_options() -> List[str]:
    """Restrict untrusted music inputs to local, non-playlist demuxers."""
    return [
        "-protocol_whitelist", "file",
        "-format_whitelist", _AUDIO_FORMAT_WHITELIST,
    ]


def run_ff(cmd: list, log: bool = True) -> None:
    if log:
        logger.info("FFMPEG: " + " ".join(shlex.quote(c) for c in cmd[:20]) + (" ..." if len(cmd) > 20 else ""))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        logger.error(f"FFmpeg failed: {proc.stderr[-1500:]}")
        raise RuntimeError(f"FFmpeg failed: {proc.stderr[-500:]}")


def probe_video(path: str) -> Dict[str, Any]:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(proc.stdout)
    v_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    return {
        "duration": float(data.get("format", {}).get("duration", 0)),
        "width": int(v_stream.get("width", 0)),
        "height": int(v_stream.get("height", 0)),
        "fps": _parse_fps(v_stream.get("r_frame_rate", "30/1")),
        "size": int(data.get("format", {}).get("size", 0)),
    }


def probe_audio(path: str, expected_extension: Optional[str] = None) -> Dict[str, Any]:
    """Probe an uploaded music bed without requiring a video stream."""
    extension = (expected_extension or os.path.splitext(path)[1]).lower()
    expected_formats = _AUDIO_CONTAINER_FORMATS.get(extension)
    if not expected_formats:
        raise RuntimeError("Unsupported audio container")
    cmd = [
        "ffprobe", "-v", "quiet", *_local_audio_input_options(),
        "-print_format", "json", "-show_format", "-show_streams", path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
    data = json.loads(proc.stdout)
    format_name = str(data.get("format", {}).get("format_name", ""))
    detected_formats = {name.strip().lower() for name in format_name.split(",") if name.strip()}
    if not detected_formats.intersection(expected_formats):
        raise RuntimeError(f"Audio container does not match {extension}")
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not audio:
        raise RuntimeError("No audio stream found")
    return {
        "duration": float(data.get("format", {}).get("duration", 0)),
        "channels": int(audio.get("channels", 0)),
        "sample_rate": int(audio.get("sample_rate", 0) or 0),
        "size": int(data.get("format", {}).get("size", 0)),
        "format_name": format_name,
    }


def _parse_fps(rate: str) -> float:
    try:
        num, den = rate.split("/")
        return float(num) / float(den)
    except Exception:
        return 30.0


def extract_audio(input_path: str, output_path: str) -> None:
    """Extract compressed mp3 audio suitable for Whisper (under 25MB)."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vn", "-ac", "1", "-ar", "16000",
        "-b:a", "48k",
        output_path,
    ]
    run_ff(cmd)


def analyze_audio_energy(input_path: str, frame_seconds: float = 0.5) -> List[Dict[str, float]]:
    """Sample RMS and peak levels without loading the source audio into memory."""
    sample_count = max(800, int(16000 * frame_seconds))
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", input_path, "-vn", "-ac", "1", "-ar", "16000",
        "-af", f"aresample=16000,asetnsamples=n={sample_count}:p=0,astats=metadata=1:reset=1,ametadata=mode=print:file=-",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Audio signal sampling unavailable: %s", exc)
        return []
    if proc.returncode != 0:
        logger.warning("Audio signal sampling failed: %s", proc.stderr[-500:])
        return []

    frames = []
    current: Dict[str, float] = {}
    for line in proc.stdout.splitlines():
        time_match = re.search(r"pts_time:([0-9.]+)", line)
        if time_match:
            if "time" in current and "rms_db" in current:
                frames.append(current)
            current = {"time": float(time_match.group(1))}
            continue
        rms_match = re.search(r"lavfi\.astats\.Overall\.RMS_level=(-?(?:inf|[0-9.]+))", line, re.I)
        if rms_match:
            value = rms_match.group(1).lower()
            current["rms_db"] = float("-inf") if value == "-inf" else float(value)
            continue
        peak_match = re.search(r"lavfi\.astats\.Overall\.Peak_level=(-?(?:inf|[0-9.]+))", line, re.I)
        if peak_match:
            value = peak_match.group(1).lower()
            current["peak_db"] = float("-inf") if value == "-inf" else float(value)
    if "time" in current and "rms_db" in current:
        frames.append(current)
    return frames


def compress_upload(input_path: str, output_path: str, max_width: int = 1920,
                    crf: int = 26) -> None:
    """Create a compact H.264 working source without upscaling smaller uploads."""
    scale = f"scale=w='min({max_width},iw)':h=-2"
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-map", "0:v:0", "-map", "0:a?",
        "-vf", scale,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ar", "44100",
        "-movflags", "+faststart",
        output_path,
    ]
    run_ff(cmd)


# ---------- FILLER SEGMENT MERGING ----------
def build_keep_segments(words: List[Dict], filler_indices: List[int],
                        duration: float, pad: float = 0.03,
                        silence_threshold: float | None = None) -> List[Dict]:
    if not words:
        return [{"start": 0, "end": duration}]
    fillers = set(filler_indices or [])
    remove = []
    for i, w in enumerate(words):
        if i in fillers:
            s = max(0.0, float(w.get("start", 0)) - pad)
            e = float(w.get("end", 0)) + pad
            if e > s:
                remove.append((s, e))
    # Optional dead-air cleanup. Keep a small breath between phrases, while
    # removing only transcript-grounded gaps above the user's threshold.
    if silence_threshold is not None and silence_threshold > 0 and len(words) > 1:
        ordered = sorted(
            (w for w in words if isinstance(w, dict)),
            key=lambda item: float(item.get("start", 0) or 0),
        )
        for previous, current in zip(ordered, ordered[1:]):
            previous_end = float(previous.get("end", 0) or 0)
            current_start = float(current.get("start", 0) or 0)
            if current_start - previous_end >= silence_threshold:
                gap_start = previous_end + pad
                gap_end = current_start - pad
                if gap_end > gap_start:
                    remove.append((gap_start, gap_end))
    remove.sort()
    merged = []
    for s, e in remove:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    keep = []
    cursor = 0.0
    for s, e in merged:
        if s > cursor + 0.01:
            keep.append({"start": cursor, "end": s})
        cursor = max(cursor, e)
    if cursor < duration - 0.05:
        keep.append({"start": cursor, "end": duration})
    keep = [seg for seg in keep if seg["end"] - seg["start"] > 0.08]
    if not keep:
        keep = [{"start": 0, "end": duration}]
    return keep


# ---------- ASPECT RATIO ----------
def aspect_target_size(aspect: str, src_w: int, src_h: int) -> tuple:
    """Return (out_w, out_h) target output canvas."""
    if aspect == "9:16":
        return (1080, 1920)
    if aspect == "1:1":
        return (1080, 1080)
    # default 16:9 — keep close to source, capped at 1920
    if src_w >= src_h * 16 / 9:
        # H.264/yuv420p requires even dimensions. Phone captures and cropped
        # exports can report odd widths, which otherwise makes FFmpeg fail at
        # the end of an apparently successful upload.
        width = max(2, min(src_w, 1920))
        height = max(2, min(int(width * 9 / 16), 1080))
        return (width - width % 2, height - height % 2)
    return (1920, 1080)


def aspect_filter(src_w: int, src_h: int, out_w: int, out_h: int) -> str:
    """Return an FFmpeg filter chain that fits src into out via center-crop + scale + pad."""
    # First scale to cover, then crop center to exact aspect
    src_ratio = src_w / max(src_h, 1)
    out_ratio = out_w / max(out_h, 1)
    if abs(src_ratio - out_ratio) < 0.02:
        # Same aspect — just scale
        return f"scale={out_w}:{out_h}"
    if src_ratio > out_ratio:
        # Source wider — scale to output height, crop width
        return f"scale=-2:{out_h},crop={out_w}:{out_h}"
    # Source taller — scale to output width, crop height
    return f"scale={out_w}:-2,crop={out_w}:{out_h}"


# ---------- ASS SUBTITLE GEN ----------
def _fmt_time(sec: float) -> str:
    if sec < 0:
        sec = 0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _sanitize(text: str) -> str:
    return (text or "").replace("{", "").replace("}", "").replace("\\", "").strip()


def generate_ass(words: List[Dict], out_path: str, style: str,
                 res_w: int, res_h: int,
                 emphasis_set: Optional[set] = None,
                 keep_intervals: Optional[List[Dict]] = None) -> None:
    emphasis_set = emphasis_set or set()

    if style == "tiktok":
        font = "Impact"
        size = int(res_h * 0.055)
        primary = "&H00FFFFFF"
        outline = "&H00000000"
        emphasis_color = "&H005000FF"  # TikTok pink #FF0050 in ASS BGR
        outline_w = 5
        margin_v = int(res_h * 0.20)
        alignment = 2
        bold = -1
    elif style in {"luxury", "editorial"}:
        # Editorial white captions with restrained gold keyword emphasis.
        # DejaVu Serif ships with the production Linux image, unlike most
        # commercial display fonts, so this treatment renders consistently.
        font = "DejaVu Serif"
        size = int(res_h * 0.050)
        primary = "&H00FFFFFF"
        outline = "&H00101010"
        emphasis_color = "&H0037AFD4"  # #D4AF37 gold in ASS BGR
        outline_w = 2
        margin_v = int(res_h * (0.16 if style == "luxury" else 0.12))
        alignment = 2
        bold = -1
    elif style == "marketing":
        # High-contrast, brand-safe treatment for marketing explainers:
        # compact sans captions, yellow emphasis, and a slightly higher card
        # position so product/UI overlays have room below.
        font = "Arial"
        size = int(res_h * 0.050)
        primary = "&H00FFFFFF"
        outline = "&H00141414"
        emphasis_color = "&H0000D9FF"  # vivid yellow in ASS BGR
        outline_w = 3
        margin_v = int(res_h * 0.18)
        alignment = 2
        bold = -1
    else:  # youtube clean
        font = "Arial"
        size = int(res_h * 0.045)
        primary = "&H00FFFFFF"
        outline = "&H00000000"
        emphasis_color = "&H0000FFFF"
        outline_w = 3
        margin_v = int(res_h * 0.10)
        alignment = 2
        bold = -1

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {res_w}
PlayResY: {res_h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{primary},{outline},&H00000000,{bold},0,0,0,100,100,0,0,1,{outline_w},2,{alignment},60,60,{margin_v},1
Style: Emph,{font},{int(size*1.2)},{emphasis_color},{outline},&H00000000,{bold},0,0,0,100,100,0,0,1,{outline_w+1},3,{alignment},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def _remap(t: float) -> Optional[float]:
        if not keep_intervals:
            return t
        offset = 0.0
        for seg in keep_intervals:
            s, e = seg["start"], seg["end"]
            if t < s:
                return None
            if t <= e:
                return offset + (t - s)
            offset += (e - s)
        return None

    # Word-level timing is useful for karaoke highlighting, but one dialogue
    # event per word becomes unreadable on normal speech. Group nearby words
    # into short, legible cards while preserving emphasis when any word in the
    # card is emphasized.
    caption_groups = []
    current = []
    for i, w in enumerate(words):
        txt = _sanitize(w.get("word", ""))
        if not txt:
            continue
        start = float(w.get("start", 0))
        end = float(w.get("end", start + 0.15))
        rs = _remap(start)
        re_ = _remap(end)
        if rs is None or re_ is None or re_ <= rs:
            continue
        item = {"index": i, "text": txt, "start": rs, "end": max(re_, rs + 0.12)}
        if current:
            gap = item["start"] - current[-1]["end"]
            span = item["end"] - current[0]["start"]
            if gap > 0.24 or len(current) >= 3 or span > 1.35:
                caption_groups.append(current)
                current = []
        current.append(item)
    if current:
        caption_groups.append(current)

    # Avoid flash captions caused by a short final word or a brief pause. A
    # readable card needs roughly half a second; merge tiny adjacent cards
    # where possible, then extend isolated cards only into their available
    # gap so captions never overlap.
    merged_groups = []
    for group in caption_groups:
        duration = group[-1]["end"] - group[0]["start"]
        if merged_groups and duration < 0.5:
            previous = merged_groups[-1]
            gap = group[0]["start"] - previous[-1]["end"]
            if gap <= 0.4 and len(previous) + len(group) <= 6:
                previous.extend(group)
                continue
        merged_groups.append(group)
    for i in range(len(merged_groups) - 2, -1, -1):
        group = merged_groups[i]
        duration = group[-1]["end"] - group[0]["start"]
        following = merged_groups[i + 1]
        gap = following[0]["start"] - group[-1]["end"]
        if duration < 0.5 and gap <= 0.4 and len(group) + len(following) <= 6:
            group.extend(following)
            del merged_groups[i + 1]
    caption_groups = merged_groups

    lines = [header]
    for group_index, group in enumerate(caption_groups):
        txt = " ".join(item["text"] for item in group)
        rs = group[0]["start"]
        re_ = group[-1]["end"]
        if re_ - rs < 0.5:
            next_start = caption_groups[group_index + 1][0]["start"] if group_index + 1 < len(caption_groups) else None
            re_ = min(rs + 0.5, next_start - 0.03) if next_start is not None else rs + 0.5
            if re_ <= rs:
                re_ = group[-1]["end"]
        emphasized = any(item["index"] in emphasis_set for item in group)
        style_name = "Emph" if emphasized else "Default"
        if style == "luxury":
            y = int(res_h * 0.84)
            start_scale = 128 if emphasized else 112
            effect = rf"{{\an2\move({res_w // 2},{y + 42},{res_w // 2},{y},0,180)\fad(50,90)\fscx{start_scale}\fscy{start_scale}\t(0,180,\fscx100\fscy100)}}"
        else:
            effect = r"{\fad(60,60)\t(0,80,\fscx115\fscy115)\t(80,160,\fscx100\fscy100)}"
            if emphasized:
                effect = r"{\fad(40,60)\t(0,100,\fscx140\fscy140)\t(100,220,\fscx110\fscy110)}"
        lines.append(f"Dialogue: 0,{_fmt_time(rs)},{_fmt_time(re_)},{style_name},,0,0,0,,{effect}{txt}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------- CONCAT (cut fillers) ----------
def cut_and_concat(input_path: str, keep_segments: List[Dict], output_path: str,
                   res_w: int = 1920, res_h: int = 1080,
                   src_w: Optional[int] = None, src_h: Optional[int] = None,
                   clip_start: Optional[float] = None, clip_end: Optional[float] = None) -> None:
    """Cut input into keep_segments and concatenate to output.
    If clip_start/clip_end given, first slice the source to that range, then apply cuts.
    res_w/res_h = target output canvas (for aspect ratio changes).
    src_w/src_h = source video dimensions (used to build aspect filter)."""
    src_w = src_w or res_w
    src_h = src_h or res_h
    a_filter = aspect_filter(src_w, src_h, res_w, res_h)

    # Adjust keep_segments if clip_start/end provided (slice into that window)
    if clip_start is not None and clip_end is not None:
        new_keep = []
        for seg in keep_segments:
            s = max(seg["start"], clip_start)
            e = min(seg["end"], clip_end)
            if e - s > 0.08:
                new_keep.append({"start": s, "end": e})
        keep_segments = new_keep or [{"start": clip_start, "end": clip_end}]

    filters = []
    parts_v = []
    parts_a = []
    for i, seg in enumerate(keep_segments):
        s, e = seg["start"], seg["end"]
        filters.append(f"[0:v]trim=start={s}:end={e},setpts=PTS-STARTPTS,{a_filter}[v{i}]")
        filters.append(f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS[a{i}]")
        parts_v.append(f"[v{i}]")
        parts_a.append(f"[a{i}]")
    n = len(keep_segments)
    filters.append(f"{''.join(parts_v)}concat=n={n}:v=1:a=0[vout]")
    filters.append(f"{''.join(parts_a)}concat=n={n}:v=0:a=1[aout]")
    filter_complex = ";".join(filters)

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-profile:v", "main", "-level", "4.0",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart",
        output_path,
    ]
    run_ff(cmd)


# ---------- FULL RENDER ----------
def render_final(cut_video: str, ass_file: Optional[str], sfx_events: List[float],
                 broll_events: List[Dict], sfx_dir: str, output_path: str,
                 bgm_path: Optional[str] = None, bgm_volume: float = 0.16) -> None:
    base_meta = probe_video(cut_video)
    canvas_w = max(2, int(base_meta.get("width") or 1920))
    canvas_h = max(2, int(base_meta.get("height") or 1080))
    inputs = ["-i", cut_video]
    input_idx = 1

    broll_input_map = []
    image_extensions = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".svg"}
    for b in broll_events:
        local = b.get("local_path")
        if not local or not os.path.exists(local):
            continue
        # Still images and logo files need a looped input so they can be used
        # as timed overlays in the same way as a video B-roll clip.
        if os.path.splitext(local)[1].lower() in image_extensions:
            inputs += ["-loop", "1", "-t", str(b.get("out_duration", 3.5)), "-i", local]
        else:
            inputs += ["-i", local]
        broll_input_map.append((input_idx, b))
        input_idx += 1

    whoosh_path = os.path.join(sfx_dir, "whoosh.wav")
    has_whoosh = os.path.exists(whoosh_path) and sfx_events
    whoosh_idx = None
    if has_whoosh:
        inputs += ["-i", whoosh_path]
        whoosh_idx = input_idx
        input_idx += 1

    bgm_idx = None
    if bgm_path and os.path.exists(bgm_path):
        inputs += ["-stream_loop", "-1", *_local_audio_input_options(), "-i", bgm_path]
        bgm_idx = input_idx
        input_idx += 1

    filters = []
    cur = "[0:v]"

    for i, (idx, b) in enumerate(broll_input_map):
        start = b.get("out_start", 0)
        duration = b.get("out_duration", 3.5)
        end = start + duration
        fit = b.get("fit", "cover")
        if fit == "full":
            prepare = f"scale={canvas_w}:{canvas_h}"
            position = "x=0:y=0"
        elif fit == "pip":
            prepare = "scale=iw*0.35:-2"
            position = "x=W-w-30:y=30"
        else:
            prepare = (
                f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,"
                f"crop={canvas_w}:{canvas_h}"
            )
            position = "x=0:y=0"
        filters.append(f"[{idx}:v]{prepare},setpts=PTS-STARTPTS+{start}/TB[br{i}]")
        filters.append(
            f"{cur}[br{i}]overlay={position}:eof_action=pass:enable='between(t,{start},{end})'[vo{i}]"
        )
        cur = f"[vo{i}]"

    if ass_file and os.path.exists(ass_file):
        safe_ass = ass_file.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        filters.append(f"{cur}subtitles='{safe_ass}'[vsub]")
        cur = "[vsub]"

    # Ensure video has a filter-output label (needed for filter_complex mapping)
    if cur == "[0:v]":
        filters.append("[0:v]null[vout]")
        cur = "[vout]"

    audio_cur = "[0:a]"
    if has_whoosh and sfx_events:
        delays = []
        for i, t in enumerate(sfx_events[:12]):
            ms = max(0, int(t * 1000))
            filters.append(f"[{whoosh_idx}:a]adelay={ms}|{ms},volume=0.3[sfx{i}]")
            delays.append(f"[sfx{i}]")
        if delays:
            mix_inputs = "[0:a]" + "".join(delays)
            filters.append(
                f"{mix_inputs}amix=inputs={len(delays)+1}:duration=first:dropout_transition=0:normalize=0[amix]"
            )
            audio_cur = "[amix]"
    else:
        # Passthrough audio via anull so filter output label exists
        filters.append("[0:a]anull[aout]")
        audio_cur = "[aout]"

    if bgm_idx is not None:
        # Keep the music present, but automatically duck it under the spoken
        # track. The small volume default mirrors the reference mixes.
        filters.append(f"[{bgm_idx}:a]volume={max(0.0, min(0.5, float(bgm_volume)))}[bgm0]")
        filters.append(f"[bgm0][0:a]sidechaincompress=threshold=0.035:ratio=8:attack=20:release=260[bgmduck]")
        filters.append(f"{audio_cur}[bgmduck]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[bgm_mix]")
        audio_cur = "[bgm_mix]"

    # Match the reference pack's finished-ad loudness while preserving headroom
    # for mobile playback and short-form platform normalization.
    filters.append(f"{audio_cur}loudnorm=I=-14:TP=-1.5:LRA=7:linear=false[final_audio]")
    audio_cur = "[final_audio]"

    filter_complex = ";".join(filters) if filters else None

    cmd = ["ffmpeg", "-y"] + inputs
    if filter_complex:
        # If audio wasn't touched by any filter, reference the stream directly without brackets
        audio_map = audio_cur if audio_cur != "[0:a]" else "0:a"
        cmd += ["-filter_complex", filter_complex, "-map", cur, "-map", audio_map]
    else:
        cmd += ["-map", "0:v", "-map", "0:a"]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-profile:v", "main", "-level", "4.0",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart",
        "-shortest",
        output_path,
    ]
    run_ff(cmd)


async def download_broll(url: str, dest_path: str, max_bytes: int = 500 * 1024 * 1024) -> bool:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
            async with c.stream("GET", url) as r:
                r.raise_for_status()
                declared = int(r.headers.get("content-length") or 0)
                if declared > max_bytes:
                    raise RuntimeError("B-roll download exceeds size limit")
                total = 0
                with open(dest_path, "wb") as f:
                    async for chunk in r.aiter_bytes(1024 * 256):
                        total += len(chunk)
                        if total > max_bytes:
                            raise RuntimeError("B-roll download exceeds size limit")
                        f.write(chunk)
        return os.path.getsize(dest_path) > 1024
    except Exception as e:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        logger.warning(f"Broll download failed {url}: {e}")
        return False
