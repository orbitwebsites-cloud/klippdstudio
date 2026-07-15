import copy

import pytest

from edit_chat_engine import (
    EditCommandError, EditSession, SCHEMA_VERSION, apply_command,
    compile_chat_request, preview_command, validate_command,
)
from video_processor import build_keep_segments


def project():
    return {
        "id": "p1",
        "transcript": {"words": [{"word": f"w{i}", "start": i * .5, "end": i * .5 + .4} for i in range(50)]},
        "analysis": {
            "filler_indices": [2, 4], "emphasis_indices": [],
            "story_beats": [{"word_index": 20, "beat_type": "reveal", "intent": "answer"}],
            "transitions": [], "audio_cues": [],
        },
        "render_options": {"style": "tiktok", "aspect": "16:9", "captions": True, "selected_broll": [{"word_index": 8, "id": "old"}]},
        "asset_library": [{"id": "approved", "approved": True, "local_path": "safe.mp4"}, {"id": "bad", "approved": False}],
        "available_creator_profiles": [{"id": "creator.fast"}],
    }


def command(command_id, *operations):
    return {"schema_version": SCHEMA_VERSION, "command_id": command_id, "operations": list(operations)}


def creator_grammar(*, serif=False, hook_seconds=2.5, hook_cpm=30, highlight_ratio=.25, zooms=12):
    metric = lambda value: {"value": value, "confidence": .9, "evidence_count": 20, "sample_count": 3, "outliers_removed": 0}
    pattern = lambda values: {"values": values, "confidence": .9, "evidence_count": 20}
    return {
        "hook_duration_seconds": metric(hook_seconds),
        "pacing_by_section": [{"section": "hook", "cuts_per_minute": metric(hook_cpm), "average_shot_seconds": metric(2)}],
        "caption_typography": pattern(["editorial serif" if serif else "clean minimal"]),
        "caption_position": pattern(["upper center" if serif else "lower center"]),
        "caption_highlight_ratio": metric(highlight_ratio),
        "zooms_per_minute": metric(zooms),
    }


def test_preview_and_apply_are_copy_on_write_and_show_diff():
    original = project()
    snapshot = copy.deepcopy(original)
    cmd = command("c1", {"type": "set_word_cut", "word_indices": [3, 2], "cut": True})
    preview = preview_command(original, cmd)
    applied = apply_command(original, cmd)
    assert original == snapshot
    assert applied["analysis"]["filler_indices"] == [2, 3, 4]
    assert preview["project_state"] == applied and preview["changed"]
    assert any(change["path"] == "/analysis/filler_indices" for change in preview["changes"])


def test_compile_supported_examples():
    state = project()
    fast = compile_chat_request("make first 10 seconds faster", state)
    luxury = compile_chat_request("luxury captions", state)
    remove = compile_chat_request("remove this B-roll", state, selection={"word_index": 8})
    impact = compile_chat_request("more impact on the reveal", state)
    assert fast["operations"][0] == {"type": "set_hook_pacing", "end_seconds": 10.0, "target": "faster"}
    assert luxury["operations"][0]["preset"] == "luxury"
    assert remove["operations"][0]["action"] == "remove"
    assert [op["type"] for op in impact["operations"]] == ["set_emphasis", "set_timeline_cue"]


def test_compile_shipped_suggestion_tightens_transcript_grounded_fillers():
    state = project()
    state["transcript"]["words"][7]["word"] = "um"
    compiled = compile_chat_request("Tighten pauses and remove filler words", state)
    assert compiled["operations"] == [{"type": "set_word_cut", "word_indices": [2, 4, 7], "cut": True}]
    applied = apply_command(state, compiled)
    assert applied["analysis"]["filler_indices"] == [2, 4, 7]


def test_compile_shipped_suggestion_uses_grounded_keyword_highlights():
    state = project()
    state["analysis"]["emphasis_indices"] = [6]
    compiled = compile_chat_request("Use cleaner captions with key words highlighted.", state)
    operation = compiled["operations"][0]
    assert operation == {
        "type": "set_captions", "enabled": True, "preset": "minimal",
        "placement": "bottom", "highlight_word_indices": [6, 20],
    }
    applied = apply_command(state, compiled)
    assert applied["render_options"]["caption_settings"]["highlight_word_indices"] == [6, 20]


def test_clean_caption_suggestion_has_deterministic_transcript_fallback():
    state = project()
    state["analysis"]["story_beats"] = []
    state["transcript"]["words"] = [
        {"word": "the"}, {"word": "extraordinary"}, {"word": "result"},
        {"word": "extraordinary"}, {"word": "today"},
    ]
    compiled = compile_chat_request("Use cleaner captions with keywords highlighted", state)
    assert compiled["operations"][0]["highlight_word_indices"] == [1, 2, 4]


