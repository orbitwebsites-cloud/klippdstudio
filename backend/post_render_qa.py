"""Deterministic, fail-closed quality review for rendered Gaming videos.

The analyzer intentionally uses only local files and invokes FFprobe/FFmpeg
without a shell. Expensive decoding is capped so it can be called by a future
worker without allowing an arbitrary render to monopolize that worker.
"""
from __future__ import annotations

import csv
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


SCHEMA_VERSION = "klippd.post_render_qa.v1"
MAX_FILE_BYTES = 12 * 1024 * 1024 * 1024
MAX_ANALYSIS_SECONDS = 600.0
COMMAND_TIMEOUT_SECONDS = 45.0
MAX_COMMAND_TIMEOUT_SECONDS = 60.0
MAX_TOOL_OUTPUT_CHARS = 2_000_000
SCENE_THRESHOLD = 0.30

CommandRunner = Callable[[List[str], float], subprocess.CompletedProcess[str]]


def _run(command: List[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _issue(code: str, message: str, severity: str = "critical",
           metric: Optional[str] = None) -> Dict[str, Any]:
    item: Dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if metric:
        item["metric"] = metric
    return item


def _float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rate(value: Any) -> Optional[float]:
    if not value or str(value) in {"0/0", "N/A"}:
        return None
    try:
        numerator, denominator = str(value).split("/", 1)
        result = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return None
    return round(result, 3) if math.isfinite(result) else None


def _invoke(runner: CommandRunner, command: List[str], timeout: float) -> subprocess.CompletedProcess[str]:
    result = runner(command, timeout)
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if len(stdout) + len(stderr) > MAX_TOOL_OUTPUT_CHARS:
        raise ValueError("tool output exceeded the QA safety limit")
    return result


def _probe(video: Path, runner: CommandRunner, timeout: float) -> Dict[str, Any]:
    command = [
        "ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(video),
    ]
    result = _invoke(runner, command, timeout)
    if result.returncode != 0:
        raise ValueError((result.stderr or "ffprobe failed").strip()[:500])
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        raise ValueError("ffprobe returned a non-object response")
    return parsed


def _scene_times(video: Path, seconds: float, runner: CommandRunner,
                 timeout: float) -> List[float]:
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", str(video),
        "-t", f"{seconds:.3f}", "-an", "-sn",
        "-vf", f"scale=320:-2,select=gt(scene\\,{SCENE_THRESHOLD}),metadata=print:file=-",
        "-vsync", "vfr", "-f", "null", "-",
    ]
    result = _invoke(runner, command, timeout)
    if result.returncode != 0:
        raise ValueError((result.stderr or "scene analysis failed").strip()[:500])
    values = {_float(match) for match in re.findall(r"pts_time:([0-9.]+)", result.stdout or "")}
    return sorted(round(value, 3) for value in values if value is not None and 0 < value <= seconds)


def _loudness(video: Path, seconds: float, runner: CommandRunner,
              timeout: float) -> Dict[str, Optional[float]]:
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "info", "-i", str(video),
        "-t", f"{seconds:.3f}", "-vn", "-sn",
        "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    result = _invoke(runner, command, timeout)
    if result.returncode != 0:
        raise ValueError((result.stderr or "loudness analysis failed").strip()[:500])
    candidates = re.findall(r"\{[^{}]*\}", result.stderr or "", flags=re.DOTALL)
    for candidate in reversed(candidates):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if "input_i" in data:
            return {
                "integrated_lufs": _float(data.get("input_i")),
                "true_peak_dbtp": _float(data.get("input_tp")),
                "loudness_range_lu": _float(data.get("input_lra")),
            }
    raise ValueError("FFmpeg did not emit a loudness summary")


def _csv_fields(line: str) -> List[str]:
    return next(csv.reader([line], skipinitialspace=True))


def _ass_section(lines: Iterable[str]) -> Iterable[tuple[str, str]]:
    section = ""
    for raw in lines:
        line = raw.strip().lstrip("\ufeff")
        if line.startswith("[") and line.endswith("]"):
            section = line.lower()
        elif line and not line.startswith(";"):
            yield section, line


def _ass_time(value: str) -> Optional[float]:
    match = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)", value.strip())
    if not match:
        return None
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def inspect_ass(ass_path: Path) -> Dict[str, Any]:
    """Inspect ASS metadata only; it does not claim pixel-level OCR validation."""
    if ass_path.stat().st_size > 5_000_000:
        raise ValueError("ASS file exceeds 5 MB")
    text = ass_path.read_text(encoding="utf-8-sig", errors="strict")
    play_x = play_y = None
    style_format: List[str] = []
    event_format: List[str] = []
    styles: Dict[str, Dict[str, str]] = {}
    events: List[Dict[str, str]] = []
    for section, line in _ass_section(text.splitlines()):
        key, separator, value = line.partition(":")
        if not separator:
            continue
        normalized = key.strip().lower()
        if section == "[script info]" and normalized == "playresx":
            play_x = _float(value)
        elif section == "[script info]" and normalized == "playresy":
            play_y = _float(value)
        elif "styles" in section and normalized == "format":
            style_format = [item.strip().lower() for item in _csv_fields(value)]
        elif "styles" in section and normalized == "style" and style_format:
            values = _csv_fields(value)
            if len(values) == len(style_format):
                item = dict(zip(style_format, values))
                styles[item.get("name", "Default").lower()] = item
        elif section == "[events]" and normalized == "format":
            event_format = [item.strip().lower() for item in _csv_fields(value)]
        elif section == "[events]" and normalized == "dialogue" and event_format:
            values = value.split(",", len(event_format) - 1)
            if len(values) == len(event_format):
                events.append(dict(zip(event_format, (item.strip() for item in values))))
    if not play_x or not play_y or not styles:
        raise ValueError("ASS requires PlayResX, PlayResY, and at least one valid style")

    unsafe = 0
    unreadable = 0
    evaluated = 0
    details: List[Dict[str, Any]] = []
    for index, event in enumerate(events):
        start, end = _ass_time(event.get("start", "")), _ass_time(event.get("end", ""))
        style = styles.get(event.get("style", "default").lower())
        if start is None or end is None or end <= start or style is None:
            unreadable += 1
            details.append({"event": index, "safe_zone": None, "readable": False, "reason": "invalid timing or style"})
            continue
        evaluated += 1
        font_ratio = (_float(style.get("fontsize")) or 0) / play_y
        margin_l = (_float(event.get("marginl")) or _float(style.get("marginl")) or 0) / play_x
        margin_r = (_float(event.get("marginr")) or _float(style.get("marginr")) or 0) / play_x
        margin_v = (_float(event.get("marginv")) or _float(style.get("marginv")) or 0) / play_y
        outline = _float(style.get("outline")) or 0
        border = int(_float(style.get("borderstyle")) or 1)
        visible_text = re.sub(r"\{[^}]*\}", "", event.get("text", "")).replace("\\N", "\n")
        longest_line = max((len(line) for line in visible_text.splitlines()), default=0)
        readable = 0.03 <= font_ratio <= 0.14 and longest_line <= 84 and end - start >= 0.5 and (border == 3 or outline >= 1)
        safe = margin_l >= 0.03 and margin_r >= 0.03 and margin_v >= 0.04
        position = re.search(r"\\pos\(([-0-9.]+),([-0-9.]+)\)", event.get("text", ""))
        if position:
            x, y = float(position.group(1)) / play_x, float(position.group(2)) / play_y
            safe = 0.05 <= x <= 0.95 and 0.05 <= y <= 0.92
        unsafe += int(not safe)
        unreadable += int(not readable)
        details.append({"event": index, "safe_zone": safe, "readable": readable})
    return {
        "provided": True,
        "evaluable": bool(events) and evaluated == len(events),
        "play_resolution": {"width": int(play_x), "height": int(play_y)},
        "event_count": len(events),
        "evaluated_event_count": evaluated,
        "unsafe_event_count": unsafe,
        "unreadable_event_count": unreadable,
        "safe_zone_pass_rate": round((evaluated - unsafe) / evaluated, 4) if evaluated else None,
        "readability_pass_rate": round((evaluated - unreadable) / evaluated, 4) if evaluated else None,
        "events": details[:1000],
        "event_details_truncated": len(details) > 1000,
    }


