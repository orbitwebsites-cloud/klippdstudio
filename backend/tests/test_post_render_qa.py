import json
import subprocess
from pathlib import Path

from post_render_qa import inspect_ass, review_render


def _completed(command, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class FakeRunner:
    def __init__(self, *, duration=60.0, scenes=None, audio=True, lufs=-14.5, peak=-1.2):
        self.duration = duration
        self.scenes = scenes if scenes is not None else list(range(3, 60, 4))
        self.audio = audio
        self.lufs = lufs
        self.peak = peak
        self.commands = []

    def __call__(self, command, timeout):
        self.commands.append((command, timeout))
        if command[0] == "ffprobe":
            streams = [{"codec_type": "video", "codec_name": "h264", "width": 1920,
                        "height": 1080, "avg_frame_rate": "60/1"}]
            if self.audio:
                streams.append({"codec_type": "audio", "codec_name": "aac"})
            return _completed(command, json.dumps({"format": {"duration": str(self.duration)}, "streams": streams}))
        if "metadata=print:file=-" in " ".join(command):
            stdout = "\n".join(f"frame:{i} pts:{int(t * 1000)} pts_time:{t}" for i, t in enumerate(self.scenes))
            return _completed(command, stdout=stdout)
        payload = {"input_i": str(self.lufs), "input_tp": str(self.peak), "input_lra": "5.1"}
        return _completed(command, stderr="loudnorm summary\n" + json.dumps(payload))


def _video(tmp_path: Path) -> Path:
    path = tmp_path / "render.mp4"
    path.write_bytes(b"not decoded because tools are mocked")
    return path


def _ass(tmp_path: Path, *, margin=60, size=54, outline=3, text="Readable caption") -> Path:
    path = tmp_path / "captions.ass"
    path.write_text(
        "[Script Info]\nPlayResX: 1920\nPlayResY: 1080\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BorderStyle, Outline, MarginL, MarginR, MarginV\n"
        f"Style: Default,Arial,{size},&H00FFFFFF,&H00000000,1,{outline},{margin},{margin},{margin}\n"
        "[Events]\nFormat: Layer, Start, End, Style, MarginL, MarginR, MarginV, Text\n"
        f"Dialogue: 0,0:00:01.00,0:00:03.00,Default,0,0,0,{text}\n",
        encoding="utf-8",
    )
    return path


def test_good_gaming_render_passes_with_structured_metrics(tmp_path):
    runner = FakeRunner()
    result = review_render(_video(tmp_path), _ass(tmp_path), runner=runner)

    assert result["schema_version"] == "klippd.post_render_qa.v1"
    assert result["passed"] is True
    assert result["evaluable"] is True
    assert result["metrics"]["duration_seconds"] == 60.0
    assert result["metrics"]["cut_proxy"]["rate_per_minute"] == 15.0
    assert result["metrics"]["captions"]["safe_zone_pass_rate"] == 1.0
    assert result["metrics"]["audio"]["integrated_lufs"] == -14.5
    assert all(timeout == 45.0 for _, timeout in runner.commands)
    assert all(command[0] in {"ffprobe", "ffmpeg"} for command, _ in runner.commands)


def test_slow_gaming_render_hard_fails(tmp_path):
    result = review_render(_video(tmp_path), runner=FakeRunner(scenes=[20, 40]))
    codes = {item["code"] for item in result["issues"]}
    assert "gaming_cut_proxy_too_slow" in codes
    assert result["hard_fail"] is True
    assert result["passed"] is False


def test_missing_audio_and_unmeasurable_probe_fail_closed(tmp_path):
    no_audio = review_render(_video(tmp_path), runner=FakeRunner(audio=False))
    assert "gaming_audio_missing" in {item["code"] for item in no_audio["issues"]}
    assert no_audio["evaluable"] is False

    def broken_probe(command, timeout):
        return _completed(command, stderr="corrupt container", returncode=1)

    corrupt = review_render(_video(tmp_path), runner=broken_probe)
    assert corrupt["passed"] is False
    assert corrupt["hard_fail"] is True
    assert "probe_not_evaluable" in {item["code"] for item in corrupt["issues"]}


def test_loudness_and_true_peak_limits_are_hard_gates(tmp_path):
    result = review_render(_video(tmp_path), runner=FakeRunner(lufs=-20.0, peak=0.2))
    codes = {item["code"] for item in result["issues"]}
    assert {"gaming_loudness_out_of_range", "gaming_true_peak_too_high"} <= codes
    assert result["passed"] is False


def test_ass_unsafe_or_unreadable_metadata_fails(tmp_path):
    ass = _ass(tmp_path, margin=2, size=12, outline=0, text="x" * 100)
    result = inspect_ass(ass)
    assert result["unsafe_event_count"] == 1
    assert result["unreadable_event_count"] == 1

    review = review_render(_video(tmp_path), ass, runner=FakeRunner())
    codes = {item["code"] for item in review["issues"]}
    assert {"caption_safe_zone_failed", "caption_readability_failed"} <= codes
    assert review["passed"] is False


def test_analysis_is_capped_and_marks_partial_render(tmp_path):
    runner = FakeRunner(duration=5000, scenes=list(range(10, 600, 10)))
    result = review_render(_video(tmp_path), runner=runner, max_analysis_seconds=9999)
    assert result["limits"]["max_analysis_seconds"] == 600.0
    assert result["metrics"]["cut_proxy"]["sampled_seconds"] == 600.0
    assert result["metrics"]["cut_proxy"]["full_render"] is False
    ffmpeg_commands = [command for command, _ in runner.commands if command[0] == "ffmpeg"]
    assert all(command[command.index("-t") + 1] == "600.000" for command in ffmpeg_commands)


def test_missing_ass_is_not_evaluable_when_requested(tmp_path):
    result = review_render(_video(tmp_path), tmp_path / "missing.ass", runner=FakeRunner())
    assert result["evaluable"] is False
    assert "captions_not_evaluable" in {item["code"] for item in result["issues"]}
