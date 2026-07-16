"""Create six original, rights-safe editorial benchmark videos.

The clips are synthetic test media with Windows' local speech synthesizer and
simple generated visuals. They are intentionally not training examples from
third-party creators.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import av
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "editorial_bakeoff"
FPS = 24
SIZE = (1280, 720)

CASES = [
    ("finance", "margin-mystery", "Revenue climbed this quarter, but margin fell. The hidden cost is inventory sitting too long. Watch cash conversion before celebrating growth."),
    ("finance", "rate-cut-reaction", "The rate cut is not automatically bullish. The useful question is why it happened, what it changes for borrowing, and which businesses actually benefit."),
    ("finance", "three-number-check", "Before buying the headline, check three numbers: free cash flow, customer retention, and dilution. Together they tell a better story than revenue alone."),
    ("gaming", "one-heart-run", "One heart left, no armor, and the fortress is still three rooms away. The safe route is gone, so we are taking the risky bridge and saving the potion for the boss."),
    ("gaming", "redstone-proof", "The farm looked finished, but the output was zero. The missing repeater was one block underground. We fix the signal, test the clock, and finally see the drops arrive."),
    ("gaming", "last-circle", "The last circle closes on the ridge. We have height, one magazine, and two teams below us. Wait for the first fight, then take the clean angle before the zone moves."),
]


def font(size: int):
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("C:/Windows/Fonts/seguisb.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def synthesize(text: str, path: Path) -> None:
    escaped_text = text.replace("'", "''")
    escaped_path = str(path).replace("'", "''")
    command = (
        "Add-Type -AssemblyName System.Speech; "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{escaped_path}'); "
        f"$s.Speak('{escaped_text}'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True, capture_output=True)


def audio_duration(path: Path) -> float:
    container = av.open(str(path))
    stream = container.streams.audio[0]
    duration = float(stream.duration * stream.time_base) if stream.duration else 18.0
    container.close()
    return max(8.0, duration)


def make_video(case_id: str, niche: str, title: str, seconds: float, path: Path) -> None:
    container = av.open(str(path), mode="w")
    stream = container.add_stream("libx264", rate=FPS)
    stream.width, stream.height = SIZE
    stream.pix_fmt = "yuv420p"
    stream.options = {"crf": "22", "preset": "veryfast"}
    title_font = font(54)
    body_font = font(30)
    label_font = font(22)
    frame_count = int(seconds * FPS)
    for frame_index in range(frame_count):
        t = frame_index / FPS
        if niche == "finance":
            image = Image.new("RGB", SIZE, (7, 16, 28))
            draw = ImageDraw.Draw(image)
            accent = (57, 210, 170)
            for y in range(120, 640, 80): draw.line((80, y, 1200, y), fill=(19, 43, 58), width=2)
            points = []
            for x in range(100, 1180, 16):
                y = 420 - int(110 * (x - 100) / 1080) - int(30 * __import__("math").sin((x + frame_index * 2) / 90))
                points.append((x, y))
            draw.line(points, fill=accent, width=6)
            draw.text((80, 50), "FINANCE / EVIDENCE FIRST", font=label_font, fill=accent)
            draw.text((80, 105), title.upper(), font=title_font, fill=(240, 248, 250))
            draw.text((80, 610), f"LIVE REVIEW FRAME  {t:05.1f}s", font=label_font, fill=(142, 174, 188))
        else:
            image = Image.new("RGB", SIZE, (13, 11, 26))
            draw = ImageDraw.Draw(image)
            accent = (204, 255, 0)
            for x in range(0, 1280, 64): draw.line((x, 120, x, 720), fill=(31, 27, 53), width=2)
            for y in range(120, 720, 64): draw.line((0, y, 1280, y), fill=(31, 27, 53), width=2)
            offset = int((t * 80) % 320)
            draw.rectangle((760 - offset, 300, 940 - offset, 480), outline=accent, width=6)
            draw.rectangle((250 + offset // 3, 220, 420 + offset // 3, 390), outline=(255, 79, 139), width=6)
            draw.text((80, 50), "GAMING / DECISION UNDER PRESSURE", font=label_font, fill=accent)
            draw.text((80, 105), title.upper(), font=title_font, fill=(246, 243, 255))
            draw.text((80, 610), f"LIVE REVIEW FRAME  {t:05.1f}s", font=label_font, fill=(172, 163, 200))
        frame = av.VideoFrame.from_image(image)
        frame.pts = frame_index
        for packet in stream.encode(frame): container.mux(packet)
    for packet in stream.encode(): container.mux(packet)
    container.close()


def mux(video_path: Path, audio_path: Path, output_path: Path) -> None:
    video_in = av.open(str(video_path))
    audio_in = av.open(str(audio_path))
    output = av.open(str(output_path), mode="w")
    video_out = output.add_stream("libx264", rate=FPS)
    video_out.width, video_out.height = SIZE
    video_out.pix_fmt = "yuv420p"
    video_out.options = {"crf": "22", "preset": "veryfast"}
    audio_out = output.add_stream("aac", rate=44100)
    audio_out.layout = "mono"
    for frame in video_in.decode(video=0):
        for packet in video_out.encode(frame): output.mux(packet)
    for frame in audio_in.decode(audio=0):
        for packet in audio_out.encode(frame): output.mux(packet)
    for packet in video_out.encode(): output.mux(packet)
    for packet in audio_out.encode(): output.mux(packet)
    output.close(); video_in.close(); audio_in.close()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": "klippd.editorial_bakeoff.v1", "rights": "originally generated benchmark media", "cases": []}
    for niche, case_id, script in CASES:
        title = case_id.replace("-", " ")
        wav = OUT / f"{case_id}.wav"
        silent = OUT / f"{case_id}.silent.mp4"
        final = OUT / f"{case_id}.mp4"
        synthesize(script, wav)
        seconds = audio_duration(wav)
        make_video(case_id, niche, title, seconds, silent)
        mux(silent, wav, final)
        silent.unlink(missing_ok=True); wav.unlink(missing_ok=True)
        manifest["cases"].append({"id": case_id, "niche": niche, "source": str(final.relative_to(ROOT)), "script": script, "rights": "original_generated", "duration_seconds": round(seconds, 2)})
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__": main()
