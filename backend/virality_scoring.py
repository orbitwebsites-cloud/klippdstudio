"""Deterministic validation and signal scoring for short-form clip candidates."""
from __future__ import annotations

import math
import re
from statistics import fmean, pstdev
from typing import Any, Dict, Iterable, List


FILLERS = {
    "ah", "basically", "erm", "hmm", "like", "literally", "okay", "right",
    "so", "uh", "um", "well", "you know",
}
GENERAL_HOOK_TERMS = {
    "actually", "best", "biggest", "but", "crazy", "didn't", "finally",
    "here's", "how", "imagine", "instead", "mistake", "never", "secret",
    "stop", "this", "until", "wait", "what", "why", "worst",
}
NICHE_TERMS = {
    "gaming": {
        "ace", "boss", "clutch", "destroyed", "insane", "kill", "no way",
        "one shot", "speedrun", "win", "world record",
    },
    "finance": {
        "cash flow", "debt", "invest", "market", "money", "profit", "risk",
        "stock", "tax", "wealth",
    },
    "fitness": {
        "burn", "fat", "gain", "muscle", "personal best", "protein", "rep",
        "strength", "workout",
    },
}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _word_text(word: Dict[str, Any]) -> str:
    return str(word.get("word", "")).strip()


def _word_start(word: Dict[str, Any]) -> float:
    try:
        return float(word.get("start", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _word_end(word: Dict[str, Any]) -> float:
    try:
        return float(word.get("end", _word_start(word)) or _word_start(word))
    except (TypeError, ValueError):
        return _word_start(word)


def _is_boundary(words: List[Dict[str, Any]], index: int) -> bool:
    text = _word_text(words[index])
    if text.endswith((".", "!", "?")):
        return True
    if index + 1 < len(words):
        return _word_start(words[index + 1]) - _word_end(words[index]) >= 0.7
    return True


def repair_candidate_bounds(
    words: List[Dict[str, Any]], start_index: int, end_index: int,
    min_seconds: float = 12.0, max_seconds: float = 75.0,
) -> tuple[int, int] | None:
    """Snap model indices to nearby speech boundaries and enforce sane duration."""
    if not words:
        return None
    start_index = max(0, min(int(start_index), len(words) - 1))
    end_index = max(start_index, min(int(end_index), len(words) - 1))

    for index in range(start_index - 1, max(-1, start_index - 9), -1):
        if _is_boundary(words, index):
            start_index = index + 1
            break

    for index in range(end_index, min(len(words), end_index + 13)):
        end_index = index
        if _is_boundary(words, index):
            break

    start_time = _word_start(words[start_index])
    while end_index + 1 < len(words) and _word_end(words[end_index]) - start_time < min_seconds:
        end_index += 1

    if _word_end(words[end_index]) - start_time > max_seconds:
        target = start_time + max_seconds
        end_index = max(
            start_index,
            max((i for i in range(start_index, end_index + 1) if _word_end(words[i]) <= target), default=start_index),
        )
        for index in range(end_index, max(start_index, end_index - 10), -1):
            if _is_boundary(words, index):
                end_index = index
                break

    clip_duration = _word_end(words[end_index]) - _word_start(words[start_index])
    if clip_duration < 5.0 or clip_duration > max_seconds + 0.5:
        return None
    return start_index, end_index


def _contains_phrase(text: str, phrases: Iterable[str]) -> int:
    return sum(1 for phrase in phrases if re.search(rf"\b{re.escape(phrase)}\b", text))


def _energy_score(audio_frames: List[Dict[str, Any]], start: float, end: float) -> tuple[int, List[str]]:
    valid = [
        frame for frame in audio_frames
        if math.isfinite(float(frame.get("rms_db", float("-inf"))))
    ]
    inside = [frame for frame in valid if start <= float(frame.get("time", -1)) <= end]
    if not valid or not inside:
        return 50, ["Audio energy unavailable; score stayed neutral"]

    global_values = [float(frame["rms_db"]) for frame in valid]
    local_values = [float(frame["rms_db"]) for frame in inside]
    mean = fmean(global_values)
    spread = max(pstdev(global_values), 1.5)
    lift = (fmean(local_values) - mean) / spread
    peak = (max(local_values) - mean) / spread
    score = round(_clamp(50 + lift * 14 + peak * 8))
    signals = []
    if lift >= 0.45:
        signals.append("Sustained energy above the video's baseline")
    if peak >= 1.25:
        signals.append("Contains a strong reaction or volume spike")
    if not signals:
        signals.append("Steady audio energy")
    return score, signals


def score_candidate(
    candidate: Dict[str, Any], words: List[Dict[str, Any]],
    audio_frames: List[Dict[str, Any]] | None = None, niche: str = "general",
) -> Dict[str, Any] | None:
    """Repair and score one candidate using explainable editorial and media signals."""
    try:
        bounds = repair_candidate_bounds(
            words, int(candidate.get("start_word_index", 0)), int(candidate.get("end_word_index", 0)),
        )
    except (TypeError, ValueError):
        return None
    if not bounds:
        return None
    start_index, end_index = bounds
    selected = words[start_index:end_index + 1]
    text = " ".join(_word_text(word) for word in selected).strip()
    lowered = text.lower()
    opening = " ".join(_word_text(word) for word in selected[:14]).lower()
    start = _word_start(words[start_index])
    end = _word_end(words[end_index])
    duration = max(0.1, end - start)

    niche_key = str(niche or "general").lower()
    niche_phrases = NICHE_TERMS.get(niche_key, set())
    hook_hits = _contains_phrase(opening, GENERAL_HOOK_TERMS | niche_phrases)
    hook_score = 48 + min(hook_hits, 4) * 11
    if "?" in opening or "!" in opening:
        hook_score += 8
    if any(opening.startswith(filler) for filler in FILLERS):
        hook_score -= 18
    hook_score = round(_clamp(hook_score))

    word_count = max(1, len(selected))
    words_per_minute = word_count / duration * 60
    pacing_score = round(_clamp(100 - abs(words_per_minute - 175) * 0.65))
    filler_hits = sum(1 for word in selected if _word_text(word).lower().strip(".,!?") in FILLERS)
    clarity_score = round(_clamp(100 - (filler_hits / word_count) * 360))
    energy_score, energy_signals = _energy_score(audio_frames or [], start, end)
    try:
        editorial_score = round(_clamp(float(candidate.get("editorial_score", candidate.get("score", 50)))))
    except (TypeError, ValueError):
        editorial_score = 50

    final_score = round(
        editorial_score * 0.35 + hook_score * 0.25 + energy_score * 0.20
        + pacing_score * 0.10 + clarity_score * 0.10
    )
    signals = list(energy_signals)
    if hook_hits:
        signals.append(f"Opening contains {hook_hits} strong hook signal{'s' if hook_hits != 1 else ''}")
    if niche_phrases and _contains_phrase(lowered, niche_phrases):
        signals.append(f"Matches {niche_key} audience language")
    if pacing_score >= 80:
        signals.append(f"Short-form pacing at {round(words_per_minute)} words/min")
    if clarity_score >= 85:
        signals.append("Low filler density")

    hook_options = candidate.get("hook_options") if isinstance(candidate.get("hook_options"), list) else []
    caption_options = candidate.get("caption_options") if isinstance(candidate.get("caption_options"), list) else []
    hook = str(candidate.get("hook", "")).strip()[:200]
    caption = str(candidate.get("caption", "")).strip()[:200]
    if hook and hook not in hook_options:
        hook_options.insert(0, hook)
    if caption and caption not in caption_options:
        caption_options.insert(0, caption)

    return {
        "start_word_index": start_index,
        "end_word_index": end_index,
        "start": round(start, 2),
        "end": round(end, 2),
        "duration": round(duration, 2),
        "hook": hook or " ".join(_word_text(word) for word in selected[:12]),
        "caption": caption,
        "hook_options": [str(value)[:200] for value in hook_options[:3] if str(value).strip()],
        "caption_options": [str(value)[:200] for value in caption_options[:3] if str(value).strip()],
        "score": final_score,
        "score_label": "Breakout" if final_score >= 85 else "Strong" if final_score >= 70 else "Promising" if final_score >= 55 else "Needs work",
        "score_breakdown": {
            "editorial": editorial_score,
            "hook": hook_score,
            "energy": energy_score,
            "pacing": pacing_score,
            "clarity": clarity_score,
        },
        "score_signals": signals[:5],
        "reason": str(candidate.get("reason", ""))[:300],
    }


def _overlap_ratio(first: Dict[str, Any], second: Dict[str, Any]) -> float:
    overlap = max(0.0, min(float(first["end"]), float(second["end"])) - max(float(first["start"]), float(second["start"])))
    shorter = min(float(first["duration"]), float(second["duration"]))
    return overlap / shorter if shorter > 0 else 0.0


def rank_candidates(
    candidates: List[Dict[str, Any]], words: List[Dict[str, Any]],
    audio_frames: List[Dict[str, Any]] | None = None, niche: str = "general", limit: int = 5,
) -> List[Dict[str, Any]]:
    scored = [score_candidate(candidate, words, audio_frames, niche) for candidate in candidates]
    ranked = sorted((item for item in scored if item), key=lambda item: item["score"], reverse=True)
    selected: List[Dict[str, Any]] = []
    for candidate in ranked:
        if any(_overlap_ratio(candidate, existing) >= 0.65 for existing in selected):
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def fallback_candidates(words: List[Dict[str, Any]], duration: float, limit: int = 8) -> List[Dict[str, Any]]:
    """Build deterministic windows when an LLM is unavailable or returns bad JSON."""
    if not words:
        return []
    anchors = []
    all_phrases = GENERAL_HOOK_TERMS | set().union(*NICHE_TERMS.values())
    for index, word in enumerate(words):
        token = _word_text(word).lower().strip(".,!?")
        if token in all_phrases or _word_text(word).endswith(("!", "?")):
            anchors.append(index)
    if not anchors:
        step = max(1, len(words) // max(1, min(limit, 5)))
        anchors = list(range(0, len(words), step))

    candidates = []
    for anchor in anchors[:limit * 2]:
        start_time = _word_start(words[anchor])
        end_index = anchor
        while end_index + 1 < len(words) and _word_end(words[end_index]) < start_time + 38:
            end_index += 1
        candidates.append({
            "start_word_index": anchor,
            "end_word_index": end_index,
            "editorial_score": 50,
            "hook": " ".join(_word_text(word) for word in words[anchor:anchor + 12]),
            "caption": "",
            "reason": "Selected from transcript and pacing signals",
        })
    return candidates
