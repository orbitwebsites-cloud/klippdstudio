from pathlib import Path

from PIL import Image

import asset_generator
import video_processor as vp


def test_generated_asset_is_transparent_renderable_png(tmp_path):
    assets = asset_generator.generate_assets(
        [{
            "word_index": 4,
            "kind": "title_card",
            "text": "The Final Fight",
            "subtext": "One life left",
            "accent": "gold",
            "reason": "Marks the payoff",
        }],
        "project-123",
        tmp_path,
    )

    assert len(assets) == 1
    asset = assets[0]
    assert asset["generated"] is True
    assert asset["word_index"] == 4
    path = Path(asset["local_path"])
    assert path.is_file()
    with Image.open(path) as image:
        assert image.mode == "RGBA"
        assert image.size == (1920, 1080)
        assert image.getpixel((0, 0))[3] == 0


def test_keep_segments_remove_only_selected_words():
    words = [
        {"word": "hello", "start": 0.0, "end": 0.5},
        {"word": "um", "start": 0.5, "end": 0.7},
        {"word": "world", "start": 0.7, "end": 1.2},
    ]
    keep = vp.build_keep_segments(words, [1], 1.2, pad=0)
    assert keep == [{"start": 0.0, "end": 0.5}, {"start": 0.7, "end": 1.2}]


def test_landscape_output_dimensions_are_encoder_safe():
    assert vp.aspect_target_size("16:9", 641, 360) == (640, 360)
    width, height = vp.aspect_target_size("16:9", 853, 480)
    assert width % 2 == 0
    assert height % 2 == 0


def test_ass_captions_remap_after_removed_segment(tmp_path):
    output = tmp_path / "captions.ass"
    words = [
        {"word": "first", "start": 0.0, "end": 0.4},
        {"word": "second", "start": 1.0, "end": 1.4},
    ]
    vp.generate_ass(
        words,
        str(output),
        "luxury",
        1920,
        1080,
        {1},
        [{"start": 0.0, "end": 0.5}, {"start": 1.0, "end": 1.5}],
    )
    content = output.read_text(encoding="utf-8")
    assert "first" in content
    assert "second" in content
    assert "Dialogue: 0,0:00:00.00,0:00:00.90,Emph" in content
    assert "first second" in content
