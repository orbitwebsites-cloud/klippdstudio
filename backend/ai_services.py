"""AI service integrations for transcription and edit planning."""
import json
import logging
import re
import httpx
from openai import AsyncOpenAI
from typing import List, Dict, Any, Optional
from editing_profiles import profile_prompt
from editing_intelligence import (
    build_revision_prompt,
    context_as_prompt,
    quality_gate_edit_plan,
    retrieve_editing_context,
)

logger = logging.getLogger(__name__)


# ---------- MODEL CONFIG ----------
GROQ_BASE = "https://api.groq.com/openai/v1"
CEREBRAS_BASE = "https://api.cerebras.ai/v1"

# Best models per task (Jan 2026)
WHISPER_MODEL = "whisper-large-v3-turbo"
GROQ_TEXT_MODEL = "llama-3.3-70b-versatile"
GROQ_TEXT_MODEL_FALLBACK = "llama-3.1-8b-instant"
CEREBRAS_TEXT_MODEL = "gpt-oss-120b"
CEREBRAS_TEXT_MODEL_FALLBACK = "zai-glm-4.7"


# ---------- TRANSCRIPTION ----------
async def transcribe_audio(audio_path: str, groq_key: str) -> Dict[str, Any]:
    """Transcribe audio using Groq Whisper with word-level timestamps."""
    if not groq_key:
        raise RuntimeError("Groq API key required for transcription.")

    client = AsyncOpenAI(api_key=groq_key, base_url=GROQ_BASE, timeout=180.0)
    with open(audio_path, "rb") as f:
        resp = await client.audio.transcriptions.create(
            file=(audio_path.split("/")[-1], f, "audio/mpeg"),
            model=WHISPER_MODEL,
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
        )
    data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    return {
        "text": data.get("text", ""),
        "words": data.get("words", []) or [],
        "segments": data.get("segments", []) or [],
        "duration": data.get("duration", 0),
        "language": data.get("language", "en"),
    }


# ---------- TEXT LLM WITH FALLBACK ----------
async def _call_openai_compat(base_url: str, api_key: str, model: str,
                              messages: list, response_format: Optional[dict] = None) -> str:
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=90.0)
    kwargs = {"model": model, "messages": messages, "temperature": 0.2}
    if response_format:
        kwargs["response_format"] = response_format
    res = await client.chat.completions.create(**kwargs)
    return res.choices[0].message.content or ""