def test_exact_ui_reveal_suggestion_is_accepted():
    compiled = compile_chat_request("Add more impact to the reveal", project())
    assert [operation["type"] for operation in compiled["operations"]] == ["set_emphasis", "set_timeline_cue"]


def test_compiled_examples_apply_to_runtime_shapes():
    state = project()
    state = apply_command(state, compile_chat_request("luxury captions", state))
    state = apply_command(state, compile_chat_request("remove this B-roll", state, selection={"word_index": 8}))
    state = apply_command(state, compile_chat_request("more impact on reveal", state))
    assert state["render_options"]["style"] == "luxury"
    assert state["render_options"]["selected_broll"] == []
    assert state["analysis"]["emphasis_indices"] == [20]
    assert state["analysis"]["audio_cues"][0]["type"] == "impact"


def test_cut_and_restore_words_are_deterministic():
    state = project()
    state = apply_command(state, compile_chat_request("cut words 1, 3", state))
    state = apply_command(state, compile_chat_request("restore word 2", state))
    assert state["analysis"]["filler_indices"] == [1, 3, 4]


def test_session_history_is_immutable_and_supports_undo_redo_and_idempotency():
    initial = project()
    session = EditSession.create(initial)
    cmd = command("same", {"type": "set_render_format", "aspect": "9:16"})
    applied, response = session.apply(cmd)
    duplicate, duplicate_response = applied.apply(cmd)
    undone = applied.undo()
    redone = undone.redo()
    assert session.state == initial and session.history == ()
    assert response["status"] == "applied" and applied.state["render_options"]["aspect"] == "9:16"
    assert duplicate is applied and duplicate_response["status"] == "duplicate"
    assert undone.state == initial and redone.state == applied.state
    assert len(applied.history) == 1 and len(undone.redo_stack) == 1


def test_new_apply_clears_redo_without_mutating_old_session():
    session, _ = EditSession.create(project()).apply(command("one", {"type": "set_render_format", "aspect": "9:16"}))
    undone = session.undo()
    changed, _ = undone.apply(command("two", {"type": "set_render_format", "style": "youtube"}))
    assert len(undone.redo_stack) == 1 and changed.redo_stack == ()
    with pytest.raises(EditCommandError, match="no undone"):
        changed.redo()


@pytest.mark.parametrize("bad,code", [
    ({"schema_version": SCHEMA_VERSION, "command_id": "x", "operations": [{"type": "shell", "command": "rm"}]}, "unsupported_operation"),
    (command("x", {"type": "set_render_format", "style": "cinematic-freeform"}), "invalid_value"),
    (command("x", {"type": "set_word_cut", "word_indices": [1], "cut": True, "surprise": 1}), "unsupported_field"),
])
def test_untrusted_json_is_closed_schema(bad, code):
    with pytest.raises(EditCommandError) as caught:
        validate_command(bad)
    assert caught.value.code == code


def test_reference_and_asset_validation_happen_before_mutation():
    state = project()
    with pytest.raises(EditCommandError) as word_error:
        apply_command(state, command("word", {"type": "set_emphasis", "word_indices": [99], "enabled": True}))
    with pytest.raises(EditCommandError) as asset_error:
        apply_command(state, command("asset", {"type": "set_broll", "action": "assign", "word_index": 3, "asset_id": "bad"}))
    assert word_error.value.code == "word_out_of_range"
    assert asset_error.value.code == "unapproved_asset"
    assert state == project()


def test_broll_replace_uses_only_registered_asset_metadata():
    state = apply_command(project(), command("b", {"type": "set_broll", "action": "replace", "word_index": 8, "asset_id": "approved"}))
    assert state["render_options"]["selected_broll"] == [{"id": "approved", "local_path": "safe.mp4", "word_index": 8}]


def test_creator_profile_must_be_saved_on_project():
    state = apply_command(project(), command("profile", {"type": "select_creator_profile", "profile_id": "creator.fast"}))
    assert state["creator_profile"] == "creator.fast"
    with pytest.raises(EditCommandError) as caught:
        apply_command(project(), command("profile2", {"type": "select_creator_profile", "profile_id": "unknown"}))
    assert caught.value.code == "unknown_creator_profile"


def test_unsupported_chat_and_missing_context_are_explicitly_rejected():
    with pytest.raises(EditCommandError) as unsupported:
        compile_chat_request("make it go viral somehow", project())
    with pytest.raises(EditCommandError) as selection:
        compile_chat_request("remove this B-roll", project())
    no_reveal = project(); no_reveal["analysis"]["story_beats"] = []
    with pytest.raises(EditCommandError) as reveal:
        compile_chat_request("more impact on reveal", no_reveal)
    assert unsupported.value.code == "unsupported_request"
    assert selection.value.code == "selection_required"
    assert reveal.value.code == "missing_timeline_anchor"


