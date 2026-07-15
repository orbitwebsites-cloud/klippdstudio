"""Retrieval and deterministic quality gates for editing plans.

This module is deliberately model-agnostic. Public creator links are research
pointers, not weights or copied timelines. The runtime retrieves original
editing principles, evaluates a proposed plan, and safely repairs mechanical
problems without inventing story facts or unavailable footage.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


DEFAULT_KNOWLEDGE_PATH = Path(
    os.environ.get(
        "EDITING_KNOWLEDGE_PATH",
        str(Path(__file__).resolve().parents[1] / "training" / "editing_knowledge_v1.json"),
    )
)
QUALITY_THRESHOLD = 82
GAMING_QUALITY_THRESHOLD = 90
_UNSAFE_GENERATION_TERMS = re.compile(
    r"\b(fake|fabricat(?:e|ed)|screenshot|gameplay footage|photo of|logo|official ui|proof of|evidence of)\b",
    re.IGNORECASE,
)


def _validate_knowledge_pack(data: Dict[str, Any], source: Path, *, module: bool = False) -> None:
    expected = "klippd.editing_knowledge.module.v1" if module else "klippd.editing_knowledge.v1"
    if data.get("schema_version") != expected:
        raise ValueError(f"Unsupported editing knowledge schema in {source}: {data.get('schema_version')!r}")
    if not isinstance(data.get("rules"), list) or not data["rules"]:
        raise ValueError(f"Editing knowledge contains no rules: {source}")
    seen = set()
    for rule in data["rules"]:
        if not isinstance(rule, dict):
            raise ValueError(f"Editing rule must be an object: {source}")
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id.strip() or rule_id in seen:
            raise ValueError(f"Invalid or duplicate editing rule id {rule_id!r}: {source}")
        seen.add(rule_id)
        if not isinstance(rule.get("tags"), list) or not all(isinstance(tag, str) and tag for tag in rule["tags"]):
            raise ValueError(f"Editing rule {rule_id!r} has invalid tags: {source}")
        if not isinstance(rule.get("rule"), str) or not rule["rule"].strip():
            raise ValueError(f"Editing rule {rule_id!r} has no actionable rule: {source}")
        if not isinstance(rule.get("rationale"), str) or not rule["rationale"].strip():
            raise ValueError(f"Editing rule {rule_id!r} has no rationale: {source}")
        if not isinstance(rule.get("guardrail"), str) or not rule["guardrail"].strip():
            raise ValueError(f"Editing rule {rule_id!r} has no guardrail: {source}")
        weight = rule.get("weight")
        if not isinstance(weight, int) or isinstance(weight, bool) or not 1 <= weight <= 10:
            raise ValueError(f"Editing rule {rule_id!r} has weight outside 1..10: {source}")


def load_knowledge(path: str | Path | None = None) -> Dict[str, Any]:
    knowledge_path = Path(path) if path else DEFAULT_KNOWLEDGE_PATH
    with knowledge_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    _validate_knowledge_pack(data, knowledge_path)

    base_id = str(data.get("knowledge_id", knowledge_path.stem))
    base_version = str(data.get("version", "1"))
    modules = [{"id": base_id, "version": base_version, "schema_version": data["schema_version"]}]
    for rule in data["rules"]:
        rule["_module_id"] = base_id

    module_dir = Path(os.environ.get("EDITING_KNOWLEDGE_DIR", str(knowledge_path.parent / "niches")))
    known_rule_ids = {rule["id"] for rule in data["rules"]}
    known_profiles = set(data.get("profiles", {}))
    if module_dir.is_dir():
        for module_path in sorted(module_dir.glob("*.json"), key=lambda item: item.name.lower()):
            with module_path.open("r", encoding="utf-8") as handle:
                module_data = json.load(handle)
            _validate_knowledge_pack(module_data, module_path, module=True)
            module_id = module_data.get("module_id")
            module_version = module_data.get("version")
            if not isinstance(module_id, str) or not module_id.strip() or not isinstance(module_version, str) or not module_version.strip():
                raise ValueError(f"Knowledge module needs stable module_id and version: {module_path}")
            if any(item["id"] == module_id for item in modules):
                raise ValueError(f"Duplicate knowledge module id {module_id!r}: {module_path}")
            module_profiles = module_data.get("profiles", {})
            if not isinstance(module_profiles, dict):
                raise ValueError(f"Knowledge module profiles must be an object: {module_path}")
            conflicts = known_profiles & set(module_profiles)
            if conflicts:
                raise ValueError(f"Knowledge module profile conflicts {sorted(conflicts)}: {module_path}")
            for rule in module_data["rules"]:
                if rule["id"] in known_rule_ids:
                    raise ValueError(f"Duplicate editing rule id {rule['id']!r}: {module_path}")
                rule["_module_id"] = module_id
                known_rule_ids.add(rule["id"])
                data["rules"].append(rule)
            data.setdefault("profiles", {}).update(module_profiles)
            known_profiles.update(module_profiles)
            modules.append({
                "id": module_id,
                "version": module_version,
                "schema_version": module_data["schema_version"],
                "evidence_status": str(module_data.get("evidence_status", "unspecified")),
            })
    data["_knowledge_modules"] = modules
    return data


def _transcript_text(words: Sequence[Dict[str, Any]]) -> str:
    return " ".join(str(word.get("word", "")) for word in words).lower()


def infer_profile(
    words: Sequence[Dict[str, Any]],
    requested_profile: str | None = None,
    knowledge: Dict[str, Any] | None = None,
) -> str:
    knowledge = knowledge or load_knowledge()
    profiles = knowledge.get("profiles", {})
    if requested_profile in profiles:
        return str(requested_profile)

    transcript = _transcript_text(words)
    scores: Dict[str, int] = {}
    for name, config in profiles.items():
        if name == "general":
            continue
        scores[name] = sum(
            1 for alias in config.get("aliases", [])
            if str(alias).lower() in transcript
        )
    if scores and max(scores.values()) > 0:
        return max(scores, key=scores.get)
    return "general"


def retrieve_editing_context(
    words: Sequence[Dict[str, Any]],
    requested_profile: str | None = None,
    max_rules: int = 14,
    knowledge: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Retrieve a compact, traceable rule set for the current transcript."""
    knowledge = knowledge or load_knowledge()
    profile = infer_profile(words, requested_profile, knowledge)
    transcript = _transcript_text(words)
    query_tags = {profile, "all", "story", "qa"}
    keyword_tags = {
        "caption": "captions", "subtitle": "captions", "sound": "audio",
        "music": "audio", "logo": "logo", "asset": "assets",
        "transition": "transitions", "proof": "evidence",
    }
    for keyword, tag in keyword_tags.items():
        if keyword in transcript:
            query_tags.add(tag)

    ranked = []
    profile_names = set(knowledge.get("profiles", {})) - {"general"}
    for rule in knowledge.get("rules", []):
        tags = {str(tag) for tag in rule.get("tags", [])}
        niche_tags = tags & profile_names
        if niche_tags and profile not in niche_tags:
            continue
        relevance = len(tags & query_tags)
        if not relevance:
            continue
        profile_bonus = 5 if profile in tags else 0
        score = int(rule.get("weight", 1)) + relevance * 2 + profile_bonus
        ranked.append((score, str(rule.get("id", "")), rule))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = [copy.deepcopy(item[2]) for item in ranked[:max(1, max_rules)]]
    selected_module_ids = {str(rule.get("_module_id", "")) for rule in selected}
    for rule in selected:
        rule.pop("_module_id", None)
    profile_config = copy.deepcopy(knowledge.get("profiles", {}).get(profile, {}))
    return {
        "schema_version": "klippd.retrieval_context.v1",
        "profile": profile,
        "goal": profile_config.get("goal", ""),
        "default_arc": profile_config.get("default_arc", []),
        "rules": selected,
        "rule_ids": [rule.get("id") for rule in selected],
        "provenance_policy": copy.deepcopy(knowledge.get("provenance_policy", {})),
        "knowledge_modules": [
            copy.deepcopy(module) for module in knowledge.get("_knowledge_modules", [])
            if module.get("id") in selected_module_ids
        ],
    }