async def call_text_llm(prompt: str, keys: dict, system: str = "",
                        want_json: bool = False) -> str:
    """Try Groq primary → Groq fallback → Cerebras primary → Cerebras fallback."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response_format = {"type": "json_object"} if want_json else None

    chain = [
        ("groq", GROQ_BASE, keys.get("groq"), GROQ_TEXT_MODEL),
        ("groq", GROQ_BASE, keys.get("groq"), GROQ_TEXT_MODEL_FALLBACK),
        ("cerebras", CEREBRAS_BASE, keys.get("cerebras"), CEREBRAS_TEXT_MODEL),
        ("cerebras", CEREBRAS_BASE, keys.get("cerebras"), CEREBRAS_TEXT_MODEL_FALLBACK),
    ]

    last_err = None
    for provider, base, key, model in chain:
        if not key:
            continue
        try:
            logger.info(f"LLM call → {provider}/{model}")
            out = await _call_openai_compat(base, key, model, messages, response_format)
            if out:
                return out
        except Exception as e:
            last_err = e
            logger.warning(f"{provider}/{model} failed: {e}")
            continue

    raise RuntimeError(f"All LLM providers failed. Last error: {last_err}")


def _extract_json(text: str) -> Any:
    """Extract JSON from an LLM response that may have code fences or extra text."""
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    for pattern in [r"(\{[\s\S]*\})", r"(\[[\s\S]*\])"]:
        m = re.search(pattern, text)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                continue
    raise ValueError(f"Could not parse JSON from response: {text[:200]}")


# ---------- ANALYSIS ----------
def _normalize_edit_plan(parsed: Any, max_words: int) -> Dict[str, Any]:
    """Convert an untrusted model response into the bounded runtime schema."""
    parsed = parsed if isinstance(parsed, dict) else {}

    def clean_timed(items, allowed_type=None, limit=12):
        cleaned = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("word_index", 0))
            except (TypeError, ValueError):
                continue
            if index < 0 or index >= max_words:
                continue
            row = {"word_index": index}
            if allowed_type:
                value = str(item.get(allowed_type[0], ""))
                if value not in allowed_type[1]:
                    continue
                row[allowed_type[0]] = value
            row["reason" if "reason" in item else "intent"] = str(
                item.get("reason", item.get("intent", "")))[:200]
            cleaned.append(row)
        return cleaned[:limit]

    broll = []
    for moment in parsed.get("broll_moments", []):
        if not isinstance(moment, dict):
            continue
        try:
            index = int(moment.get("word_index", 0))
        except (TypeError, ValueError):
            continue
        if 0 <= index < max_words:
            broll.append({
                "word_index": index,
                "query": str(moment.get("query", ""))[:80],
                "reason": str(moment.get("reason", ""))[:200],
                "visual_intent": str(moment.get("visual_intent", ""))[:240],
            })

    asset_requests = []
    allowed_kinds = {"title_card", "stat_card", "player_label", "item_callout", "quote_card"}
    allowed_accents = {"gold", "lime", "red", "cyan", "purple", "white"}
    for item in parsed.get("asset_requests", []):
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("word_index", 0))
        except (TypeError, ValueError):
            continue
        kind = str(item.get("kind", ""))
        if not 0 <= index < max_words or kind not in allowed_kinds:
            continue
        accent = str(item.get("accent", "lime")).lower()
        asset_requests.append({
            "word_index": index,
            "kind": kind,
            "text": str(item.get("text", ""))[:48],
            "subtext": str(item.get("subtext", ""))[:90],
            "accent": accent if accent in allowed_accents else "lime",
            "reason": str(item.get("reason", ""))[:180],
        })

    def clean_indices(key: str) -> List[int]:
        values = parsed.get(key, []) if isinstance(parsed.get(key), list) else []
        return [
            int(value) for value in values
            if isinstance(value, (int, str)) and str(value).lstrip("-").isdigit()
        ]

    return {
        "filler_indices": clean_indices("filler_indices"),
        "emphasis_indices": clean_indices("emphasis_indices"),
        "broll_moments": broll[:8],
        "story_beats": clean_timed(parsed.get("story_beats", []), ("beat_type", {"hook", "setup", "escalation", "setback", "reveal", "payoff", "cta"})),
        "transitions": clean_timed(parsed.get("transitions", []), ("type", {"hard_cut", "match_cut", "push", "whip", "dip"})),
        "audio_cues": clean_timed(parsed.get("audio_cues", []), ("type", {"impact", "whoosh", "riser", "silence", "glitch"})),
        "asset_requests": asset_requests[:5],
        "pacing_summary": str(parsed.get("pacing_summary", ""))[:400],
        "title": str(parsed.get("title", "Untitled Clip"))[:120],
        "summary": str(parsed.get("summary", ""))[:400],
    }


def _quality_rank(plan: Dict[str, Any]) -> tuple[int, int, int]:
    review = plan.get("quality_review", {})
    issues = review.get("remaining_issues", []) if isinstance(review, dict) else []
    critical = sum(
        1 for issue in issues
        if isinstance(issue, dict) and issue.get("severity") == "critical"
    )
    return (int(bool(review.get("passed"))), -critical, int(review.get("score", 0)))


async def analyze_transcript(
    words: List[Dict], keys: dict, profile: str | None = None,
    training_context: str = "",
) -> Dict[str, Any]:
    """Build a complete, transcript-grounded editing blueprint."""
    max_words = min(len(words), 1200)
    lines = []
    for i, w in enumerate(words[:max_words]):
        txt = (w.get("word") or "").strip()
        if not txt:
            continue
        lines.append(f"{i}:{txt}")
    numbered = " ".join(lines)

    system = (
        "You are an expert video editor AI. You analyze podcast/vlog transcripts to "
        "produce editing decisions. You reply ONLY with valid JSON, no prose."
    )
    profile_rules = profile_prompt(profile)
    retrieved_context = retrieve_editing_context(words[:max_words], profile)
    knowledge_rules = context_as_prompt(retrieved_context)

    prompt = f"""Analyze this transcript. Each token is formatted `index:word`.

TRANSCRIPT:
{numbered}

