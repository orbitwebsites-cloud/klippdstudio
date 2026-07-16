"""Creator DNA and deterministic edit-chat HTTP integration.

Public reference URLs are provenance only. This module never downloads them and
never fabricates observations; an owned, already-analyzed Klipped project is the
MVP evidence source.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from creator_dna import (
    CreatorDNAAnalysisInput,
    CreatorDNARepository,
    aggregate_creator_dna,
    observation_from_analyzed_project,
)
from edit_chat_engine import EditCommandError, EditSession, JournalEntry, compile_chat_request, validate_command
from editorial_quality import (
    assess_project,
    effective_quality_review,
    post_render_hard_failure_blocked,
    post_render_qa_fingerprint,
    project_has_render,
    rubric,
)


class CreatorProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    references: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    rights_attested: bool
    consent_scope: str = "editing_style_analysis"


class EditPreviewBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str = Field(min_length=1, max_length=1000)
    creator_profile_id: str | None = Field(default=None, max_length=128)


class ApplyPreviewBody(BaseModel):
    preview_id: str = Field(min_length=1, max_length=128)


class VersionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(default="Draft", min_length=1, max_length=80)
    editor_state: dict[str, Any] | None = None


class MarkerBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    time: float = Field(ge=0)
    label: str = Field(min_length=1, max_length=120)
    kind: str = Field(default="note", min_length=1, max_length=32)
    color: str = Field(default="#ccff00", min_length=4, max_length=16)


class PostRenderQAAcknowledgmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledge: Literal[True]
    reason: str = Field(min_length=10, max_length=500)


def _profile_json(profile) -> dict[str, Any]:
    raw = profile.model_dump(mode="json")
    grammar = raw["grammar"]
    evidence = []
    hook = grammar["hook_duration_seconds"]
    if hook.get("value") is not None:
        evidence.append(f"Typical hook: {hook['value']:.1f}s")
    for row in grammar.get("pacing_by_section", [])[:2]:
        value = row["cuts_per_minute"].get("value")
        if value is not None:
            evidence.append(f"{row['section'].title()} pacing: {value:.1f} cuts/min")
    captions = grammar["caption_typography"].get("values", [])
    if captions:
        evidence.append("Caption style: " + ", ".join(captions[:2]))
    transitions = grammar["transition_types"].get("values", [])
    if transitions:
        evidence.append("Transitions: " + ", ".join(transitions[:2]))
    return {
        **raw,
        "id": raw["profile_id"],
        "confidence": raw["overall_confidence"],
        "evidence": evidence,
    }


def _source_payload(reference: dict[str, Any], index: int) -> dict[str, Any]:
    kind = reference.get("type")
    value = str(reference.get("value") or "").strip()
    if not value:
        raise HTTPException(422, "Every Creator DNA reference needs a value")
    base = {
        "source_id": f"source_{index + 1}",
        "analyzed_with_consent": True,
        "rights_basis": "explicit_permission" if kind == "url" else "owned",
    }
    if kind == "url":
        return {**base, "kind": "reference_url", "url": value}
    if kind in {"owned_upload", "owned_project"}:
        return {**base, "kind": "upload", "asset_id": value}
    raise HTTPException(422, "Reference type must be url, owned_project, or owned_upload")


def _state_from_project(project: dict[str, Any], profiles: list[dict[str, Any]]) -> dict[str, Any]:
    analysis = copy.deepcopy(project.get("analysis") or {})
    assets = []
    for item in [*analysis.get("resolved_pack_assets", []), *analysis.get("generated_assets", [])]:
        if isinstance(item, dict) and item.get("id"):
            assets.append({**copy.deepcopy(item), "approved": True})
    return {
        "transcript": copy.deepcopy(project.get("transcript") or {"words": []}),
        "analysis": analysis,
        "render_options": copy.deepcopy(project.get("render_options") or project.get("chat_render_options") or {}),
        "timeline": copy.deepcopy(project.get("chat_timeline") or {}),
        "asset_library": assets,
        "available_creator_profiles": profiles,
        "creator_profile": project.get("creator_profile_id"),
    }


def _session_dump(session: EditSession) -> dict[str, Any]:
    entry = lambda value: {"command_json": value.command_json, "before_json": value.before_json, "after_json": value.after_json}
    return {
        "state_json": session.state_json,
        "history": [entry(value) for value in session.history],
        "redo_stack": [entry(value) for value in session.redo_stack],
        "seen_command_ids": sorted(session.seen_command_ids),
    }


def _session_load(data: dict[str, Any] | None, fallback: dict[str, Any]) -> EditSession:
    if not data:
        return EditSession.create(fallback)
    make = lambda row: JournalEntry(row["command_json"], row["before_json"], row["after_json"])
    return EditSession(
        data["state_json"], tuple(make(x) for x in data.get("history", [])),
        tuple(make(x) for x in data.get("redo_stack", [])), frozenset(data.get("seen_command_ids", [])),
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _editable_hash(state: dict[str, Any]) -> str:
    editable = {key: state.get(key) for key in ("analysis", "render_options", "timeline", "creator_profile")}
    return hashlib.sha256(_canonical(editable).encode()).hexdigest()


def _operation_summary(operation: dict[str, Any]) -> str:
    labels = {
        "set_hook_pacing": "Adjust hook pacing", "set_captions": "Update captions",
        "set_word_cut": "Update transcript cuts", "set_broll": "Update B-roll",
        "set_emphasis": "Add visual emphasis", "set_timeline_cue": "Update timeline cue",
        "set_render_format": "Change output format", "select_creator_profile": "Select Creator DNA",
    }
    return labels.get(operation.get("type"), "Edit timeline")


def _matched_count(result: Any) -> int:
    if isinstance(result, dict):
        return int(result.get("matched_count") or 0)
    return int(getattr(result, "matched_count", 0) or 0)


async def _revisioned_project_update(
    db,
    pid: str,
    revision_field: str,
    mutate: Callable[[dict[str, Any]], tuple[dict[str, Any], Any]],
    initial_project: dict[str, Any],
) -> Any:
    """Apply a project mutation with optimistic concurrency control."""
    project = initial_project
    for _ in range(8):
        revision_token = project.get(revision_field)
        revision = revision_token if type(revision_token) is int and revision_token >= 0 else 0
        fields, response = mutate(project)
        result = await db.projects.update_one(
            {"id": pid, revision_field: revision_token},
            {"$set": {
                **fields,
                revision_field: revision + 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        if _matched_count(result):
            return response
        project = await db.projects.find_one({"id": pid}, {"_id": 0})
        if not project:
            raise HTTPException(404, "Project not found")
    raise HTTPException(409, "The project changed repeatedly; retry the request")


def register_premium_routes(api, db, user_id, get_project, update_project, require_plan=None) -> None:
    repository = CreatorDNARepository(db)

    def uid() -> str:
        return user_id() if callable(user_id) else user_id

    def require_pro() -> None:
        if require_plan:
            require_plan("pro")

    @api.get("/creator-profiles")
    async def list_creator_profiles():
        require_pro()
        return {"profiles": [_profile_json(item) for item in await repository.list(uid())]}

    @api.post("/creator-profiles/analyze")
    @api.post("/creator-profiles")
    async def analyze_creator_profile(body: CreatorProfileBody):
        require_pro()
        if not body.rights_attested or body.consent_scope != "editing_style_analysis":
            raise HTTPException(422, "Creator DNA requires rights, consent, and the editing-style analysis scope")
        sources = [_source_payload(item, index) for index, item in enumerate(body.references)]
        try:
            request = CreatorDNAAnalysisInput.model_validate({
                "owner_id": uid(), "profile_name": body.name,
                "sources": sources, "consent_confirmed": True,
            })
            observations = []
            for source in request.sources:
                if source.asset_id:
                    project = await db.projects.find_one({"id": source.asset_id, "user_id": uid()}, {"_id": 0})
                    if not project:
                        raise HTTPException(422, f"Owned project {source.asset_id!r} was not found")
                    observations.append(observation_from_analyzed_project(source.source_id, project))
        except ValidationError as exc:
            raise HTTPException(422, detail=exc.errors(include_url=False, include_context=False)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not observations:
            raise HTTPException(422, "URL references need imported, analyzed examples. Use an owned Klipped project ID for the MVP; URLs are never scored without evidence.")
        try:
            profile = aggregate_creator_dna(request, observations)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        await repository.save(profile)
        return {"profile": _profile_json(profile)}

    async def context(pid: str):
        require_pro()
        project = await get_project(pid)
        profiles = [_profile_json(item) for item in await repository.list(uid())]
        chat = copy.deepcopy(project.get("edit_chat") or {})
        fallback = _state_from_project(project, profiles)
        saved = _session_load(chat.get("session"), fallback)
        # Always compile/apply against the current project. Keep the immutable
        # journal, but never let its old state snapshot overwrite newer edits.
        session = EditSession(
            _canonical(fallback), saved.history, saved.redo_stack, saved.seen_command_ids
        )
        return project, profiles, chat, session

    @api.get("/projects/{pid}/editorial-team/review")
    async def editorial_team_review(pid: str):
        """Return deterministic, evidence-backed notes from the editorial team.

        The team is intentionally a review layer over analyzed project data. It
        does not invent footage decisions or silently mutate the edit; every
        note includes the evidence it used and a supported edit-chat prompt.
        """
        project = await get_project(pid)
        analysis = project.get("analysis") or {}
        transcript = project.get("transcript") or {}
        words = transcript.get("words") or []
        fillers = analysis.get("filler_indices") or []
        broll = analysis.get("broll_moments") or []
        transitions = analysis.get("transitions") or []
        audio_cues = analysis.get("audio_cues") or []
        qa, _ = effective_quality_review(project)
        qa_issues = qa.get("issues") or []
        duration = float(project.get("duration") or 0)
        word_count = len(words)
        filler_rate = round(len(fillers) / word_count * 100, 1) if word_count else 0

        def note(note_id, role, title, detail, evidence, prompt, priority="review"):
            return {
                "id": note_id, "role": role, "title": title, "detail": detail,
                "evidence": evidence, "prompt": prompt, "priority": priority,
            }

        team = [
            {
                "id": "story",
                "name": "Story Editor",
                "editorial_lens": "Emotion, intention, and cause-and-effect",
                "color": "#ccff00",
                "notes": [],
            },
            {
                "id": "rhythm",
                "name": "Rhythm Editor",
                "editorial_lens": "Pacing, silence, music, and audience attention",
                "color": "#00d9ff",
                "notes": [],
            },
            {
                "id": "performance",
                "name": "Performance Editor",
                "editorial_lens": "Best take, clarity, and natural delivery",
                "color": "#ffb000",
                "notes": [],
            },
            {
                "id": "visual",
                "name": "Visual Editor",
                "editorial_lens": "B-roll meaning, visual continuity, and emphasis",
                "color": "#ff4f8b",
                "notes": [],
            },
            {
                "id": "finishing",
                "name": "Finishing Editor",
                "editorial_lens": "Captions, audio, safe zones, rights, and delivery",
                "color": "#a78bfa",
                "notes": [],
            },
        ]

        team[0]["notes"].append(note(
            "story-context", "Story Editor", "Make the promise visible early",
            "The first pass should make the subject and payoff legible before decorative treatment is added.",
            [f"{word_count} transcript words", f"{duration:.1f}s source duration"],
            "Tighten the opening around the clearest hook and keep the payoff readable.",
            "high" if word_count and duration > 0 else "review",
        ))
        if not analysis.get("summary"):
            team[0]["notes"].append(note(
                "story-summary", "Story Editor", "Add a reviewable story summary",
                "Analysis did not produce a summary, so the team cannot explain the cut's intended arc yet.",
                ["analysis.summary is missing"],
                "Set a clear hook and payoff before changing the visual style.", "high",
            ))

        team[1]["notes"].append(note(
            "rhythm-silence", "Rhythm Editor", "Do not confuse speed with rhythm",
            "Review pauses and transitions as a sequence. A high cut count is not automatically better.",
            [f"{len(transitions)} detected transition cues", f"{len(audio_cues)} detected audio cues"],
            "Review pacing around the hook and payoff; preserve intentional pauses and only tighten dead air.",
            "review",
        ))
        if duration > 0 and duration < 15:
            team[1]["notes"].append(note(
                "rhythm-short", "Rhythm Editor", "Short-form timing needs a clean landing",
                "This is a short source; protect the final beat instead of stacking more effects into it.",
                [f"{duration:.1f}s duration"],
                "Keep the final payoff intact and remove only pauses that do not carry meaning.", "medium",
            ))

        team[2]["notes"].append(note(
            "performance-filler", "Performance Editor", "Review filler cuts by performance",
            "Filler removal is a suggestion, not a license to flatten the speaker's timing or personality.",
            [f"{len(fillers)} filler candidates", f"{filler_rate}% of transcript words flagged"],
            "Remove only filler words that interrupt the thought; restore any cut that damages the performance.",
            "high" if filler_rate > 8 else "review",
        ))

        team[3]["notes"].append(note(
            "visual-evidence", "Visual Editor", "Every insert needs a reason",
            "B-roll should clarify, contrast, or intensify the spoken idea; empty coverage is not a creative decision.",
            [f"{len(broll)} B-roll moments detected"],
            "Review each B-roll moment and keep only inserts that add information or emotional emphasis.",
            "high" if broll else "review",
        ))

        quality = assess_project(project)
        finishing_priority = "high" if qa_issues else "review"
        finishing_detail = (
            "Hard post-render QA failures block publish-ready delivery and download until they are fixed or explicitly acknowledged."
            if quality["delivery_blocked"] else
            "The final pass should verify captions, audio, safe zones, and asset provenance before delivery."
        )
        team[4]["notes"].append(note(
            "finishing-delivery", "Finishing Editor", "Prove the export is publishable",
            finishing_detail,
            [f"{len(qa_issues)} QA issues recorded", f"{len(broll)} B-roll moments with provenance review"],
            "Run the final render QA, fix any caption or audio issue, and confirm every external asset is cleared.",
            finishing_priority,
        ))

        timeline_events = []
        for index in fillers:
            if 0 <= index < len(words):
                word = words[index]
                timeline_events.append({"time": float(word.get("start") or 0), "type": "filler", "label": str(word.get("word") or "filler")})
        for moment in broll:
            word_index = moment.get("word_index")
            if isinstance(word_index, int) and 0 <= word_index < len(words):
                timeline_events.append({"time": float(words[word_index].get("start") or 0), "type": "broll", "label": str(moment.get("query") or "B-roll")})
        for cue in [*transitions, *audio_cues]:
            word_index = cue.get("word_index")
            if isinstance(word_index, int) and 0 <= word_index < len(words):
                timeline_events.append({"time": float(words[word_index].get("start") or 0), "type": cue.get("type") or "cue", "label": str(cue.get("label") or cue.get("cue_type") or cue.get("type") or "cue")})
        timeline_events.sort(key=lambda item: item["time"])

        return {
            "project_id": pid,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "delivery_blocked" if quality["delivery_blocked"] else "review_ready",
            "quality": quality,
            "rubric": {"schema_version": rubric().get("schema_version"), "principle_count": len(rubric().get("principles") or [])},
            "team": team,
            "timeline": {"duration": duration, "events": timeline_events[:200]},
            "principles": [
                "Evidence before assertion",
                "Review before apply",
                "Performance before cosmetic continuity",
                "Meaning before decoration",
                "Quality gates before delivery",
            ],
        }

    @api.post("/projects/{pid}/post-render-qa/acknowledge")
    async def acknowledge_post_render_qa(pid: str, body: PostRenderQAAcknowledgmentBody):
        project = await get_project(pid)
        review = project.get("post_render_qa")
        reason = body.reason.strip()
        if len(reason) < 10:
            raise HTTPException(422, "Acknowledgment reason must contain at least 10 non-whitespace characters")
        if not project_has_render(project) or not isinstance(review, dict):
            raise HTTPException(409, "A completed post-render QA review is required")
        if review.get("hard_fail") is not True:
            raise HTTPException(409, "The current post-render QA review has no hard failure to acknowledge")

        acknowledgment = {
            "schema_version": "klippd.post_render_qa_acknowledgment.v1",
            "acknowledged": True,
            "qa_fingerprint": post_render_qa_fingerprint(review),
            "reason": reason,
            "acknowledged_at": datetime.now(timezone.utc).isoformat(),
            "issue_codes": [
                str(issue.get("code"))
                for issue in review.get("issues") or []
                if isinstance(issue, dict) and issue.get("code")
            ],
        }
        await update_project(
            pid,
            post_render_qa_acknowledgment=acknowledgment,
            render_review_required=False,
        )
        updated = {**project, "post_render_qa_acknowledgment": acknowledgment, "render_review_required": False}
        return {
            "acknowledgment": acknowledgment,
            "download_allowed": not post_render_hard_failure_blocked(updated),
            "quality": assess_project(updated),
        }

    @api.get("/projects/{pid}/edit-versions")
    async def list_edit_versions(pid: str):
        project = await get_project(pid)
        return {"versions": project.get("edit_versions") or []}

    @api.post("/projects/{pid}/edit-versions")
    async def save_edit_version(pid: str, body: VersionBody):
        project = await get_project(pid)
        snapshot = {
            key: copy.deepcopy(project.get(key))
            for key in ("analysis", "render_options", "edit_options", "chat_render_options", "chat_timeline", "creator_profile_id")
        }
        if body.editor_state is not None:
            snapshot["edit_options"] = copy.deepcopy(body.editor_state)
            if "creator_profile_id" in body.editor_state:
                snapshot["creator_profile_id"] = copy.deepcopy(body.editor_state["creator_profile_id"])
        version = {
            "id": "version_" + uuid.uuid4().hex[:16],
            "name": body.name.strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "snapshot": snapshot,
        }

        def append_version(current: dict[str, Any]):
            versions = [*list(current.get("edit_versions") or []), version][-20:]
            return {"edit_versions": versions}, versions

        versions = await _revisioned_project_update(
            db, pid, "edit_versions_revision", append_version, project
        )
        return {"version": version, "versions": versions}

    @api.post("/projects/{pid}/edit-versions/{version_id}/restore")
    async def restore_edit_version(pid: str, version_id: str):
        project = await get_project(pid)
        version = next((item for item in project.get("edit_versions") or [] if item.get("id") == version_id), None)
        if not version:
            raise HTTPException(404, "Edit version not found")
        snapshot = version.get("snapshot") or {}
        await update_project(pid, **{key: copy.deepcopy(snapshot.get(key)) for key in ("analysis", "render_options", "edit_options", "chat_render_options", "chat_timeline", "creator_profile_id")})
        return {"status": "restored", "version": version, "project": await get_project(pid)}

    @api.get("/projects/{pid}/markers")
    async def list_markers(pid: str):
        project = await get_project(pid)
        return {"markers": project.get("edit_markers") or []}

    @api.post("/projects/{pid}/markers")
    async def create_marker(pid: str, body: MarkerBody):
        project = await get_project(pid)
        duration = float(project.get("duration") or 0)
        if duration and body.time > duration:
            raise HTTPException(422, f"Marker time must be within the {duration:.2f}s project")
        marker = {
            "id": "marker_" + uuid.uuid4().hex[:16],
            **body.model_dump(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        def append_marker(current: dict[str, Any]):
            markers = [*list(current.get("edit_markers") or []), marker]
            markers.sort(key=lambda item: float(item.get("time") or 0))
            markers = markers[-200:]
            return {"edit_markers": markers}, markers

        markers = await _revisioned_project_update(
            db, pid, "edit_markers_revision", append_marker, project
        )
        return {"marker": marker, "markers": markers}

    @api.delete("/projects/{pid}/markers/{marker_id}")
    async def delete_marker(pid: str, marker_id: str):
        project = await get_project(pid)

        def remove_marker(current: dict[str, Any]):
            existing = list(current.get("edit_markers") or [])
            markers = [item for item in existing if item.get("id") != marker_id]
            if len(markers) == len(existing):
                raise HTTPException(404, "Marker not found")
            return {"edit_markers": markers}, markers

        markers = await _revisioned_project_update(
            db, pid, "edit_markers_revision", remove_marker, project
        )
        return {"status": "deleted", "markers": markers}

    @api.get("/projects/{pid}/edit-chat/history")
    async def edit_chat_history(pid: str):
        _, _, chat, session = await context(pid)
        return {"messages": chat.get("messages", []), "can_undo": bool(session.history), "can_redo": bool(session.redo_stack)}

    @api.post("/projects/{pid}/edit-chat/preview")
    async def edit_chat_preview(pid: str, body: EditPreviewBody):
        _, profiles, chat, session = await context(pid)
        try:
            creator_grammar = None
            if body.creator_profile_id:
                if body.creator_profile_id not in {item["id"] for item in profiles}:
                    raise HTTPException(422, "Select a saved Creator DNA profile")
                selected_profile = await repository.get(uid(), body.creator_profile_id)
                if not selected_profile:
                    raise HTTPException(422, "Select a saved Creator DNA profile")
                creator_grammar = selected_profile.grammar.model_dump(mode="json")
            command = compile_chat_request(body.message, session.state, creator_grammar=creator_grammar)
            if body.creator_profile_id:
                command["operations"].insert(0, {"type": "select_creator_profile", "profile_id": body.creator_profile_id})
                command = validate_command(command)
            preview = session.preview(command)
        except EditCommandError as exc:
            raise HTTPException(422, detail=exc.as_dict()) from exc
        preview_id = "preview_" + uuid.uuid4().hex[:20]
        base_hash = _editable_hash(session.state)
        previews = chat.get("previews", {})
        previews[preview_id] = {"command": command, "base_hash": base_hash, "created_at": datetime.now(timezone.utc).isoformat()}
        chat["previews"] = dict(list(previews.items())[-10:])
        chat.setdefault("messages", []).append({"id": uuid.uuid4().hex, "role": "user", "content": body.message})
        await update_project(pid, edit_chat=chat)
        return {
            "preview_id": preview_id, "changed": preview["changed"], "changes": preview["changes"],
            "operations": [{**op, "summary": _operation_summary(op)} for op in command["operations"]],
        }

    @api.post("/projects/{pid}/edit-chat/apply")
    async def edit_chat_apply(pid: str, body: ApplyPreviewBody):
        _, _, chat, session = await context(pid)
        saved = (chat.get("previews") or {}).get(body.preview_id)
        if not saved:
            raise HTTPException(404, "Edit preview expired; preview the request again")
        current_hash = _editable_hash(session.state)
        if saved["base_hash"] != current_hash:
            raise HTTPException(409, "The project changed; preview this edit again")
        try:
            session, result = session.apply(saved["command"])
        except EditCommandError as exc:
            raise HTTPException(422, detail=exc.as_dict()) from exc
        state = session.state
        chat["session"] = _session_dump(session)
        chat["previews"] = {}
        chat.setdefault("messages", []).append({"id": uuid.uuid4().hex, "role": "assistant", "content": f"Applied {len(saved['command']['operations'])} timeline operation(s)."})
        await update_project(pid, edit_chat=chat, analysis=state["analysis"], render_options=state["render_options"], chat_render_options=state["render_options"], chat_timeline=state["timeline"], creator_profile_id=state.get("creator_profile"))
        return {"status": result["status"], "can_undo": True, "can_redo": False, "project": state}

    async def change_history(pid: str, direction: str):
        _, _, chat, session = await context(pid)
        try:
            if direction == "undo" and session.history:
                expected = json.loads(session.history[-1].after_json)
                if _editable_hash(session.state) != _editable_hash(expected):
                    raise EditCommandError("project_changed", "The project changed after that edit, so undo was stopped to protect newer work.")
            if direction == "redo" and session.redo_stack:
                expected = json.loads(session.redo_stack[-1].before_json)
                if _editable_hash(session.state) != _editable_hash(expected):
                    raise EditCommandError("project_changed", "The project changed after undo, so redo was stopped to protect newer work.")
            session = session.undo() if direction == "undo" else session.redo()
        except EditCommandError as exc:
            raise HTTPException(409, detail=exc.as_dict()) from exc
        state = session.state
        chat["session"] = _session_dump(session)
        chat.setdefault("messages", []).append({"id": uuid.uuid4().hex, "role": "assistant", "content": "Last edit undone." if direction == "undo" else "Edit restored."})
        await update_project(pid, edit_chat=chat, analysis=state["analysis"], render_options=state["render_options"], chat_render_options=state["render_options"], chat_timeline=state["timeline"], creator_profile_id=state.get("creator_profile"))
        return {"can_undo": bool(session.history), "can_redo": bool(session.redo_stack), "project": state}

    @api.post("/projects/{pid}/edit-chat/undo")
    async def edit_chat_undo(pid: str):
        return await change_history(pid, "undo")

    @api.post("/projects/{pid}/edit-chat/redo")
    async def edit_chat_redo(pid: str):
        return await change_history(pid, "redo")
