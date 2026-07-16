"""Deterministic, model-agnostic command engine for chat-driven editing.

An LLM may produce the JSON accepted by :func:`validate_command`, but no model
output is ever applied directly.  This module owns the closed operation set,
reference checks, safety limits, copy-on-write mutation, preview diffs, and an
immutable undo/redo journal.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = "klippd.edit_command.v1"
MAX_OPERATIONS = 32
MAX_WORDS_PER_OPERATION = 500
MAX_TIMELINE_EVENTS = 64
ALLOWED_STYLES = {"tiktok", "youtube", "luxury"}
ALLOWED_ASPECTS = {"16:9", "9:16", "1:1"}
ALLOWED_CAPTION_PRESETS = {"default", "bold", "minimal", "luxury"}
ALLOWED_CAPTION_PLACEMENTS = {"top", "center", "bottom"}
ALLOWED_PACING_TARGETS = {"tight", "faster", "balanced"}
ALLOWED_TRANSITIONS = {"hard_cut", "match_cut", "push", "whip", "dip"}
ALLOWED_AUDIO_CUES = {"impact", "whoosh", "riser", "silence", "glitch"}
ALLOWED_OPERATIONS = {
    "set_word_cut", "set_hook_pacing", "set_captions", "set_broll",
    "set_emphasis", "set_timeline_cue", "set_render_format",
    "select_creator_profile",
}


class EditCommandError(ValueError):
    """A safe, user-displayable rejection of a chat edit command."""

    def __init__(self, code: str, message: str, *, operation_index: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.operation_index = operation_index

    def as_dict(self) -> Dict[str, Any]:
        result = {"code": self.code, "message": self.message}
        if self.operation_index is not None:
            result["operation_index"] = self.operation_index
        return result


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _strict_keys(value: Mapping[str, Any], required: set[str], optional: set[str], index: int) -> None:
    missing = required - set(value)
    extra = set(value) - required - optional
    if missing:
        raise EditCommandError("missing_field", f"Missing fields: {', '.join(sorted(missing))}.", operation_index=index)
    if extra:
        raise EditCommandError("unsupported_field", f"Unsupported fields: {', '.join(sorted(extra))}.", operation_index=index)


def _word_indices(value: Any, index: int) -> list[int]:
    if not isinstance(value, list) or not value:
        raise EditCommandError("invalid_word_indices", "word_indices must be a non-empty list.", operation_index=index)
    if len(value) > MAX_WORDS_PER_OPERATION:
        raise EditCommandError("safety_limit", f"A single edit can target at most {MAX_WORDS_PER_OPERATION} words.", operation_index=index)
    if any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in value):
        raise EditCommandError("invalid_word_indices", "word_indices must contain non-negative integers.", operation_index=index)
    return sorted(set(value))


def validate_operation(operation: Any, index: int = 0) -> Dict[str, Any]:
    """Strictly validate and normalize one operation; unknown keys are rejected."""
    if not isinstance(operation, Mapping):
        raise EditCommandError("invalid_operation", "Each operation must be an object.", operation_index=index)
    kind = operation.get("type")
    if kind not in ALLOWED_OPERATIONS:
        raise EditCommandError("unsupported_operation", f"Unsupported edit operation: {kind!r}.", operation_index=index)
    op = dict(operation)
    if kind == "set_word_cut":
        _strict_keys(op, {"type", "word_indices", "cut"}, set(), index)
        op["word_indices"] = _word_indices(op["word_indices"], index)
        if not isinstance(op["cut"], bool):
            raise EditCommandError("invalid_value", "cut must be true or false.", operation_index=index)
    elif kind == "set_hook_pacing":
        _strict_keys(op, {"type", "end_seconds", "target"}, set(), index)
        if not isinstance(op["end_seconds"], (int, float)) or isinstance(op["end_seconds"], bool) or not 0.5 <= float(op["end_seconds"]) <= 30:
            raise EditCommandError("invalid_range", "Hook pacing must target 0.5 to 30 seconds.", operation_index=index)
        if op["target"] not in ALLOWED_PACING_TARGETS:
            raise EditCommandError("invalid_value", "Unknown pacing target.", operation_index=index)
        op["end_seconds"] = float(op["end_seconds"])
    elif kind == "set_captions":
        _strict_keys(op, {"type"}, {"enabled", "preset", "placement", "highlight_word_indices"}, index)
        if len(op) == 1:
            raise EditCommandError("missing_field", "Caption edit needs at least one setting.", operation_index=index)
        if "enabled" in op and not isinstance(op["enabled"], bool):
            raise EditCommandError("invalid_value", "enabled must be true or false.", operation_index=index)
        if "preset" in op and op["preset"] not in ALLOWED_CAPTION_PRESETS:
            raise EditCommandError("invalid_value", "Unknown caption preset.", operation_index=index)
        if "placement" in op and op["placement"] not in ALLOWED_CAPTION_PLACEMENTS:
            raise EditCommandError("invalid_value", "Unknown caption placement.", operation_index=index)
        if "highlight_word_indices" in op:
            op["highlight_word_indices"] = _word_indices(op["highlight_word_indices"], index)
    elif kind == "set_broll":
        _strict_keys(op, {"type", "action", "word_index"}, {"asset_id"}, index)
        if op["action"] not in {"assign", "remove", "replace"}:
            raise EditCommandError("invalid_value", "B-roll action must be assign, remove, or replace.", operation_index=index)
        if not isinstance(op["word_index"], int) or isinstance(op["word_index"], bool) or op["word_index"] < 0:
            raise EditCommandError("invalid_word_index", "word_index must be a non-negative integer.", operation_index=index)
        if op["action"] in {"assign", "replace"} and not isinstance(op.get("asset_id"), str):
            raise EditCommandError("missing_field", "Assigning B-roll requires an approved asset_id.", operation_index=index)
        if op["action"] == "remove" and "asset_id" in op:
            raise EditCommandError("unsupported_field", "Remove B-roll does not accept asset_id.", operation_index=index)
    elif kind == "set_emphasis":
        _strict_keys(op, {"type", "word_indices", "enabled"}, {"zoom"}, index)
        op["word_indices"] = _word_indices(op["word_indices"], index)
        if not isinstance(op["enabled"], bool):
            raise EditCommandError("invalid_value", "enabled must be true or false.", operation_index=index)
        if "zoom" in op and (not isinstance(op["zoom"], (int, float)) or isinstance(op["zoom"], bool) or not 1.0 <= float(op["zoom"]) <= 1.5):
            raise EditCommandError("invalid_range", "Zoom must be between 1.0 and 1.5.", operation_index=index)
    elif kind == "set_timeline_cue":
        _strict_keys(op, {"type", "cue_kind", "action", "word_index"}, {"cue_type"}, index)
        if op["cue_kind"] not in {"transition", "audio"} or op["action"] not in {"upsert", "remove"}:
            raise EditCommandError("invalid_value", "Invalid timeline cue kind or action.", operation_index=index)
        if not isinstance(op["word_index"], int) or isinstance(op["word_index"], bool) or op["word_index"] < 0:
            raise EditCommandError("invalid_word_index", "word_index must be a non-negative integer.", operation_index=index)
        allowed = ALLOWED_TRANSITIONS if op["cue_kind"] == "transition" else ALLOWED_AUDIO_CUES
        if op["action"] == "upsert" and op.get("cue_type") not in allowed:
            raise EditCommandError("invalid_value", "Unknown timeline cue type.", operation_index=index)
        if op["action"] == "remove" and "cue_type" in op:
            raise EditCommandError("unsupported_field", "Removing a cue does not accept cue_type.", operation_index=index)
    elif kind == "set_render_format":
        _strict_keys(op, {"type"}, {"style", "aspect"}, index)
        if len(op) == 1:
            raise EditCommandError("missing_field", "Render edit needs style or aspect.", operation_index=index)
        if "style" in op and op["style"] not in ALLOWED_STYLES:
            raise EditCommandError("invalid_value", "Unknown render style.", operation_index=index)
        if "aspect" in op and op["aspect"] not in ALLOWED_ASPECTS:
            raise EditCommandError("invalid_value", "Unknown aspect ratio.", operation_index=index)
    else:
        _strict_keys(op, {"type", "profile_id"}, set(), index)
        if op["profile_id"] is not None and (not isinstance(op["profile_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", op["profile_id"])):
            raise EditCommandError("invalid_value", "profile_id must be a saved profile ID or null.", operation_index=index)
    return op


def validate_command(command: Any) -> Dict[str, Any]:
    """Validate untrusted JSON (including a future LLM proposal)."""
    if not isinstance(command, Mapping):
        raise EditCommandError("invalid_command", "Edit command must be an object.")
    _strict_keys(command, {"schema_version", "command_id", "operations"}, {"source_text"}, 0)
    if command["schema_version"] != SCHEMA_VERSION:
        raise EditCommandError("unsupported_schema", "Unsupported edit command schema.")
    if not isinstance(command["command_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", command["command_id"]):
        raise EditCommandError("invalid_command_id", "command_id is invalid.")
    operations = command["operations"]
    if not isinstance(operations, list) or not operations:
        raise EditCommandError("invalid_operations", "A command needs at least one operation.")
    if len(operations) > MAX_OPERATIONS:
        raise EditCommandError("safety_limit", f"A command can contain at most {MAX_OPERATIONS} operations.")
    result = dict(command)
    result["operations"] = [validate_operation(op, i) for i, op in enumerate(operations)]
    if "source_text" in result:
        if not isinstance(result["source_text"], str):
            raise EditCommandError("invalid_source_text", "source_text must be text.")
        result["source_text"] = result["source_text"][:1000]
    return result


def _words(state: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    transcript = state.get("transcript", {})
    return transcript.get("words", []) if isinstance(transcript, Mapping) and isinstance(transcript.get("words"), list) else []


def _check_references(state: Mapping[str, Any], operation: Mapping[str, Any], index: int) -> None:
    word_count = len(_words(state))
    indices = operation.get("word_indices", [operation.get("word_index")])
    for value in indices:
        if value is not None and value >= word_count:
            raise EditCommandError("word_out_of_range", f"Word {value} is outside this {word_count}-word transcript.", operation_index=index)
    if operation["type"] == "set_broll" and operation["action"] in {"assign", "replace"}:
        library = state.get("asset_library", [])
        asset = next((item for item in library if isinstance(item, Mapping) and item.get("id") == operation["asset_id"]), None)
        if not asset or asset.get("approved") is not True:
            raise EditCommandError("unapproved_asset", "B-roll must reference an approved project asset.", operation_index=index)
    if operation["type"] == "select_creator_profile" and operation["profile_id"] is not None:
        profiles = state.get("available_creator_profiles", [])
        ids = {item.get("id") for item in profiles if isinstance(item, Mapping)}
        if operation["profile_id"] not in ids:
            raise EditCommandError("unknown_creator_profile", "Choose a saved creator profile attached to this project.", operation_index=index)


def _event_upsert(events: list[Dict[str, Any]], word_index: int, cue_type: str) -> list[Dict[str, Any]]:
    kept = [item for item in events if item.get("word_index") != word_index]
    kept.append({"word_index": word_index, "type": cue_type, "reason": "chat_edit"})
    return sorted(kept, key=lambda item: item["word_index"])


def _apply_operation(state: Dict[str, Any], op: Mapping[str, Any]) -> None:
    # Newly analyzed projects currently carry ``render_options: None``.  Treat
    # absent/null containers as their empty editable form without touching the
    # caller's object (``apply_command`` has already made the working copy).
    if not isinstance(state.get("analysis"), dict):
        state["analysis"] = {}
    if not isinstance(state.get("render_options"), dict):
        state["render_options"] = {}
    if not isinstance(state.get("timeline"), dict):
        state["timeline"] = {}
    analysis = state["analysis"]
    render = state["render_options"]
    timeline = state["timeline"]
    kind = op["type"]
    if kind == "set_word_cut":
        values = set(analysis.get("filler_indices", []))
        values = values | set(op["word_indices"]) if op["cut"] else values - set(op["word_indices"])
        analysis["filler_indices"] = sorted(values)
    elif kind == "set_hook_pacing":
        timeline["hook_pacing"] = {"end_seconds": op["end_seconds"], "target": op["target"]}
        # The current renderer consumes ``analysis.filler_indices`` through
        # video_processor.build_keep_segments.  Tight/faster hooks therefore
        # promote only exact, transcript-grounded filler/false-start indices
        # inside the requested opening window. No content word is guessed.
        if op["target"] in {"tight", "faster"}:
            timed_fillers = []
            words = _words(state)
            for word_index in _transcript_filler_indices(state):
                word = words[word_index]
                end = word.get("end")
                if isinstance(end, (int, float)) and not isinstance(end, bool) and float(end) <= op["end_seconds"]:
                    timed_fillers.append(word_index)
            analysis["filler_indices"] = sorted(set(analysis.get("filler_indices", [])) | set(timed_fillers))
            render["remove_fillers"] = True
    elif kind == "set_captions":
        captions = render.setdefault("caption_settings", {})
        if "enabled" in op:
            render["captions"] = op["enabled"]
        for key in ("preset", "placement"):
            if key in op:
                captions[key] = op[key]
        if "highlight_word_indices" in op:
            captions["highlight_word_indices"] = op["highlight_word_indices"]
            # generate_ass receives analysis.emphasis_indices from the current
            # render route, so caption keyword highlights affect the exported
            # ASS captions today rather than existing only as chat metadata.
            analysis["emphasis_indices"] = sorted(
                set(analysis.get("emphasis_indices", [])) | set(op["highlight_word_indices"])
            )
        if op.get("preset") == "luxury":
            render["style"] = "luxury"
    elif kind == "set_broll":
        selected = render.setdefault("selected_broll", [])
        selected[:] = [item for item in selected if item.get("word_index") != op["word_index"]]
        if op["action"] != "remove":
            asset = next(item for item in state["asset_library"] if item.get("id") == op["asset_id"])
            selected.append({key: _copy(asset[key]) for key in ("id", "video_url", "local_path", "is_custom", "generated") if key in asset} | {"word_index": op["word_index"]})
            selected.sort(key=lambda item: item["word_index"])
            render["broll"] = True
    elif kind == "set_emphasis":
        values = set(analysis.get("emphasis_indices", []))
        values = values | set(op["word_indices"]) if op["enabled"] else values - set(op["word_indices"])
        analysis["emphasis_indices"] = sorted(values)
        if "zoom" in op:
            zooms = timeline.setdefault("zooms", {})
            for word_index in op["word_indices"]:
                if op["enabled"]:
                    zooms[str(word_index)] = float(op["zoom"])
                else:
                    zooms.pop(str(word_index), None)
    elif kind == "set_timeline_cue":
        key = "transitions" if op["cue_kind"] == "transition" else "audio_cues"
        events = list(analysis.get(key, []))
        if op["action"] == "remove":
            analysis[key] = [item for item in events if item.get("word_index") != op["word_index"]]
        else:
            analysis[key] = _event_upsert(events, op["word_index"], op["cue_type"])
    elif kind == "set_render_format":
        for key in ("style", "aspect"):
            if key in op:
                render[key] = op[key]
    else:
        state["creator_profile"] = op["profile_id"]


def _diff(before: Any, after: Any, path: str = "") -> list[Dict[str, Any]]:
    if before == after:
        return []
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        result = []
        for key in sorted(set(before) | set(after)):
            result.extend(_diff(before.get(key), after.get(key), f"{path}/{key}"))
        return result
    return [{"path": path or "/", "before": _copy(before), "after": _copy(after)}]


def apply_command(project_state: Mapping[str, Any], command: Any) -> Dict[str, Any]:
    """Validate and apply to a deep copy. The supplied project is never mutated."""
    validated = validate_command(command)
    result = _copy(dict(project_state))
    for index, operation in enumerate(validated["operations"]):
        _check_references(result, operation, index)
        _apply_operation(result, operation)
        if len(result.get("analysis", {}).get("transitions", [])) > MAX_TIMELINE_EVENTS or len(result.get("analysis", {}).get("audio_cues", [])) > MAX_TIMELINE_EVENTS:
            raise EditCommandError("safety_limit", f"A project can contain at most {MAX_TIMELINE_EVENTS} cues of each kind.", operation_index=index)
    return result


def preview_command(project_state: Mapping[str, Any], command: Any) -> Dict[str, Any]:
    validated = validate_command(command)
    after = apply_command(project_state, validated)
    changes = _diff(project_state, after)
    return {
        "schema_version": "klippd.edit_preview.v1", "command_id": validated["command_id"],
        "changed": bool(changes), "change_count": len(changes), "changes": changes,
        "project_state": after,
    }


def _command_id(text: str, operations: Sequence[Mapping[str, Any]]) -> str:
    return "chat:" + hashlib.sha256(_canonical([text.strip().lower(), operations]).encode()).hexdigest()[:24]


_SPOKEN_FILLERS = {"um", "uh", "ah", "erm", "hmm", "basically", "literally"}
_KEYWORD_STOP_WORDS = {
    "about", "after", "again", "because", "before", "could", "every", "first",
    "from", "have", "just", "really", "should", "their", "there", "these",
    "thing", "this", "those", "through", "very", "what", "when", "where",
    "which", "while", "with", "would", "your",
}


def _transcript_filler_indices(project_state: Mapping[str, Any]) -> list[int]:
    """Return only analysis-backed or exact lexical filler positions."""
    words = _words(project_state)
    existing = project_state.get("analysis", {}).get("filler_indices", [])
    indices = {
        value for value in existing
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(words)
    }
    normalized = [re.sub(r"[^a-z0-9']", "", str(item.get("word", "")).lower()) for item in words]
    indices.update(index for index, word in enumerate(normalized) if word in _SPOKEN_FILLERS)
    # Exact adjacent repetitions are deterministic false starts, e.g. "I I".
    indices.update(
        index for index in range(1, len(normalized))
        if normalized[index] and normalized[index] == normalized[index - 1]
    )
    return sorted(indices)


def _transcript_keyword_indices(project_state: Mapping[str, Any]) -> list[int]:
    """Prefer explicit editorial anchors, then stable transcript-only keywords."""
    words = _words(project_state)
    analysis = project_state.get("analysis", {})
    explicit = {
        value for value in analysis.get("emphasis_indices", [])
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(words)
    }
    explicit.update(
        item.get("word_index") for item in analysis.get("story_beats", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("word_index"), int)
        and 0 <= item["word_index"] < len(words)
    )
    if explicit:
        return sorted(explicit)[:12]
    candidates = []
    seen = set()
    for index, item in enumerate(words):
        word = re.sub(r"[^a-z0-9']", "", str(item.get("word", "")).lower())
        if len(word) < 5 or word in _KEYWORD_STOP_WORDS or word in seen:
            continue
        seen.add(word)
        candidates.append((-len(word), index))
    # Long, unique content words are a conservative fallback; return in time order.
    return sorted(index for _, index in sorted(candidates)[:8])


def _metric_value(grammar: Mapping[str, Any], key: str, low: float, high: float) -> Optional[float]:
    metric = grammar.get(key)
    if metric is None:
        return None
    if not isinstance(metric, Mapping):
        raise EditCommandError("invalid_creator_grammar", f"Creator grammar metric {key} is invalid.")
    value = metric.get("value")
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or not low <= float(value) <= high:
        raise EditCommandError("invalid_creator_grammar", f"Creator grammar metric {key} is outside its safe range.")
    confidence = metric.get("confidence", 0)
    evidence = metric.get("evidence_count", 0)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
        raise EditCommandError("invalid_creator_grammar", f"Creator grammar confidence for {key} is invalid.")
    if not isinstance(evidence, int) or isinstance(evidence, bool) or evidence < 0:
        raise EditCommandError("invalid_creator_grammar", f"Creator grammar evidence for {key} is invalid.")
    # Low-evidence metrics do not influence edits; this is absence, not failure.
    return float(value) if float(confidence) >= 0.5 and evidence >= 1 else None


def _pattern_values(grammar: Mapping[str, Any], key: str) -> list[str]:
    pattern = grammar.get(key)
    if pattern is None:
        return []
    if not isinstance(pattern, Mapping) or not isinstance(pattern.get("values", []), list):
        raise EditCommandError("invalid_creator_grammar", f"Creator grammar pattern {key} is invalid.")
    confidence = pattern.get("confidence", 0)
    evidence = pattern.get("evidence_count", 0)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
        raise EditCommandError("invalid_creator_grammar", f"Creator grammar confidence for {key} is invalid.")
    if not isinstance(evidence, int) or isinstance(evidence, bool) or evidence < 0:
        raise EditCommandError("invalid_creator_grammar", f"Creator grammar evidence for {key} is invalid.")
    if float(confidence) < 0.5 or evidence < 1:
        return []
    values = pattern.get("values", [])
    if any(not isinstance(value, str) for value in values) or len(values) > 20:
        raise EditCommandError("invalid_creator_grammar", f"Creator grammar values for {key} are invalid.")
    return [" ".join(value.lower().split())[:80] for value in values]


def _creator_directives(creator_grammar: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if creator_grammar is None:
        return {}
    if hasattr(creator_grammar, "model_dump"):
        creator_grammar = creator_grammar.model_dump(mode="json")
    if not isinstance(creator_grammar, Mapping):
        raise EditCommandError("invalid_creator_grammar", "Creator grammar must be a trusted grammar object.")
    hook_seconds = _metric_value(creator_grammar, "hook_duration_seconds", 0.5, 30)
    highlight_ratio = _metric_value(creator_grammar, "caption_highlight_ratio", 0, 1)
    zooms_per_minute = _metric_value(creator_grammar, "zooms_per_minute", 0, 120)
    typography = _pattern_values(creator_grammar, "caption_typography")
    positions = _pattern_values(creator_grammar, "caption_position")
    combined_type = " ".join(typography)
    if any(token in combined_type for token in ("serif", "luxury", "editorial")):
        preset = "luxury"
    elif any(token in combined_type for token in ("clean", "minimal")):
        preset = "minimal"
    elif "bold" in combined_type:
        preset = "bold"
    else:
        preset = "default"
    combined_position = " ".join(positions)
    placement = "top" if "top" in combined_position or "upper" in combined_position else "center" if "center" in combined_position and "lower" not in combined_position else "bottom"
    hook_cpm = None
    pacing = creator_grammar.get("pacing_by_section", [])
    if pacing is not None:
        if not isinstance(pacing, list) or len(pacing) > 30:
            raise EditCommandError("invalid_creator_grammar", "Creator pacing grammar is invalid.")
        hook_row = next((row for row in pacing if isinstance(row, Mapping) and str(row.get("section", "")).lower() in {"hook", "opening", "overall"}), None)
        if hook_row:
            hook_cpm = _metric_value(hook_row, "cuts_per_minute", 0, 240)
    target = "faster" if hook_cpm is not None and hook_cpm >= 20 else "tight" if hook_cpm is not None and hook_cpm >= 10 else "balanced"
    zoom = 1.12 if zooms_per_minute is None else round(min(1.3, 1.08 + zooms_per_minute / 200), 2)
    return {
        "hook_seconds": hook_seconds, "pacing_target": target, "caption_preset": preset,
        "caption_placement": placement, "highlight_ratio": highlight_ratio,
        "zoom": zoom, "has_caption_evidence": bool(typography or positions or highlight_ratio is not None),
        "has_pacing_evidence": hook_seconds is not None or hook_cpm is not None,
        "has_zoom_evidence": zooms_per_minute is not None,
    }


def compile_chat_request(
    text: str,
    project_state: Mapping[str, Any],
    *,
    selection: Optional[Mapping[str, Any]] = None,
    command_id: Optional[str] = None,
    creator_grammar: Optional[Mapping[str, Any]] = None,
    context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Compile a deliberately small, deterministic phrase set (no fuzzy execution)."""
    if not isinstance(text, str) or not text.strip():
        raise EditCommandError("empty_request", "Type an editing request.")
    if context is not None:
        if not isinstance(context, Mapping):
            raise EditCommandError("invalid_context", "Edit context must be an object.")
        contextual_grammar = context.get("creator_grammar", context.get("grammar"))
        if creator_grammar is not None and contextual_grammar is not None and creator_grammar != contextual_grammar:
            raise EditCommandError("conflicting_creator_grammar", "Provide Creator DNA grammar once.")
        creator_grammar = creator_grammar if creator_grammar is not None else contextual_grammar
    directives = _creator_directives(creator_grammar)
    raw, lowered = text.strip(), text.strip().lower()
    operations: list[Dict[str, Any]] = []
    pacing = re.search(r"(?:make\s+)?(?:the\s+)?first\s+(\d+(?:\.\d+)?)\s*seconds?\s+(faster|tighter|balanced)", lowered)
    if pacing:
        target = {"tighter": "tight"}.get(pacing.group(2), pacing.group(2))
        operations.append({"type": "set_hook_pacing", "end_seconds": float(pacing.group(1)), "target": target})
    if re.search(r"\bluxury\s+captions?\b", lowered):
        operations.append({"type": "set_captions", "enabled": True, "preset": "luxury", "placement": "bottom"})
    if re.search(r"\btighten\s+pauses?\s+and\s+remove\s+filler\s+words?\b", lowered):
        fillers = _transcript_filler_indices(project_state)
        if not fillers:
            raise EditCommandError("missing_timeline_anchor", "I could not find transcript-grounded filler words to remove.")
        operations.append({"type": "set_word_cut", "word_indices": fillers, "cut": True})
    if re.search(r"\b(?:use\s+)?cleaner\s+captions?\s+with\s+key\s*words?\s+highlighted\b", lowered):
        keywords = _transcript_keyword_indices(project_state)
        if not keywords:
            raise EditCommandError("missing_timeline_anchor", "I could not find transcript-grounded key words to highlight.")
        ratio = directives.get("highlight_ratio")
        if ratio is not None:
            count = max(1, min(len(keywords), int(math.ceil(len(keywords) * ratio))))
            keywords = keywords[:count]
        operations.append({
            "type": "set_captions", "enabled": True,
            "preset": directives.get("caption_preset", "minimal") if directives.get("has_caption_evidence") else "minimal",
            "placement": directives.get("caption_placement", "bottom"), "highlight_word_indices": keywords,
        })
    word_action = re.fullmatch(r"(?:please\s+)?(cut|restore)\s+words?\s+([0-9,\s]+)", lowered)
    if word_action:
        indices = [int(item) for item in re.findall(r"\d+", word_action.group(2))]
        operations.append({"type": "set_word_cut", "word_indices": indices, "cut": word_action.group(1) == "cut"})
    if re.search(r"\bremove\s+(?:this|the selected)\s+b-?roll\b", lowered):
        selected_index = (selection or {}).get("word_index")
        if not isinstance(selected_index, int):
            raise EditCommandError("selection_required", "Select a B-roll moment before asking to remove this B-roll.")
        operations.append({"type": "set_broll", "action": "remove", "word_index": selected_index})
    if re.search(r"\b(?:add|insert|include|use)\s+(?:some\s+)?b-?roll\b", lowered):
        moments = [
            item for item in project_state.get("analysis", {}).get("broll_moments", [])
            if isinstance(item, Mapping) and isinstance(item.get("word_index"), int)
        ]
        if not moments:
            raise EditCommandError("missing_timeline_anchor", "I could not find a transcript-grounded B-roll moment to place footage on.")
        selected = {
            item.get("word_index") for item in project_state.get("render_options", {}).get("selected_broll", [])
            if isinstance(item, Mapping) and isinstance(item.get("word_index"), int)
        }
        assets = [
            item for item in project_state.get("asset_library", [])
            if isinstance(item, Mapping) and item.get("approved") is True and item.get("id")
        ]
        if not assets:
            raise EditCommandError("no_approved_asset", "There are no approved B-roll assets available. Search the approved pack or upload a rights-attested clip first.")
        unused_assets = list(assets)
        for moment in moments:
            word_index = moment["word_index"]
            if word_index in selected:
                continue
            matching = next((asset for asset in unused_assets if asset.get("word_index") == word_index), None)
            asset = matching or unused_assets[0]
            operations.append({"type": "set_broll", "action": "assign", "word_index": word_index, "asset_id": asset["id"]})
            if matching:
                unused_assets.remove(matching)
            if len(operations) >= MAX_OPERATIONS:
                break
        if not any(operation.get("type") == "set_broll" and operation.get("action") == "assign" for operation in operations):
            raise EditCommandError("broll_already_assigned", "Approved B-roll is already assigned to every available moment.")
    if re.search(r"\bmore impact (?:on|at|for|to) (?:the )?reveal\b", lowered):
        beats = project_state.get("analysis", {}).get("story_beats", [])
        reveal = next((item for item in beats if item.get("beat_type") in {"reveal", "payoff"}), None)
        if not reveal:
            raise EditCommandError("missing_timeline_anchor", "I could not find a transcript-grounded reveal to emphasize.")
        wi = reveal.get("word_index")
        operations.extend([
            {"type": "set_emphasis", "word_indices": [wi], "enabled": True, "zoom": directives.get("zoom", 1.18) if directives.get("has_zoom_evidence") else 1.18},
            {"type": "set_timeline_cue", "cue_kind": "audio", "action": "upsert", "word_index": wi, "cue_type": "impact"},
        ])
    if re.search(r"\b(?:apply|use|edit (?:this )?with)\s+(?:the )?creator\s+(?:dna|style)\b", lowered):
        if not directives:
            raise EditCommandError("creator_grammar_required", "Select a Creator DNA profile before applying its style.")
        keywords = _transcript_keyword_indices(project_state)
        if directives.get("has_pacing_evidence"):
            operations.append({
                "type": "set_hook_pacing", "end_seconds": directives.get("hook_seconds") or 8.0,
                "target": directives["pacing_target"],
            })
        if directives.get("has_caption_evidence"):
            caption_op = {
                "type": "set_captions", "enabled": True, "preset": directives["caption_preset"],
                "placement": directives["caption_placement"],
            }
            if keywords and directives.get("highlight_ratio") is not None:
                count = max(1, min(len(keywords), int(math.ceil(len(keywords) * directives["highlight_ratio"]))))
                caption_op["highlight_word_indices"] = keywords[:count]
            operations.append(caption_op)
        if directives.get("has_zoom_evidence") and keywords:
            operations.append({"type": "set_emphasis", "word_indices": keywords[:8], "enabled": True, "zoom": directives["zoom"]})
        if not operations:
            raise EditCommandError("insufficient_creator_grammar", "This Creator DNA profile has no sufficiently supported editing metrics.")
    if not operations:
        raise EditCommandError("unsupported_request", "That request cannot be converted into safe timeline edits yet. Try a pacing, caption, cut/restore, B-roll, or reveal-emphasis command.")
    command = {"schema_version": SCHEMA_VERSION, "command_id": command_id or _command_id(raw, operations), "source_text": raw, "operations": operations}
    return validate_command(command)


