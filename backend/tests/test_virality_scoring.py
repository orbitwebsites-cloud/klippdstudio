import asyncio
from unittest.mock import patch

import ai_services
import virality_scoring as scoring


def _words(text: str, step: float = 0.5):
    return [
        {"word": token, "start": index * step, "end": (index + 1) * step}
        for index, token in enumerate(text.split())
    ]


def test_score_candidate_blends_explainable_signals():
    words = _words(
        "Wait this is the craziest clutch I have ever seen and nobody thought the final boss could be beaten "
        "but we actually won the entire match with one shot and broke the world record today!"
    )
    frames = [
        {"time": index * 0.5, "rms_db": -30.0 if index < 3 else -12.0, "peak_db": -5.0}
        for index in range(35)
    ]
    clip = scoring.score_candidate({
        "start_word_index": 0,
        "end_word_index": len(words) - 1,
        "editorial_score": 86,
        "hook": "Wait for the final one-shot clutch",
        "caption": "Nobody expected this finish",
        "reason": "Strong setup and payoff",
    }, words, frames, niche="gaming")

    assert clip is not None
    assert 0 <= clip["score"] <= 100
    assert set(clip["score_breakdown"]) == {"editorial", "hook", "energy", "pacing", "clarity"}
    assert any("gaming" in signal.lower() for signal in clip["score_signals"])


def test_rank_candidates_repairs_bounds_and_removes_heavy_overlap():
    words = _words(" ".join(["moment"] * 180))
    candidates = [
        {"start_word_index": 0, "end_word_index": 80, "editorial_score": 90},
        {"start_word_index": 5, "end_word_index": 82, "editorial_score": 85},
        {"start_word_index": 95, "end_word_index": 170, "editorial_score": 80},
    ]

    ranked = scoring.rank_candidates(candidates, words, limit=5)

    assert len(ranked) == 2
    assert ranked[0]["duration"] <= 75
    assert ranked[0]["start_word_index"] == 0


def test_fallback_candidates_work_without_an_llm():
    words = _words("ordinary setup words then wait this is insane no way we actually won " * 8)
    candidates = scoring.fallback_candidates(words, duration=60)

    assert candidates
    assert all(candidate["end_word_index"] > candidate["start_word_index"] for candidate in candidates)


def test_clip_extraction_falls_back_when_provider_is_unavailable():
    words = _words("wait this was an insane finish and nobody expected us to win the final round " * 8)
    with patch("ai_services.call_text_llm", side_effect=RuntimeError("provider unavailable")):
        clips = asyncio.run(ai_services.extract_viral_clips(words, {}, duration=60, niche="gaming"))

    assert clips
    assert all("score_breakdown" in clip for clip in clips)
