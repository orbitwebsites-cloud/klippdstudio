"""Generate safe, reusable graphic assets when no suitable footage exists.

These are deliberately honest editorial graphics (titles, stats, labels and
callouts), not synthetic gameplay or fake evidence.  They render as transparent
PNGs so the existing FFmpeg B-roll overlay path can composite them on video.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List
import hashlib
import os
import re
import uuid

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 1920, 1080
GENERATOR_ID = "klipped_editorial_graphics"
GENERATOR_VERSION = "1.0.0"
KINDS = {"title_card", "stat_card", "player_label", "item_callout", "quote_card"}
ACCENTS = {
    "gold": "#E7B84B",
    "lime": "#CCFF00",
    "red": "#FF4D5E",
    "cyan": "#3DEBFF",
    "purple": "#A982FF",
    "white": "#FFFFFF",
}


def _font(size: int, bold: bool = False):
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _clean(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _fit(draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int, minimum: int = 42):
    size = start
    while size > minimum:
        font = _font(size, bold=True)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        size -= 4
    return _font(minimum, bold=True)


def _rounded_panel(draw: ImageDraw.ImageDraw, box, fill, outline, radius=34, width=3):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _render(spec: Dict[str, Any], destination: Path) -> None:
    kind = spec["kind"]
    accent = ACCENTS.get(spec.get("accent"), ACCENTS["lime"])
    text = _clean(spec.get("text"), 48) or "KEY MOMENT"
    subtext = _clean(spec.get("subtext"), 90)

    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    if kind == "player_label":
        box = (115, 775, 950, 970)
        _rounded_panel(draw, box, (7, 9, 12, 235), accent, radius=24, width=4)
        draw.rectangle((115, 775, 139, 970), fill=accent)
        font = _fit(draw, text.upper(), 700, 74)
        draw.text((180, 810), text.upper(), font=font, fill="white")
        if subtext:
            draw.text((183, 900), subtext, font=_font(31), fill=(220, 224, 230, 230))
    elif kind == "item_callout":
        box = (1030, 150, 1800, 520)
        _rounded_panel(draw, box, (7, 9, 12, 238), accent, radius=38, width=4)
        draw.ellipse((1110, 235, 1240, 365), fill=accent)
        draw.text((1151, 249), "!", font=_font(82, bold=True), fill="#080A0E")
        font = _fit(draw, text.upper(), 470, 68)
        draw.text((1290, 220), text.upper(), font=font, fill="white")
        if subtext:
            draw.multiline_text((1290, 320), subtext, font=_font(30), fill=(220, 224, 230, 230), spacing=8)
    elif kind == "stat_card":
        box = (535, 235, 1385, 850)
        _rounded_panel(draw, box, (5, 7, 10, 242), accent, radius=46, width=4)
        draw.text((610, 305), "THE NUMBER", font=_font(34, bold=True), fill=accent)
        font = _fit(draw, text, 700, 172, minimum=72)
        draw.text((610, 410), text, font=font, fill="white")
        if subtext:
            draw.multiline_text((615, 650), subtext, font=_font(38), fill=(225, 228, 235, 235), spacing=10)
    elif kind == "quote_card":
        box = (255, 245, 1665, 835)
        _rounded_panel(draw, box, (5, 7, 10, 238), accent, radius=46, width=4)
        draw.text((345, 280), "\u201c", font=_font(150, bold=True), fill=accent)
        font = _fit(draw, text, 1160, 92, minimum=54)
        draw.text((395, 440), text, font=font, fill="white")
        if subtext:
            draw.text((405, 675), subtext, font=_font(36), fill=(220, 224, 230, 230))
    else:  # title_card
        box = (235, 330, 1685, 755)
        _rounded_panel(draw, box, (5, 7, 10, 238), accent, radius=46, width=4)
        draw.rectangle((315, 405, 430, 419), fill=accent)
        font = _fit(draw, text.upper(), 1200, 108, minimum=60)
        draw.text((315, 460), text.upper(), font=font, fill="white")
        if subtext:
            draw.text((320, 625), subtext, font=_font(36), fill=(220, 224, 230, 235))

    image.save(destination, "PNG", optimize=True)


def generate_assets(requests: Iterable[Dict[str, Any]], project_id: str,
                    library_dir: Path, niche: str = "general") -> List[Dict[str, Any]]:
    """Generate up to five requested graphics and return editor asset objects."""
    library_dir.mkdir(parents=True, exist_ok=True)
    output: List[Dict[str, Any]] = []
    for raw in list(requests)[:5]:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind", "title_card"))
        if kind not in KINDS:
            kind = "title_card"
        spec = {
            "kind": kind,
            "accent": str(raw.get("accent", "lime")).lower(),
            "text": _clean(raw.get("text"), 48),
            "subtext": _clean(raw.get("subtext"), 90),
        }
        try:
            word_index = max(0, int(raw.get("word_index", 0)))
        except (TypeError, ValueError):
            word_index = 0
        filename = f"generated_{project_id[:8]}_{uuid.uuid4().hex[:8]}.png"
        destination = library_dir / filename
        temporary = library_dir / f".{filename}.{uuid.uuid4().hex}.generating"
        try:
            _render(spec, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        output.append({
            "id": f"gen_{destination.stem}",
            "name": filename,
            "kind": "image",
            "thumbnail": f"/api/library/thumb/{filename}",
            "url": f"/api/library/file/{filename}",
            "video_url": f"file://{destination}",
            "local_path": str(destination),
            "is_custom": True,
            "generated": True,
            "provider": "klipped_generator",
            "source_id": "klipped_generator",
            "sha256": digest,
            "mime_type": "image/png",
            "rights_status": "generated_editorial",
            "license_id": "in-house-generated",
            "provenance": "generated_editorial_graphic",
            "is_evidence": False,
            "generator": GENERATOR_ID,
            "generator_version": GENERATOR_VERSION,
            "niche": niche,
            "word_index": word_index,
            "asset_kind": kind,
            "reason": _clean(raw.get("reason"), 180),
        })
    return output