@dataclass(frozen=True)
class JournalEntry:
    command_json: str
    before_json: str
    after_json: str

    @property
    def command_id(self) -> str:
        return json.loads(self.command_json)["command_id"]


@dataclass(frozen=True)
class EditSession:
    """Immutable value object: every apply/undo/redo returns a new session."""
    state_json: str
    history: Tuple[JournalEntry, ...] = field(default_factory=tuple)
    redo_stack: Tuple[JournalEntry, ...] = field(default_factory=tuple)
    seen_command_ids: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def create(cls, project_state: Mapping[str, Any]) -> "EditSession":
        return cls(_canonical(project_state))

    @property
    def state(self) -> Dict[str, Any]:
        return json.loads(self.state_json)

    def preview(self, command: Any) -> Dict[str, Any]:
        return preview_command(self.state, command)

    def apply(self, command: Any) -> Tuple["EditSession", Dict[str, Any]]:
        validated = validate_command(command)
        if validated["command_id"] in self.seen_command_ids:
            return self, {"status": "duplicate", "command_id": validated["command_id"], "changed": False, "project_state": self.state}
        before = self.state
        preview = preview_command(before, validated)
        entry = JournalEntry(_canonical(validated), _canonical(before), _canonical(preview["project_state"]))
        session = EditSession(entry.after_json, self.history + (entry,), (), self.seen_command_ids | {validated["command_id"]})
        return session, {"status": "applied", **preview}

    def undo(self) -> "EditSession":
        if not self.history:
            raise EditCommandError("nothing_to_undo", "There are no applied chat edits to undo.")
        entry = self.history[-1]
        return EditSession(entry.before_json, self.history[:-1], self.redo_stack + (entry,), self.seen_command_ids)

    def redo(self) -> "EditSession":
        if not self.redo_stack:
            raise EditCommandError("nothing_to_redo", "There are no undone chat edits to redo.")
        entry = self.redo_stack[-1]
        return EditSession(entry.after_json, self.history + (entry,), self.redo_stack[:-1], self.seen_command_ids)
