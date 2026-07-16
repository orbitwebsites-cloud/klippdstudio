from __future__ import annotations

import asyncio
import io
import json
import shutil
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

import server
import video_processor as vp


ATTESTATION = "I own or have commercial rights to this asset"


def _probe_result(format_name: str, codec_type: str = "audio") -> SimpleNamespace:
    return SimpleNamespace(
        stdout=json.dumps({
            "format": {"format_name": format_name, "duration": "1.5", "size": "64"},
            "streams": [{"codec_type": codec_type, "channels": 2, "sample_rate": "44100"}],
        })
    )


def test_audio_probe_is_local_only_and_validates_detected_container(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _probe_result("mp3")

    monkeypatch.setattr(vp.subprocess, "run", fake_run)

    metadata = vp.probe_audio("uploaded.mp3", expected_extension=".mp3")

    assert metadata["format_name"] == "mp3"
    assert captured["command"][captured["command"].index("-protocol_whitelist") + 1] == "file"
    format_whitelist = captured["command"][captured["command"].index("-format_whitelist") + 1]
    assert "hls" not in format_whitelist
    assert "concat" not in format_whitelist
    assert captured["kwargs"]["timeout"] == 30


@pytest.mark.parametrize("detected_format", ["hls", "concat", "wav"])
def test_audio_probe_rejects_playlist_or_mismatched_container(monkeypatch, detected_format):
    monkeypatch.setattr(vp.subprocess, "run", lambda *_args, **_kwargs: _probe_result(detected_format))

    with pytest.raises(RuntimeError, match="does not match"):
        vp.probe_audio("disguised.mp3", expected_extension=".mp3")


def test_background_music_render_input_uses_local_audio_policy(tmp_path, monkeypatch):
    cut_video = tmp_path / "cut.mp4"
    music = tmp_path / "music.mp3"
    cut_video.write_bytes(b"video")
    music.write_bytes(b"audio")
    captured = {}

    monkeypatch.setattr(vp, "probe_video", lambda _path: {"width": 1280, "height": 720})
    monkeypatch.setattr(vp, "run_ff", lambda command: captured.setdefault("command", command))

    vp.render_final(
        str(cut_video), None, [], [], str(tmp_path / "sfx"), str(tmp_path / "out.mp4"), str(music)
    )

    command = captured["command"]
    music_index = command.index(str(music))
    music_options = command[:music_index]
    assert music_options[music_options.index("-protocol_whitelist") + 1] == "file"
    format_whitelist = music_options[music_options.index("-format_whitelist") + 1]
    assert "hls" not in format_whitelist
    assert "concat" not in format_whitelist


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe is required")
@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("playlist.mp3", b"#EXTM3U\n#EXTINF:10,\nhttp://127.0.0.1:9/private\n"),
        ("reference.wav", b"ffconcat version 1.0\nfile 'neighbor.wav'\n"),
    ],
)
def test_music_upload_rejects_reference_payloads_disguised_as_audio(
    tmp_path, monkeypatch, filename, payload
):
    data_dir = tmp_path / "data"
    (data_dir / "music").mkdir(parents=True)
    if filename == "reference.wav":
        with wave.open(str(data_dir / "music" / "neighbor.wav"), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x00\x00" * 800)

    async def fake_get_project(_pid):
        return {"id": "project-1"}

    async def unexpected_update(*_args, **_kwargs):
        pytest.fail("rejected music must not be attached to the project")

    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "get_project", fake_get_project)
    monkeypatch.setattr(server, "update_project", unexpected_update)
    upload = UploadFile(filename=filename, file=io.BytesIO(payload))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(server.music_upload(
            "project-1",
            upload,
            rights_status="user_owned_attested",
            rights_attestation=ATTESTATION,
        ))

    assert exc_info.value.status_code == 400
    assert list((data_dir / "music").glob("project-1_*")) == []