Return a JSON object with these keys (use word indices from the transcript):
{{
  "filler_indices": [array of word indices that should be CUT - fillers like 'um','uh','ah','like','you know','so','basically','literally','right','okay' when used as fillers, stutters (repeated words), false-starts],
  "emphasis_indices": [array of word indices that should be visually emphasized (zoom-in/pop) - key words in punchlines, strong statements, hooks],
  "broll_moments": [
    {{"word_index": <int>, "query": "<2-4 word visual search query>", "reason": "<brief>", "visual_intent": "<what the viewer should understand or feel>"}}
  ],
  "story_beats": [
    {{"word_index": <int>, "beat_type": "hook|setup|escalation|setback|reveal|payoff|cta", "intent": "<brief>"}}
  ],
  "transitions": [
    {{"word_index": <int>, "type": "hard_cut|match_cut|push|whip|dip", "reason": "<brief>"}}
  ],
  "audio_cues": [
    {{"word_index": <int>, "type": "impact|whoosh|riser|silence|glitch", "reason": "<brief>"}}
  ],
  "asset_requests": [
    {{"word_index": <int>, "kind": "title_card|stat_card|player_label|item_callout|quote_card", "text": "<max 48 chars>", "subtext": "<max 90 chars>", "accent": "gold|lime|red|cyan|purple|white", "reason": "<why a graphic is better than unrelated footage>"}}
  ],
  "pacing_summary": "<one sentence describing rhythm and where it changes>",
  "title": "<catchy 3-8 word title for this clip>",
  "summary": "<1-sentence summary>"
}}

Rules:
- Be strict on fillers - only flag actual fillers, not meaningful words.
- Emphasis: 5-15 words max, spread evenly.
- First understand the story: hook, setup, escalation, setback/reveal and payoff.
- Every cut, transition, visual and sound cue must support comprehension, emotion, rhythm or continuity.
- Prefer hard cuts. Use stylized transitions only when they connect two ideas or mark a real beat change.
- Use silence before a reveal/impact when it improves contrast; do not add SFX to every cut.
- B-roll: 3-8 moments max. State the visual intent, not just a keyword.
- Asset requests: 0-5. Request an honest editorial graphic only when library/stock footage is unlikely to explain the moment well.
- Asset-request word indices should match a B-roll moment so the graphic appears as a candidate there.
- Never fabricate gameplay, results, screenshots, people, quotes or evidence.
- Return ONLY the JSON. No markdown. No commentary.
{profile_rules}

USER-APPROVED EDITING PROFILE:
{training_context[:5000] if training_context else "None selected. Use only the retrieved editing knowledge above."}

Treat this profile as general editorial guidance, not a request to imitate a creator or reproduce a reference sequence. Apply it only when supported by this video's transcript and footage.
{knowledge_rules}
"""
    raw = await call_text_llm(prompt, keys, system=system, want_json=True)
    parsed = _extract_json(raw)
    first_plan = quality_gate_edit_plan(
        _normalize_edit_plan(parsed, max_words),
        words[:max_words],
        requested_profile=retrieved_context["profile"],
    )
    candidates = [(1, first_plan)]
    attempt_count = 1
    first_review = first_plan.get("quality_review", {})
    remaining = first_review.get("remaining_issues", [])
    has_critical = any(
        isinstance(issue, dict) and issue.get("severity") == "critical"
        for issue in remaining
    )

    # One bounded semantic retry. It is intentionally skipped for a passing
    # plan, and any failed/unsafe retry loses to the safer first candidate.
    if not first_review.get("passed") or has_critical:
        revision_prompt = build_revision_prompt(
            first_plan,
            {"issues": remaining},
            context=retrieved_context,
            numbered_transcript=numbered,
        )
        try:
            attempt_count = 2
            revised_raw = await call_text_llm(
                revision_prompt,
                keys,
                system=system,
                want_json=True,
            )
            revised_plan = quality_gate_edit_plan(
                _normalize_edit_plan(_extract_json(revised_raw), max_words),
                words[:max_words],
                requested_profile=retrieved_context["profile"],
            )
            candidates.append((2, revised_plan))
        except Exception as exc:
            logger.warning("Semantic edit-plan revision failed; keeping first safe plan: %s", exc)

    selected_attempt, selected = max(candidates, key=lambda candidate: _quality_rank(candidate[1]))
    candidate_scores = [
        {
            "attempt": number,
            "score": int(plan.get("quality_review", {}).get("score", 0)),
            "passed": bool(plan.get("quality_review", {}).get("passed")),
            "critical_count": sum(
                1 for issue in plan.get("quality_review", {}).get("remaining_issues", [])
                if isinstance(issue, dict) and issue.get("severity") == "critical"
            ),
        }
        for number, plan in candidates
    ]
    selected_review = selected.setdefault("quality_review", {})
    selected_review.update({
        "llm_attempt_count": attempt_count,
        "revision_attempted": attempt_count == 2,
        "selected_attempt": selected_attempt,
        "candidate_scores": candidate_scores,
        "final_score": int(selected_review.get("score", 0)),
    })
    return selected


# ---------- VIRAL CLIP EXTRACTION ----------
async def extract_viral_clips(words: List[Dict], keys: dict, duration: float) -> List[Dict[str, Any]]:
    """Ask LLM to find the 3-5 punchiest self-contained short-clip moments."""
    if not words:
        return []

    max_words = min(len(words), 1500)
    lines = []
    for i, w in enumerate(words[:max_words]):
        txt = (w.get("word") or "").strip()
        if not txt:
            continue
        lines.append(f"{i}[{float(w.get('start',0)):.1f}s]:{txt}")
    numbered = " ".join(lines)

    system = (
        "You are a viral short-form video expert (TikTok/Reels/Shorts). "
        "You identify the most captivating, self-contained ~20-45 second moments "
        "from a longer transcript. You reply ONLY with valid JSON."
    )

    prompt = f"""Analyze this transcript. Each token is `index[start_seconds]:word`.

