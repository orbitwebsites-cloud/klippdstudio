"""Deterministic editorial quality scoring.

This module is intentionally conservative: it scores evidence and flags gaps;
it never pretends a numeric score replaces an editor's judgment.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


RUBRIC_PATH = Path(__file__).resolve().parents[1] / "training" / "editorial_council_v1.json"


def rubric() -> dict[str, Any]:
    return json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))


def assess_project(project: dict[str, Any]) -> dict[str, Any]:
    analysis = project.get("analysis") or {}
    transcript = project.get("transcript") or {}
    words = transcript.get("words") or []
    fillers = analysis.get("filler_indices") or []
    broll = analysis.get("broll_moments") or []
    qa = analysis.get("quality_review") or project.get("post_render_qa") or {}
    qa_issues = qa.get("issues") or []
    markers = project.get("edit_markers") or []
    duration = float(project.get("duration") or 0)
    selected_broll = (project.get("render_options") or {}).get("selected_broll") or []

    checks = []

    def add(check_id: str, status: str, detail: str, evidence: list[str]) -> None:
        checks.append({"id": check_id, "status": status, "detail": detail, "evidence": evidence})

    has_story = bool(analysis.get("title") or analysis.get("summary"))
    add("story_before_style", "pass" if has_story else "needs_review", "The project has an explainable editorial premise." if has_story else "The project needs a title or summary before the team can explain its story arc.", ["title/summary present" if has_story else "title/summary missing"])

    filler_rate = (len(fillers) / len(words) * 100) if words else 0
    performance_status = "needs_review" if filler_rate > 12 else "pass"
    add("performance_before_continuity", performance_status, "Filler candidates are available for human review; high density may flatten delivery." if performance_status != "pass" else "Filler density is within a reviewable range.", [f"{len(fillers)} filler candidates", f"{filler_rate:.1f}% of transcript words"])

    add("rhythm_is_not_cut_count", "needs_review", "Pacing must be judged against the project profile; no universal cut-rate threshold is applied.", [f"{duration:.1f}s duration", f"{len(analysis.get('transitions') or [])} transition cues"])

    broll_status = "pass" if not selected_broll or broll else "needs_review"
    add("meaningful_broll", broll_status, "B-roll is grounded in analyzed moments." if broll_status == "pass" else "Selected B-roll needs an analyzed moment and provenance review.", [f"{len(broll)} analyzed moments", f"{len(selected_broll)} selected assets"])

    add("reviewable_changes", "pass" if markers else "needs_review", "The project has editor-created review anchors." if markers else "Add at least one marker before a major automated pass.", [f"{len(markers)} saved markers"])

    qa_status = "pass" if qa.get("passed") is True and not qa_issues else "needs_review"
    add("delivery_is_part_of_editing", qa_status, "Render QA passed." if qa_status == "pass" else "Render QA is missing, incomplete, or has issues.", [f"{len(qa_issues)} QA issues"])

    score = round(sum(1 for check in checks if check["status"] == "pass") / len(checks) * 100) if checks else 0
    if score >= 80 and all(check["status"] != "needs_review" for check in checks):
        verdict = "ready_for_editor_review"
    elif score >= 50:
        verdict = "needs_editorial_pass"
    else:
        verdict = "not_ready"
    return {"schema_version": "klippd.editorial_quality.v1", "score": score, "verdict": verdict, "checks": checks, "disclaimer": "This score measures evidence and workflow readiness; it is not a substitute for editorial judgment."}
