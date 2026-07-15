import asyncio
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from creator_dna import (
    CreatorDNAAnalysisInput,
    CreatorDNARepository,
    ProvenanceSource,
    VideoStyleObservation,
    aggregate_creator_dna,
    observation_from_analyzed_project,
)
from local_store import LocalDatabase


def _request():
    return CreatorDNAAnalysisInput.model_validate({
        "owner_id": "user-1",
        "profile_name": "Fast narrative grammar",
        "consent_confirmed": True,
        "sources": [
            {"source_id": f"v{i}", "kind": "reference_url", "rights_basis": "public_reference_only", "url": f"https://video.example/watch/{i}"}
            for i in range(1, 6)
        ],
    })


def _observation(source_id, hook, confidence=0.9):
    return VideoStyleObservation.model_validate({
        "source_id": source_id,
        "duration_seconds": 60,
        "hook_duration_seconds": hook,
        "section_pacing": [
            {"section": "Hook", "cuts_per_minute": 30 + hook, "average_shot_seconds": 2, "evidence_count": 4},
            {"section": "Payoff", "cuts_per_minute": 18, "average_shot_seconds": 3.3, "evidence_count": 2},
        ],
        "captions": {"font_categories": ["bold sans serif"], "position": "lower center", "case_style": "sentence", "words_per_card": 4, "highlighted_word_ratio": 0.2, "highlight_behaviors": ["single keyword accent"]},
        "visuals": {"broll_density_per_minute": 5, "graphic_density_per_minute": 3, "transitions_per_minute": 2, "transition_types": ["motivated match cut"], "zooms_per_minute": 4, "motion_patterns": ["subtle punch in"], "color_traits": ["warm contrast"], "framing_traits": ["centered talking head"]},
        "audio": {"sfx_per_minute": 3, "music_present": True, "integrated_loudness_lufs": -14, "ducking_behavior": "music ducks under speech"},
        "story_beats": ["claim", "evidence", "escalation", "payoff"],
        "observation_confidence": confidence,
        "evidence_count": 10,
    })


def test_input_enforces_consent_rights_and_non_fetching_source_shape():
    with pytest.raises(ValidationError, match="consent_confirmed"):
        CreatorDNAAnalysisInput.model_validate({"owner_id": "u", "profile_name": "x", "consent_confirmed": False, "sources": [{"source_id": "1", "kind": "reference_url", "rights_basis": "public_reference_only", "url": "https://example.com/v"}]})
    with pytest.raises(ValidationError, match="owned, licensed"):
        ProvenanceSource.model_validate({"source_id": "1", "kind": "upload", "asset_id": "asset-1", "rights_basis": "public_reference_only"})
    with pytest.raises(ValidationError, match="absolute http"):
        ProvenanceSource.model_validate({"source_id": "1", "kind": "reference_url", "url": "file:///private/video.mp4", "rights_basis": "owned"})


def test_aggregation_removes_extreme_outlier_and_is_deterministic():
    observations = [_observation("v1", 2), _observation("v2", 2.1), _observation("v3", 1.9), _observation("v4", 2), _observation("v5", 50)]
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    first = aggregate_creator_dna(_request(), observations, now=now)
    second = aggregate_creator_dna(_request(), observations, now=now)
    assert first == second
    assert first.grammar.hook_duration_seconds.value == 2
    assert first.grammar.hook_duration_seconds.outliers_removed == 1
    assert first.grammar.hook_duration_seconds.evidence_count == 40
    assert [row.section for row in first.grammar.pacing_by_section] == ["hook", "payoff"]
    assert first.grammar.caption_typography.values == ["bold sans serif"]
    assert first.safety["identity_cloning"] is False
    assert first.safety["public_video_download_performed"] is False


def test_observations_must_match_declared_sources():
    with pytest.raises(ValueError, match="undeclared sources"):
        aggregate_creator_dna(_request(), [_observation("not-declared", 2)])
    with pytest.raises(ValueError, match="one observation"):
        aggregate_creator_dna(_request(), [_observation("v1", 2), _observation("v1", 2.1)])


def test_project_extractor_never_invents_absent_style_metrics():
    observation = observation_from_analyzed_project("v1", {
        "duration": 60,
        "analysis": {
            "emphasis_indices": [3, 8],
            "transitions": [{"type": "hard_cut", "word_index": 3}],
            "broll_moments": [],
            "story_beats": [{"beat_type": "reveal", "word_index": 8}],
        },
    })
    assert observation.hook_duration_seconds is None
    assert observation.section_pacing == []
    assert observation.captions.font_categories == []
    assert observation.captions.position == "unknown"
    assert observation.visuals.zooms_per_minute is None
    assert observation.audio.sfx_per_minute is None
    assert observation.visuals.transitions_per_minute == 1
    assert observation.visuals.broll_density_per_minute == 0
    assert observation.observation_confidence == 0


def test_project_extractor_uses_only_explicit_analyzer_metrics():
    observation = observation_from_analyzed_project("v1", {
        "duration": 30,
        "analysis": {
            "analysis_confidence": 0.84,
            "style_metrics": {
                "hook_duration_seconds": 1.8,
                "captions": {"font_categories": ["condensed sans"], "position": "center"},
                "visuals": {"zooms_per_minute": 4},
                "audio": {"integrated_loudness_lufs": -14.2},
            },
        },
    })
    assert observation.hook_duration_seconds == 1.8
    assert observation.captions.font_categories == ["condensed sans"]
    assert observation.visuals.zooms_per_minute == 4
    assert observation.audio.integrated_loudness_lufs == -14.2
    assert observation.observation_confidence == 0.84


def test_local_store_repository_round_trip_is_owner_scoped(tmp_path):
    profile = aggregate_creator_dna(_request(), [_observation("v1", 2)], now=datetime(2026, 7, 14, tzinfo=timezone.utc))
    repository = CreatorDNARepository(LocalDatabase(tmp_path / "store.json"))

    async def exercise():
        await repository.save(profile)
        assert await repository.get("other-user", profile.profile_id) is None
        loaded = await repository.get("user-1", profile.profile_id)
        assert loaded == profile
        assert [item.profile_id for item in await repository.list("user-1")] == [profile.profile_id]
        assert await repository.delete("user-1", profile.profile_id) is True
        assert await repository.delete("user-1", profile.profile_id) is False

    asyncio.run(exercise())