def test_safety_limits_reject_oversized_commands():
    too_many = command("many", *({"type": "set_render_format", "aspect": "9:16"} for _ in range(33)))
    with pytest.raises(EditCommandError) as caught:
        validate_command(too_many)
    assert caught.value.code == "safety_limit"


def test_repeated_operations_are_idempotent_at_state_level():
    cmd = command("x", {"type": "set_timeline_cue", "cue_kind": "audio", "action": "upsert", "word_index": 20, "cue_type": "impact"})
    once = apply_command(project(), cmd)
    twice = apply_command(once, command("y", *cmd["operations"]))
    assert once == twice


def test_null_render_options_from_new_project_are_supported():
    state = project(); state["render_options"] = None
    result = apply_command(state, compile_chat_request("luxury captions", state))
    assert result["render_options"]["captions"] is True
    assert result["render_options"]["style"] == "luxury"
    assert state["render_options"] is None


def test_creator_grammar_changes_supported_caption_command_deterministically():
    state = project()
    state["analysis"]["emphasis_indices"] = [3, 6, 9, 12]
    luxury = compile_chat_request(
        "Use cleaner captions with key words highlighted", state,
        creator_grammar=creator_grammar(serif=True, highlight_ratio=.5),
    )["operations"][0]
    minimal = compile_chat_request(
        "Use cleaner captions with key words highlighted", state,
        context={"creator_grammar": creator_grammar(serif=False, highlight_ratio=.25)},
    )["operations"][0]
    assert (luxury["preset"], luxury["placement"], luxury["highlight_word_indices"]) == ("luxury", "top", [3, 6, 9])
    assert (minimal["preset"], minimal["placement"], minimal["highlight_word_indices"]) == ("minimal", "bottom", [3, 6])
    rendered_state = apply_command(state, {"schema_version": SCHEMA_VERSION, "command_id": "dna-captions", "operations": [luxury]})
    assert rendered_state["analysis"]["emphasis_indices"] == [3, 6, 9, 12]


def test_apply_creator_dna_compiles_only_closed_grounded_operations():
    state = project()
    state["analysis"]["emphasis_indices"] = [3, 6, 9, 12]
    compiled = compile_chat_request("Apply creator DNA", state, creator_grammar=creator_grammar())
    assert [operation["type"] for operation in compiled["operations"]] == ["set_hook_pacing", "set_captions", "set_emphasis"]
    assert compiled["operations"][0] == {"type": "set_hook_pacing", "end_seconds": 2.5, "target": "faster"}
    assert compiled["operations"][2]["word_indices"] == [3, 6, 9, 12, 20]
    validate_command(compiled)


def test_creator_grammar_changes_reveal_zoom_within_safe_bounds():
    low = compile_chat_request("Add more impact to the reveal", project(), creator_grammar=creator_grammar(zooms=4))
    high = compile_chat_request("Add more impact to the reveal", project(), creator_grammar=creator_grammar(zooms=40))
    assert low["operations"][0]["zoom"] == 1.1
    assert high["operations"][0]["zoom"] == 1.28


def test_hook_pacing_promotes_only_timed_fillers_into_renderer_cut_field():
    state = project()
    state["analysis"]["filler_indices"] = []
    state["transcript"]["words"][2]["word"] = "um"       # ends 1.4s, inside hook
    state["transcript"]["words"][20]["word"] = "uh"      # ends 10.4s, outside hook
    compiled = compile_chat_request("make first 3 seconds faster", state)
    applied = apply_command(state, compiled)
    assert applied["analysis"]["filler_indices"] == [2]
    assert applied["render_options"]["remove_fillers"] is True
    keep = build_keep_segments(applied["transcript"]["words"], applied["analysis"]["filler_indices"], 25)
    assert any(segment["end"] <= .98 for segment in keep)
    assert all(not (segment["start"] < 1.2 < segment["end"]) for segment in keep)


def test_caption_highlights_feed_renderer_consumed_emphasis_indices():
    state = project()
    compiled = compile_chat_request("Use cleaner captions with key words highlighted", state)
    highlighted = compiled["operations"][0]["highlight_word_indices"]
    applied = apply_command(state, compiled)
    assert applied["analysis"]["emphasis_indices"] == highlighted


def test_creator_grammar_rejects_unsafe_or_unsupported_metrics():
    bad = creator_grammar(); bad["zooms_per_minute"]["value"] = 9999
    with pytest.raises(EditCommandError) as caught:
        compile_chat_request("Apply creator DNA", project(), creator_grammar=bad)
    assert caught.value.code == "invalid_creator_grammar"


def test_creator_dna_requires_supported_evidence():
    with pytest.raises(EditCommandError) as caught:
        compile_chat_request("Apply creator DNA", project())
    assert caught.value.code == "creator_grammar_required"