TRANSCRIPT:
{numbered}

Return JSON:
{{
  "clips": [
    {{
      "start_word_index": <int>,
      "end_word_index": <int>,
      "hook": "<the punchy opening line, max 12 words>",
      "caption": "<viral-style caption with 2-3 emojis, max 100 chars>",
      "score": <int 1-100, higher = more viral>,
      "reason": "<why this works, max 20 words>"
    }}
  ]
}}

Rules:
- 3 to 5 clips total, ranked best first.
- Each clip should be 20-60 seconds long (based on word timestamps).
- Prefer moments with strong hooks, controversy, humor, insight, or emotion.
- Skip filler-heavy sections.
- Return ONLY the JSON.
"""
    raw = await call_text_llm(prompt, keys, system=system, want_json=True)
    parsed = _extract_json(raw)
    clips_in = parsed.get("clips", []) if isinstance(parsed, dict) else []

    results = []
    for c in clips_in[:5]:
        if not isinstance(c, dict):
            continue
        try:
            si = int(c.get("start_word_index", 0))
            ei = int(c.get("end_word_index", 0))
            if si < 0 or ei <= si or si >= len(words) or ei >= len(words):
                continue
            start = float(words[si].get("start", 0))
            end = float(words[ei].get("end", start + 30))
            if end - start < 5 or end - start > 120:
                continue
            results.append({
                "start_word_index": si,
                "end_word_index": ei,
                "start": round(start, 2),
                "end": round(end, 2),
                "duration": round(end - start, 2),
                "hook": str(c.get("hook", ""))[:200],
                "caption": str(c.get("caption", ""))[:200],
                "score": int(c.get("score", 50)),
                "reason": str(c.get("reason", ""))[:300],
            })
        except (ValueError, TypeError):
            continue
    return results


# ---------- CONNECTION TESTS ----------
async def test_groq(api_key: str) -> Dict[str, Any]:
    if not api_key:
        return {"ok": False, "error": "No key"}
    try:
        client = AsyncOpenAI(api_key=api_key, base_url=GROQ_BASE, timeout=20)
        r = await client.chat.completions.create(
            model=GROQ_TEXT_MODEL_FALLBACK,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        return {"ok": True, "model": r.model}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
async def test_cerebras(api_key: str) -> Dict[str, Any]:
    if not api_key:
        return {"ok": False, "error": "No key"}
    try:
        client = AsyncOpenAI(api_key=api_key, base_url=CEREBRAS_BASE, timeout=20)
        r = await client.chat.completions.create(
            model=CEREBRAS_TEXT_MODEL_FALLBACK,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        return {"ok": True, "model": r.model}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
# End of provider connection checks.