def context_as_prompt(context: Dict[str, Any]) -> str:
    """Render retrieved knowledge into a small prompt section with rule IDs."""
    rows = [
        "RETRIEVED EDITING KNOWLEDGE:",
        f"- Profile: {context.get('profile', 'general')}",
        f"- Goal: {context.get('goal', '')}",
        f"- Expected story arc when supported: {', '.join(context.get('default_arc', []))}",
    ]
    for rule in context.get("rules", []):
        rows.append(
            f"- [{rule.get('id')}] {rule.get('rule')} Guardrail: {rule.get('guardrail')}"
        )
    rows.extend([
        "Use only rules supported by this transcript. Do not invent missing beats.",
        "Generated graphics are explanatory editorial assets, never evidence or fake gameplay.",
    ])
    return "\n".join(rows)


def _valid_index(value: Any, word_count: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < word_count


def _event_indices(plan: Dict[str, Any], key: str, word_count: int) -> List[int]:
    result = []
    for event in plan.get(key, []) if isinstance(plan.get(key), list) else []:
        if isinstance(event, dict) and _valid_index(event.get("word_index"), word_count):
            result.append(event["word_index"])
    return result


def evaluate_edit_plan(
    plan: Dict[str, Any],
    words: Sequence[Dict[str, Any]],
    profile: str = "general",
) -> Dict[str, Any]:
    """Score grounding, story structure, intent, restraint, and asset honesty."""
    word_count = len(words)
    issues: List[Dict[str, str]] = []
    score = 100

    def issue(code: str, severity: str, message: str, deduction: int) -> None:
        nonlocal score
        issues.append({"code": code, "severity": severity, "message": message})
        score -= deduction

    beats = plan.get("story_beats", []) if isinstance(plan.get("story_beats"), list) else []
    beat_types = [event.get("beat_type") for event in beats if isinstance(event, dict)]
    beat_positions = [event.get("word_index") for event in beats if isinstance(event, dict)]
    if word_count >= 20 and "hook" not in beat_types:
        issue("missing_hook", "warning", "No transcript-grounded hook was identified.", 8)
    if "hook" in beat_types and not ({"payoff", "reveal"} & set(beat_types)):
        issue("unresolved_hook", "critical", "The hook has no supported reveal or payoff.", 14)
    valid_positions = [p for p in beat_positions if _valid_index(p, word_count)]
    if valid_positions != sorted(valid_positions):
        issue("beat_order", "critical", "Story beats are not in transcript order.", 12)

    for key in ("story_beats", "transitions", "audio_cues", "broll_moments", "asset_requests"):
        events = plan.get(key, []) if isinstance(plan.get(key), list) else []
        bad = sum(
            1 for event in events
            if not isinstance(event, dict) or not _valid_index(event.get("word_index"), word_count)
        )
        if bad:
            issue(f"invalid_{key}_indices", "critical", f"{bad} {key} entries are outside the transcript.", min(15, bad * 4))

    broll = plan.get("broll_moments", []) if isinstance(plan.get("broll_moments"), list) else []
    missing_intent = sum(
        1 for event in broll
        if isinstance(event, dict) and len(str(event.get("visual_intent", "")).strip()) < 8
    )
    if missing_intent:
        issue("missing_visual_intent", "warning", f"{missing_intent} B-roll moments lack a useful visual intent.", min(12, missing_intent * 3))

    broll_indices = set(_event_indices(plan, "broll_moments", word_count))
    unsafe_assets = 0
    unanchored_assets = 0
    for asset in plan.get("asset_requests", []) if isinstance(plan.get("asset_requests"), list) else []:
        if not isinstance(asset, dict):
            continue
        combined = " ".join(str(asset.get(key, "")) for key in ("text", "subtext", "reason"))
        if _UNSAFE_GENERATION_TERMS.search(combined):
            unsafe_assets += 1
        if _valid_index(asset.get("word_index"), word_count) and asset["word_index"] not in broll_indices:
            unanchored_assets += 1
    if unsafe_assets:
        issue("unsafe_generated_asset", "critical", f"{unsafe_assets} generated requests could impersonate evidence or protected artwork.", 20)
    if unanchored_assets:
        issue("unanchored_generated_asset", "warning", f"{unanchored_assets} generated requests are not anchored to a visual moment.", min(10, unanchored_assets * 3))

    emphasis = {
        idx for idx in plan.get("emphasis_indices", [])
        if _valid_index(idx, word_count)
    }
    event_sets = [
        set(_event_indices(plan, key, word_count))
        for key in ("broll_moments", "transitions", "audio_cues", "asset_requests")
    ]
    overloaded = []
    for idx in range(word_count):
        density = int(idx in emphasis) + sum(int(idx in values) for values in event_sets)
        if density >= 4:
            overloaded.append(idx)
    if overloaded:
        issue("effect_overload", "warning", f"Too many effects stack at indices {overloaded[:5]}.", min(12, len(overloaded) * 4))

    if len(emphasis) > 15:
        issue("emphasis_density", "warning", "More than 15 words are emphasized.", 6)
    if len(plan.get("audio_cues", [])) > max(5, word_count // 35 + 2):
        issue("audio_density", "warning", "Audio cues are too dense for the transcript length.", 7)
    if len(plan.get("transitions", [])) > max(5, word_count // 45 + 2):
        issue("transition_density", "warning", "Stylized transition planning is too dense.", 7)

    if profile in {"gaming", "minecraft_narrative"}:
        timed_words = [
            word for word in words
            if isinstance(word, dict)
            and isinstance(word.get("start"), (int, float))
            and isinstance(word.get("end"), (int, float))
        ]
        duration = (
            float(timed_words[-1]["end"]) - float(timed_words[0]["start"])
            if len(timed_words) == word_count and timed_words else 0.0
        )
        if duration <= 0:
            issue("gaming_not_evaluable", "critical", "Gaming admission requires measurable transcript or timeline timing.", 30)
        else:
            def event_time(index: int) -> float:
                return float(words[index].get("start", 0.0)) - float(words[0].get("start", 0.0))

            hook_events = [
                event for event in beats
                if isinstance(event, dict)
                and event.get("beat_type") == "hook"
                and _valid_index(event.get("word_index"), word_count)
            ]
            if hook_events:
                hook_time = min(event_time(event["word_index"]) for event in hook_events)
                hook_has_stake = any(len(str(event.get("intent", "")).strip()) >= 8 for event in hook_events)
                if hook_time > 12:
                    issue("gaming_hook_hard_max", "critical", "The supported Gaming hook starts after the 12-second hard maximum.", 20)
                elif hook_time > 8:
                    issue("gaming_hook_late", "warning", "The Gaming hook starts outside the 0-to-8-second ideal window.", 7)
                elif hook_time > 3 or not hook_has_stake:
                    issue("gaming_stake_clarity", "warning", "The opening does not establish a supported stake by the 3-second target.", 5)

            meaningful_indices = set(emphasis)
            for key in ("story_beats", "broll_moments", "transitions"):
                meaningful_indices.update(_event_indices(plan, key, word_count))
            visual_change_rate = len(meaningful_indices) * 60.0 / duration
            if visual_change_rate < 10:
                issue("gaming_visual_change_rate_low", "critical", "The Gaming plan is too sluggish to admit; motivated visual changes are below the setup floor.", 18)
            elif visual_change_rate < 14:
                issue("gaming_visual_change_rate_low", "warning", "The whole-video motivated visual-change rate is below the 14-per-minute prior.", 7)

            state_times = sorted({
                event_time(event["word_index"])
                for event in beats
                if isinstance(event, dict) and _valid_index(event.get("word_index"), word_count)
            })
            state_boundaries = [0.0, *state_times, duration]
            if any(right - left > 35 for left, right in zip(state_boundaries, state_boundaries[1:])):
                issue("gaming_unresolved_span", "critical", "More than 35 seconds pass without a supported question, state change, or payoff.", 14)

            stylized = [
                event for event in plan.get("transitions", [])
                if isinstance(event, dict) and event.get("type") != "hard_cut"
            ]
            stylized_rate = len(stylized) * 60.0 / duration
            if duration >= 30 and stylized_rate > 2:
                issue("gaming_stylized_transition_rate", "critical", "Stylized transitions exceed the 0-to-2-per-minute Gaming prior.", 14)
            unmotivated = sum(
                1 for event in stylized
                if not re.search(r"\b(match|direction|motion|action|state|impact|location|movement)\w*\b", str(event.get("reason", "")), re.IGNORECASE)
            )
            if unmotivated:
                issue("gaming_unmotivated_transition", "critical", f"{unmotivated} stylized transitions lack matched motion or a real action/state reason.", min(16, unmotivated * 6))

            proof_beats = [
                event for event in beats
                if isinstance(event, dict) and event.get("beat_type") in {"escalation", "setback", "reveal", "payoff"}
            ]
            if proof_beats:
                proof_coverage = min(1.0, len(broll) / len(proof_beats))
                if proof_coverage < 0.75:
                    issue("gaming_proof_coverage", "warning", "Fewer than 75% of concrete escalation/payoff beats have a visual-proof intent.", 7)

    score = max(0, min(100, score))
    critical = sum(1 for item in issues if item["severity"] == "critical")
    threshold = GAMING_QUALITY_THRESHOLD if profile in {"gaming", "minecraft_narrative"} else QUALITY_THRESHOLD
    return {
        "schema_version": "klippd.plan_evaluation.v1",
        "score": score,
        "passed": score >= threshold and critical == 0,
        "threshold": threshold,
        "profile": profile,
        "critical_count": critical,
        "issues": issues,
    }


def _dedupe_events(events: Iterable[Any], word_count: int) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for event in events:
        if not isinstance(event, dict) or not _valid_index(event.get("word_index"), word_count):
            continue
        marker = (event["word_index"], event.get("type"), event.get("beat_type"), event.get("kind"))
        if marker in seen:
            continue
        seen.add(marker)
        result.append(copy.deepcopy(event))
    return sorted(result, key=lambda event: event["word_index"])


def repair_edit_plan(plan: Dict[str, Any], words: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Repair only mechanical/safety defects; never create a missing story beat."""
    repaired = copy.deepcopy(plan)
    word_count = len(words)
    for key in ("story_beats", "transitions", "audio_cues", "broll_moments", "asset_requests"):
        repaired[key] = _dedupe_events(repaired.get(key, []), word_count)

    for key in ("filler_indices", "emphasis_indices"):
        values = repaired.get(key, []) if isinstance(repaired.get(key), list) else []
        repaired[key] = sorted({value for value in values if _valid_index(value, word_count)})
    repaired["emphasis_indices"] = repaired["emphasis_indices"][:15]

    broll_indices = {event["word_index"] for event in repaired["broll_moments"]}
    safe_assets = []
    for asset in repaired["asset_requests"]:
        combined = " ".join(str(asset.get(key, "")) for key in ("text", "subtext", "reason"))
        if _UNSAFE_GENERATION_TERMS.search(combined) or asset["word_index"] not in broll_indices:
            continue
        asset["provenance"] = "generated_editorial_graphic"
        asset["is_evidence"] = False
        safe_assets.append(asset)
    repaired["asset_requests"] = safe_assets[:5]

    # Reduce stacked decoration while preserving the story beat and B-roll intent.
    emphasis = set(repaired["emphasis_indices"])
    broll = {event["word_index"] for event in repaired["broll_moments"]}
    assets = {event["word_index"] for event in repaired["asset_requests"]}
    story = {event["word_index"] for event in repaired["story_beats"]}
    kept_audio = []
    for event in repaired["audio_cues"]:
        idx = event["word_index"]
        density = int(idx in emphasis) + int(idx in broll) + int(idx in assets) + int(idx in story)
        if density < 4 or event.get("type") == "silence":
            kept_audio.append(event)
    repaired["audio_cues"] = kept_audio

    max_transitions = max(5, word_count // 45 + 2)
    repaired["transitions"] = repaired["transitions"][:max_transitions]
    max_audio = max(5, word_count // 35 + 2)
    repaired["audio_cues"] = repaired["audio_cues"][:max_audio]
    return repaired


def quality_gate_edit_plan(
    plan: Dict[str, Any],
    words: Sequence[Dict[str, Any]],
    requested_profile: str | None = None,
    max_rounds: int = 2,
    knowledge: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Evaluate/repair loop with a traceable report embedded in the plan."""
    context = retrieve_editing_context(words, requested_profile, knowledge=knowledge)
    current = copy.deepcopy(plan)
    reviews = []
    for round_number in range(1, max(1, max_rounds) + 1):
        evaluation = evaluate_edit_plan(current, words, context["profile"])
        reviews.append({"round": round_number, **evaluation})
        if evaluation["passed"] or round_number == max_rounds:
            break
        repaired = repair_edit_plan(current, words)
        if repaired == current:
            break
        current = repaired

    final = evaluate_edit_plan(current, words, context["profile"])
    current["quality_review"] = {
        "schema_version": "klippd.quality_review.v1",
        "passed": final["passed"],
        "score": final["score"],
        "threshold": final["threshold"],
        "profile": context["profile"],
        "knowledge_rule_ids": context["rule_ids"],
        "knowledge_modules": context.get("knowledge_modules", []),
        "rounds": reviews,
        "remaining_issues": final["issues"],
        "note": "Deterministic grounding and restraint gate; no copyrighted-footage fine-tuning is implied.",
    }
    return current


def build_revision_prompt(
    plan: Dict[str, Any],
    evaluation: Dict[str, Any],
    context: Dict[str, Any],
    numbered_transcript: str,
) -> str:
    """Build the single bounded semantic-revision request used by analysis."""
    compact_rules = [
        {
            "id": rule.get("id"),
            "rule": rule.get("rule"),
            "guardrail": rule.get("guardrail"),
        }
        for rule in context.get("rules", [])
    ]
    payload = {
        "instruction": (
            "Revise the edit plan only to address the exact evaluation issues. "
            "Use only events supported by the numbered transcript. You may identify a missing hook, "
            "reveal, or payoff only when its words are explicitly present. Otherwise remove or weaken "
            "the unsupported promise instead of inventing a beat. Preserve safe useful decisions. "
            "Never invent gameplay, results, screenshots, people, quotes, logos, or evidence. "
            "Return only the complete revised plan JSON in the same schema, without quality_review."
        ),
        "evaluation_issues": evaluation.get("issues", []),
        "retrieved_profile": context.get("profile", "general"),
        "retrieved_rules": compact_rules,
        "numbered_transcript": numbered_transcript,
        "current_sanitized_plan": {
            key: value for key, value in plan.items() if key != "quality_review"
        },
    }
    return json.dumps(payload, ensure_ascii=False)