def review_render(video_path: str | Path, ass_path: str | Path | None = None,
                  *, niche: str = "gaming", runner: CommandRunner = _run,
                  max_analysis_seconds: float = MAX_ANALYSIS_SECONDS,
                  command_timeout_seconds: float = COMMAND_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Review one local render and return a JSON-serializable QA decision."""
    issues: List[Dict[str, Any]] = []
    video = Path(video_path).expanduser().resolve()
    gaming = niche.lower() in {"gaming", "minecraft", "minecraft_narrative"}
    metrics: Dict[str, Any] = {
        "duration_seconds": None, "video": {}, "cut_proxy": {},
        "captions": {"provided": ass_path is not None, "evaluable": ass_path is None},
        "audio": {},
    }
    tools = {"ffprobe": shutil.which("ffprobe") is not None, "ffmpeg": shutil.which("ffmpeg") is not None}
    timeout = max(1.0, min(float(command_timeout_seconds), MAX_COMMAND_TIMEOUT_SECONDS))
    sampled_seconds = 0.0

    if not video.is_file():
        issues.append(_issue("render_missing", "Rendered video does not exist.", metric="video"))
    elif video.stat().st_size <= 0 or video.stat().st_size > MAX_FILE_BYTES:
        issues.append(_issue("render_size_invalid", "Rendered video is empty or exceeds 12 GB.", metric="video"))
    else:
        try:
            probe = _probe(video, runner, timeout)
            streams = probe.get("streams") or []
            video_streams = [item for item in streams if item.get("codec_type") == "video"]
            audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
            duration = _float((probe.get("format") or {}).get("duration"))
            if duration is None and video_streams:
                duration = _float(video_streams[0].get("duration"))
            metrics["duration_seconds"] = round(duration, 3) if duration and duration > 0 else None
            if video_streams:
                stream = video_streams[0]
                metrics["video"] = {
                    "width": stream.get("width"), "height": stream.get("height"),
                    "codec": stream.get("codec_name"),
                    "fps": _rate(stream.get("avg_frame_rate")),
                }
            else:
                issues.append(_issue("video_stream_missing", "No video stream was found.", metric="video"))
            if not duration or duration <= 0:
                issues.append(_issue("duration_not_evaluable", "Positive render duration could not be measured.", metric="duration"))
            elif video_streams:
                sampled_seconds = min(duration, max(1.0, min(float(max_analysis_seconds), MAX_ANALYSIS_SECONDS)))
                try:
                    scene_times = _scene_times(video, sampled_seconds, runner, timeout)
                    boundaries = [0.0, *scene_times, sampled_seconds]
                    intervals = [right - left for left, right in zip(boundaries, boundaries[1:])]
                    rate_per_minute = len(scene_times) * 60.0 / sampled_seconds
                    metrics["cut_proxy"] = {
                        "method": f"scaled_scene_score_gt_{SCENE_THRESHOLD}",
                        "scene_change_count": len(scene_times),
                        "rate_per_minute": round(rate_per_minute, 3),
                        "median_interval_seconds": round(sorted(intervals)[len(intervals) // 2], 3),
                        "max_interval_seconds": round(max(intervals), 3),
                        "first_change_seconds": scene_times[0] if scene_times else None,
                        "sampled_seconds": round(sampled_seconds, 3),
                        "full_render": sampled_seconds >= duration,
                    }
                    if gaming and (rate_per_minute < 6 or max(intervals) > 12):
                        issues.append(_issue("gaming_cut_proxy_too_slow", "Scene-change proxy is below 6/min or contains a gap over 12 seconds.", metric="cut_proxy"))
                    elif gaming and rate_per_minute > 60:
                        issues.append(_issue("gaming_cut_proxy_overstimulated", "Scene-change proxy exceeds 60/min.", metric="cut_proxy"))
                except (FileNotFoundError, subprocess.TimeoutExpired, ValueError) as exc:
                    issues.append(_issue("cut_proxy_not_evaluable", f"Scene-change proxy failed: {str(exc)[:300]}", metric="cut_proxy"))

            if audio_streams and duration and duration > 0:
                try:
                    audio = _loudness(video, sampled_seconds, runner, timeout)
                    metrics["audio"] = {**audio, "present": True, "sampled_seconds": round(sampled_seconds, 3), "full_render": sampled_seconds >= duration}
                    integrated, peak = audio["integrated_lufs"], audio["true_peak_dbtp"]
                    if integrated is None or peak is None:
                        issues.append(_issue("audio_loudness_not_evaluable", "Integrated loudness or true peak was not finite.", metric="audio"))
                    else:
                        if gaming and not -18 <= integrated <= -12:
                            issues.append(_issue("gaming_loudness_out_of_range", "Gaming audio must measure between -18 and -12 LUFS.", metric="audio"))
                        if gaming and peak > -1:
                            issues.append(_issue("gaming_true_peak_too_high", "Gaming audio true peak must not exceed -1 dBTP.", metric="audio"))
                except (FileNotFoundError, subprocess.TimeoutExpired, ValueError) as exc:
                    issues.append(_issue("audio_loudness_not_evaluable", f"Loudness analysis failed: {str(exc)[:300]}", metric="audio"))
            else:
                metrics["audio"] = {"present": False}
                if gaming:
                    issues.append(_issue("gaming_audio_missing", "Gaming render requires an audio stream.", metric="audio"))
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as exc:
            issues.append(_issue("probe_not_evaluable", f"Render probe failed: {str(exc)[:300]}", metric="video"))

    if ass_path is not None:
        subtitle = Path(ass_path).expanduser().resolve()
        try:
            if not subtitle.is_file():
                raise ValueError("ASS file does not exist")
            metrics["captions"] = inspect_ass(subtitle)
            if not metrics["captions"]["evaluable"]:
                issues.append(_issue("captions_not_evaluable", "One or more ASS events could not be evaluated.", metric="captions"))
            if metrics["captions"]["unsafe_event_count"]:
                issues.append(_issue("caption_safe_zone_failed", "One or more captions fall outside metadata safe-zone limits.", metric="captions"))
            if metrics["captions"]["unreadable_event_count"]:
                issues.append(_issue("caption_readability_failed", "One or more captions fail metadata readability limits.", metric="captions"))
        except (OSError, UnicodeError, ValueError) as exc:
            metrics["captions"] = {"provided": True, "evaluable": False}
            issues.append(_issue("captions_not_evaluable", f"ASS inspection failed: {str(exc)[:300]}", metric="captions"))

    hard_fail = any(item["severity"] == "critical" for item in issues)
    required_evaluable = metrics["duration_seconds"] is not None and bool(metrics["cut_proxy"])
    if gaming:
        required_evaluable = required_evaluable and metrics["audio"].get("integrated_lufs") is not None
    if ass_path is not None:
        required_evaluable = required_evaluable and bool(metrics["captions"].get("evaluable"))
    return {
        "schema_version": SCHEMA_VERSION,
        "niche": niche,
        "video_path": str(video),
        "evaluable": required_evaluable,
        "passed": required_evaluable and not hard_fail,
        "hard_fail": hard_fail or not required_evaluable,
        "metrics": metrics,
        "issues": issues,
        "tools_detected": tools,
        "limits": {
            "max_file_bytes": MAX_FILE_BYTES,
            "max_analysis_seconds": min(float(max_analysis_seconds), MAX_ANALYSIS_SECONDS),
            "command_timeout_seconds": timeout,
            "max_tool_output_chars": MAX_TOOL_OUTPUT_CHARS,
            "caption_event_details": 1000,
        },
    }
