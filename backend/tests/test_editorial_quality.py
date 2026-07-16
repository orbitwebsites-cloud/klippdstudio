from editorial_quality import assess_project, rubric


def _project():
    return {
        "duration": 42,
        "transcript": {"words": [{"word": f"w{i}", "start": i, "end": i + 0.4} for i in range(20)]},
        "analysis": {
            "title": "A clear hook",
            "summary": "A creator tests a risky build and shows the result.",
            "filler_indices": [2, 7],
            "transitions": [{"word_index": 10, "type": "hard_cut"}],
            "broll_moments": [{"word_index": 10, "query": "test result"}],
            "quality_review": {"passed": True, "issues": []},
        },
        "render_options": {"selected_broll": [{"word_index": 10, "video_url": "https://example.com/clip.mp4"}]},
        "edit_markers": [{"id": "m1", "time": 3.0, "label": "hook"}],
    }


def test_rubric_is_versioned_and_has_anti_slop_rules():
    data = rubric()
    assert data["schema_version"] == "klippd.editorial_council.v1"
    assert len(data["principles"]) >= 8
    assert any("universal" in rule.lower() for rule in data["anti_slop_rules"])


def test_quality_score_rewards_evidence_but_keeps_rhythm_as_review():
    result = assess_project(_project())
    assert result["schema_version"] == "klippd.editorial_quality.v1"
    assert result["score"] >= 60
    assert result["verdict"] == "needs_editorial_pass"
    assert next(item for item in result["checks"] if item["id"] == "rhythm_is_not_cut_count")["status"] == "needs_review"


def test_quality_score_flags_missing_story_and_review_anchor():
    project = _project()
    project["analysis"].pop("summary")
    project["analysis"].pop("title")
    project["edit_markers"] = []
    result = assess_project(project)
    assert next(item for item in result["checks"] if item["id"] == "story_before_style")["status"] == "needs_review"
    assert next(item for item in result["checks"] if item["id"] == "reviewable_changes")["status"] == "needs_review"
