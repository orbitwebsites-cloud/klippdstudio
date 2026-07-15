"""Consent-based, identity-safe reusable editing grammar profiles.

This module deliberately performs no network access.  Reference URLs are provenance
records only; callers must provide observations produced from user-supplied or
otherwise authorized media.
"""
from __future__ import annotations

import hashlib
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceKind(str, Enum):
    REFERENCE_URL = "reference_url"
    UPLOAD = "upload"


class RightsBasis(str, Enum):
    OWNED = "owned"
    LICENSED = "licensed"
    EXPLICIT_PERMISSION = "explicit_permission"
    PUBLIC_REFERENCE_ONLY = "public_reference_only"


class ProvenanceSource(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_id: str = Field(min_length=1, max_length=128)
    kind: SourceKind
    rights_basis: RightsBasis
    url: Optional[str] = None
    asset_id: Optional[str] = Field(default=None, max_length=256)
    rights_note: Optional[str] = Field(default=None, max_length=1000)
    analyzed_with_consent: bool = True

    @model_validator(mode="after")
    def validate_provenance(self):
        if not self.analyzed_with_consent:
            raise ValueError("each source requires affirmative analysis consent")
        if self.kind == SourceKind.REFERENCE_URL:
            if not self.url or self.asset_id:
                raise ValueError("reference_url sources require url and cannot claim an uploaded asset")
            parsed = urlparse(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("reference URLs must be absolute http(s) URLs")
            if parsed.username or parsed.password:
                raise ValueError("credentials are not allowed in reference URLs")
        else:
            if not self.asset_id or self.url:
                raise ValueError("upload sources require asset_id and must not include a remote URL")
            if self.rights_basis == RightsBasis.PUBLIC_REFERENCE_ONLY:
                raise ValueError("uploads must be owned, licensed, or explicitly permitted")
        return self


class CreatorDNAAnalysisInput(BaseModel):
    """Validated job input. Validation never dereferences a source URL."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    owner_id: str = Field(min_length=1, max_length=128)
    profile_name: str = Field(min_length=1, max_length=120)
    sources: list[ProvenanceSource] = Field(min_length=1, max_length=20)
    consent_confirmed: bool
    purpose: str = Field(default="generalized_editing_grammar", max_length=200)

    @model_validator(mode="after")
    def validate_job(self):
        if not self.consent_confirmed:
            raise ValueError("consent_confirmed must be true")
        if self.purpose != "generalized_editing_grammar":
            raise ValueError("only generalized_editing_grammar analysis is supported")
        ids = [source.source_id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("source_id values must be unique")
        return self


class SectionPacing(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    section: str = Field(min_length=1, max_length=60)
    cuts_per_minute: float = Field(ge=0, le=600)
    average_shot_seconds: float = Field(gt=0, le=600)
    evidence_count: int = Field(default=1, ge=1)


class CaptionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    font_categories: list[str] = Field(default_factory=list, max_length=8)
    position: str = Field(default="unknown", max_length=60)
    case_style: str = Field(default="mixed", max_length=40)
    words_per_card: Optional[float] = Field(default=None, gt=0, le=100)
    highlighted_word_ratio: Optional[float] = Field(default=None, ge=0, le=1)
    highlight_behaviors: list[str] = Field(default_factory=list, max_length=12)


class AudioObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sfx_per_minute: Optional[float] = Field(default=None, ge=0, le=600)
    music_present: Optional[bool] = None
    integrated_loudness_lufs: Optional[float] = Field(default=None, ge=-70, le=0)
    ducking_behavior: str = Field(default="unknown", max_length=80)


class VisualObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    broll_density_per_minute: Optional[float] = Field(default=None, ge=0, le=600)
    graphic_density_per_minute: Optional[float] = Field(default=None, ge=0, le=600)
    transitions_per_minute: Optional[float] = Field(default=None, ge=0, le=600)
    transition_types: list[str] = Field(default_factory=list, max_length=20)
    zooms_per_minute: Optional[float] = Field(default=None, ge=0, le=600)
    motion_patterns: list[str] = Field(default_factory=list, max_length=20)
    color_traits: list[str] = Field(default_factory=list, max_length=20)
    framing_traits: list[str] = Field(default_factory=list, max_length=20)


class VideoStyleObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_id: str = Field(min_length=1, max_length=128)
    duration_seconds: float = Field(gt=0, le=86400)
    hook_duration_seconds: Optional[float] = Field(default=None, ge=0, le=300)
    section_pacing: list[SectionPacing] = Field(default_factory=list, max_length=30)
    captions: CaptionObservation = Field(default_factory=CaptionObservation)
    audio: AudioObservation = Field(default_factory=AudioObservation)
    visuals: VisualObservation = Field(default_factory=VisualObservation)
    story_beats: list[str] = Field(default_factory=list, max_length=30)
    observation_confidence: float = Field(ge=0, le=1)
    evidence_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_timing(self):
        if self.hook_duration_seconds is not None and self.hook_duration_seconds > self.duration_seconds:
            raise ValueError("hook duration cannot exceed video duration")
        return self


def observation_from_analyzed_project(source_id: str, project: dict[str, Any]) -> VideoStyleObservation:
    """Build an observation only from measurements present in an analyzed project.

    No visual characteristic is guessed from a generic preset. ``style_metrics``
    is the preferred analyzer contract; a small set of concrete event lists can
    also provide count/type evidence. Missing metrics remain missing.
    """
    analysis = project.get("analysis")
    if not isinstance(analysis, dict):
        raise ValueError("owned project must have analysis evidence")
    transcript = project.get("transcript") if isinstance(project.get("transcript"), dict) else {}
    words = transcript.get("words") if isinstance(transcript.get("words"), list) else []
    raw_duration = project.get("duration")
    if not isinstance(raw_duration, (int, float)) and words:
        raw_duration = words[-1].get("end") if isinstance(words[-1], dict) else None
    if not isinstance(raw_duration, (int, float)) or raw_duration <= 0:
        raise ValueError("owned project must have a measured positive duration")
    duration = float(raw_duration)
    metrics = analysis.get("style_metrics") if isinstance(analysis.get("style_metrics"), dict) else {}
    evidence_count = 0

    payload: dict[str, Any] = {
        "source_id": source_id,
        "duration_seconds": duration,
        "captions": {},
        "visuals": {},
        "audio": {},
        "story_beats": [],
    }
    hook = metrics.get("hook_duration_seconds")
    if isinstance(hook, (int, float)):
        payload["hook_duration_seconds"] = hook
        evidence_count += 1

    section_pacing = metrics.get("section_pacing")
    if isinstance(section_pacing, list) and section_pacing:
        payload["section_pacing"] = section_pacing
        evidence_count += sum(max(1, row.get("evidence_count", 1)) for row in section_pacing if isinstance(row, dict))

    captions = metrics.get("captions") if isinstance(metrics.get("captions"), dict) else {}
    for key in ("font_categories", "position", "case_style", "words_per_card", "highlighted_word_ratio", "highlight_behaviors"):
        if key in captions and captions[key] is not None:
            payload["captions"][key] = captions[key]
            evidence_count += len(captions[key]) if isinstance(captions[key], list) else 1

    visuals = metrics.get("visuals") if isinstance(metrics.get("visuals"), dict) else {}
    for key in ("broll_density_per_minute", "graphic_density_per_minute", "transitions_per_minute", "transition_types", "zooms_per_minute", "motion_patterns", "color_traits", "framing_traits"):
        if key in visuals and visuals[key] is not None:
            payload["visuals"][key] = visuals[key]
            evidence_count += len(visuals[key]) if isinstance(visuals[key], list) else 1

    # Concrete event lists are valid count/type evidence, including an explicit
    # empty list (which measures a zero rate). They are never reinterpreted as a
    # different effect; for example, emphasis markers are not assumed to be zooms.
    if "broll_moments" in analysis and isinstance(analysis["broll_moments"], list) and "broll_density_per_minute" not in payload["visuals"]:
        payload["visuals"]["broll_density_per_minute"] = len(analysis["broll_moments"]) * 60.0 / duration
        evidence_count += max(1, len(analysis["broll_moments"]))
    if "transitions" in analysis and isinstance(analysis["transitions"], list):
        transitions = analysis["transitions"]
        if "transitions_per_minute" not in payload["visuals"]:
            payload["visuals"]["transitions_per_minute"] = len(transitions) * 60.0 / duration
            evidence_count += max(1, len(transitions))
        if "transition_types" not in payload["visuals"]:
            types = [item["type"] for item in transitions if isinstance(item, dict) and isinstance(item.get("type"), str) and item["type"].strip()]
            if types:
                payload["visuals"]["transition_types"] = types
                evidence_count += len(types)

    audio = metrics.get("audio") if isinstance(metrics.get("audio"), dict) else {}
    for key in ("sfx_per_minute", "music_present", "integrated_loudness_lufs", "ducking_behavior"):
        if key in audio and audio[key] is not None:
            payload["audio"][key] = audio[key]
            evidence_count += 1

    metric_beats = metrics.get("story_beats")
    if isinstance(metric_beats, list):
        payload["story_beats"] = metric_beats
        evidence_count += len(metric_beats)
    elif isinstance(analysis.get("story_beats"), list):
        payload["story_beats"] = [
            item["beat_type"] for item in analysis["story_beats"]
            if isinstance(item, dict) and isinstance(item.get("beat_type"), str) and item["beat_type"].strip()
        ]
        evidence_count += len(payload["story_beats"])

    if evidence_count <= 0:
        raise ValueError("owned project analysis has no measurable editing-style evidence")
    confidence = analysis.get("analysis_confidence")
    payload["observation_confidence"] = float(confidence) if isinstance(confidence, (int, float)) else 0.0
    payload["evidence_count"] = evidence_count
    return VideoStyleObservation.model_validate(payload)


class MetricEstimate(BaseModel):
    value: Optional[float] = None
    confidence: float = Field(ge=0, le=1)
    evidence_count: int = Field(ge=0)
    sample_count: int = Field(ge=0)
    outliers_removed: int = Field(ge=0)


class PacingEstimate(BaseModel):
    section: str
    cuts_per_minute: MetricEstimate
    average_shot_seconds: MetricEstimate


class PatternEstimate(BaseModel):
    values: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    evidence_count: int = Field(ge=0)


class EditingGrammar(BaseModel):
    hook_duration_seconds: MetricEstimate
    pacing_by_section: list[PacingEstimate]
    caption_typography: PatternEstimate
    caption_position: PatternEstimate
    caption_case: PatternEstimate
    caption_words_per_card: MetricEstimate
    caption_highlight_ratio: MetricEstimate
    caption_highlight_behavior: PatternEstimate
    broll_density_per_minute: MetricEstimate
    graphic_density_per_minute: MetricEstimate
    transitions_per_minute: MetricEstimate
    transition_types: PatternEstimate
    zooms_per_minute: MetricEstimate
    motion_patterns: PatternEstimate
    sfx_per_minute: MetricEstimate
    integrated_loudness_lufs: MetricEstimate
    music_behavior: PatternEstimate
    ducking_behavior: PatternEstimate
    story_beats: PatternEstimate
    color_traits: PatternEstimate
    framing_traits: PatternEstimate


class CreatorDNAProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "klippd.creator_dna.v1"
    profile_id: str
    owner_id: str
    name: str
    source_provenance: list[ProvenanceSource]
    source_count: int
    grammar: EditingGrammar
    overall_confidence: float = Field(ge=0, le=1)
    safety: dict[str, Any]
    created_at: datetime
    updated_at: datetime


def _clean_label(value: str) -> str:
    return " ".join(value.strip().lower().split())[:80]


def _numeric_metric(rows: Iterable[tuple[float | None, float, int]]) -> MetricEstimate:
    samples = [(float(value), confidence, evidence) for value, confidence, evidence in rows if value is not None]
    if not samples:
        return MetricEstimate(confidence=0, evidence_count=0, sample_count=0, outliers_removed=0)
    values = [row[0] for row in samples]
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations)
    if len(samples) >= 4:
        if mad > 0:
            kept = [row for row in samples if abs(row[0] - median) / (1.4826 * mad) <= 3.5]
        else:
            kept = [row for row in samples if row[0] == median]
    else:
        kept = samples
    if not kept:  # defensive; median itself should always survive
        kept = samples
    weight_sum = sum(max(0.01, confidence) * max(1, evidence) for _, confidence, evidence in kept)
    value = sum(value * max(0.01, confidence) * max(1, evidence) for value, confidence, evidence in kept) / weight_sum
    evidence = sum(row[2] for row in kept)
    base_confidence = sum(row[1] * row[2] for row in kept) / max(1, evidence)
    coverage = min(1.0, len(kept) / 3.0)
    agreement = 1.0 if len(kept) == 1 else 1.0 / (1.0 + statistics.pstdev([row[0] for row in kept]) / max(abs(value), 1.0))
    confidence = max(0.0, min(1.0, base_confidence * (0.55 + 0.45 * coverage) * agreement))
    return MetricEstimate(
        value=round(value, 4), confidence=round(confidence, 4), evidence_count=evidence,
        sample_count=len(kept), outliers_removed=len(samples) - len(kept),
    )


def _patterns(rows: Iterable[tuple[Iterable[str], float, int]], limit: int = 5) -> PatternEstimate:
    scores: defaultdict[str, float] = defaultdict(float)
    evidence = 0
    confidence_weight = 0.0
    for values, confidence, count in rows:
        normalized = list(dict.fromkeys(_clean_label(value) for value in values if _clean_label(value)))
        if not normalized:
            continue
        evidence += count
        confidence_weight += confidence * count
        for value in normalized:
            scores[value] += max(0.01, confidence) * count
    if not scores:
        return PatternEstimate(confidence=0, evidence_count=0)
    ordered = sorted(scores, key=lambda item: (-scores[item], item))[:limit]
    agreement = scores[ordered[0]] / max(sum(scores.values()), 0.01)
    base = confidence_weight / max(1, evidence)
    return PatternEstimate(values=ordered, confidence=round(min(1.0, base * (0.65 + 0.35 * agreement)), 4), evidence_count=evidence)


def _single_pattern(rows: Iterable[tuple[str, float, int]]) -> PatternEstimate:
    return _patterns((([value], confidence, count) for value, confidence, count in rows if value and value != "unknown"), limit=3)


def aggregate_creator_dna(
    request: CreatorDNAAnalysisInput,
    observations: list[VideoStyleObservation],
    *,
    now: datetime | None = None,
) -> CreatorDNAProfile:
    """Aggregate authorized observations into a generalized editing grammar."""
    if not observations:
        raise ValueError("at least one video observation is required")
    source_ids = {source.source_id for source in request.sources}
    observed_ids = [observation.source_id for observation in observations]
    if len(observed_ids) != len(set(observed_ids)):
        raise ValueError("only one observation per source_id is allowed")
    unknown = sorted(set(observed_ids) - source_ids)
    if unknown:
        raise ValueError(f"observations reference undeclared sources: {', '.join(unknown)}")

    rows = lambda getter: [(getter(item), item.observation_confidence, item.evidence_count) for item in observations]
    pacing: defaultdict[str, list[tuple[SectionPacing, VideoStyleObservation]]] = defaultdict(list)
    for observation in observations:
        for section in observation.section_pacing:
            pacing[_clean_label(section.section)].append((section, observation))
    pacing_estimates = []
    for section_name in sorted(pacing):
        section_rows = pacing[section_name]
        pacing_estimates.append(PacingEstimate(
            section=section_name,
            cuts_per_minute=_numeric_metric((section.cuts_per_minute, obs.observation_confidence, section.evidence_count) for section, obs in section_rows),
            average_shot_seconds=_numeric_metric((section.average_shot_seconds, obs.observation_confidence, section.evidence_count) for section, obs in section_rows),
        ))

    grammar = EditingGrammar(
        hook_duration_seconds=_numeric_metric(rows(lambda item: item.hook_duration_seconds)),
        pacing_by_section=pacing_estimates,
        caption_typography=_patterns((item.captions.font_categories, item.observation_confidence, item.evidence_count) for item in observations),
        caption_position=_single_pattern((item.captions.position, item.observation_confidence, item.evidence_count) for item in observations),
        caption_case=_single_pattern((item.captions.case_style, item.observation_confidence, item.evidence_count) for item in observations),
        caption_words_per_card=_numeric_metric(rows(lambda item: item.captions.words_per_card)),
        caption_highlight_ratio=_numeric_metric(rows(lambda item: item.captions.highlighted_word_ratio)),
        caption_highlight_behavior=_patterns((item.captions.highlight_behaviors, item.observation_confidence, item.evidence_count) for item in observations),
        broll_density_per_minute=_numeric_metric(rows(lambda item: item.visuals.broll_density_per_minute)),
        graphic_density_per_minute=_numeric_metric(rows(lambda item: item.visuals.graphic_density_per_minute)),
        transitions_per_minute=_numeric_metric(rows(lambda item: item.visuals.transitions_per_minute)),
        transition_types=_patterns((item.visuals.transition_types, item.observation_confidence, item.evidence_count) for item in observations),
        zooms_per_minute=_numeric_metric(rows(lambda item: item.visuals.zooms_per_minute)),
        motion_patterns=_patterns((item.visuals.motion_patterns, item.observation_confidence, item.evidence_count) for item in observations),
        sfx_per_minute=_numeric_metric(rows(lambda item: item.audio.sfx_per_minute)),
        integrated_loudness_lufs=_numeric_metric(rows(lambda item: item.audio.integrated_loudness_lufs)),
        music_behavior=_single_pattern((("present" if item.audio.music_present else "absent") if item.audio.music_present is not None else "unknown", item.observation_confidence, item.evidence_count) for item in observations),
        ducking_behavior=_single_pattern((item.audio.ducking_behavior, item.observation_confidence, item.evidence_count) for item in observations),
        story_beats=_patterns(((item.story_beats, item.observation_confidence, item.evidence_count) for item in observations), limit=12),
        color_traits=_patterns((item.visuals.color_traits, item.observation_confidence, item.evidence_count) for item in observations),
        framing_traits=_patterns((item.visuals.framing_traits, item.observation_confidence, item.evidence_count) for item in observations),
    )
    confidences = []
    for value in grammar.model_dump().values():
        if isinstance(value, dict) and value.get("evidence_count", 0):
            confidences.append(value["confidence"])
        elif isinstance(value, list):
            for nested in value:
                if isinstance(nested, dict):
                    for metric in nested.values():
                        if isinstance(metric, dict) and metric.get("evidence_count", 0):
                            confidences.append(metric["confidence"])
    overall = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
    timestamp = now or datetime.now(timezone.utc)
    seed = "|".join([request.owner_id, request.profile_name, *sorted(observed_ids)])
    profile_id = "dna_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return CreatorDNAProfile(
        profile_id=profile_id,
        owner_id=request.owner_id,
        name=request.profile_name,
        source_provenance=request.sources,
        source_count=len(observations),
        grammar=grammar,
        overall_confidence=overall,
        safety={
            "mode": "generalized_editing_grammar",
            "identity_cloning": False,
            "creator_impersonation": False,
            "voice_face_logo_or_likeness_replication": False,
            "public_video_download_performed": False,
            "instruction": "Apply abstract editing patterns; do not claim creator affiliation or reproduce identity assets.",
        },
        created_at=timestamp,
        updated_at=timestamp,
    )


class CreatorDNARepository:
    """Persistence adapter for Motor databases and the MVP LocalDatabase."""

    collection_name = "creator_dna_profiles"

    def __init__(self, database: Any):
        if hasattr(database, "get_collection"):
            self.collection = database.get_collection(self.collection_name)
        elif hasattr(database, self.collection_name):
            self.collection = getattr(database, self.collection_name)
        elif all(hasattr(database, name) for name in ("data", "lock", "_flush")):
            from local_store import LocalCollection
            self.collection = LocalCollection(database, self.collection_name)
        else:
            raise TypeError("database must provide a Motor-compatible collection")

    async def save(self, profile: CreatorDNAProfile) -> CreatorDNAProfile:
        existing = await self.collection.find_one({"profile_id": profile.profile_id, "owner_id": profile.owner_id})
        document = profile.model_dump(mode="json")
        if existing:
            document["created_at"] = existing.get("created_at", document["created_at"])
        await self.collection.update_one(
            {"profile_id": profile.profile_id, "owner_id": profile.owner_id},
            {"$set": document},
            upsert=True,
        )
        return CreatorDNAProfile.model_validate(document)

    async def get(self, owner_id: str, profile_id: str) -> CreatorDNAProfile | None:
        document = await self.collection.find_one({"profile_id": profile_id, "owner_id": owner_id}, {"_id": 0})
        return CreatorDNAProfile.model_validate(document) if document else None

    async def list(self, owner_id: str, limit: int = 50) -> list[CreatorDNAProfile]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        documents = await self.collection.find({"owner_id": owner_id}, {"_id": 0}).sort("updated_at", -1).to_list(limit)
        return [CreatorDNAProfile.model_validate(document) for document in documents]

    async def delete(self, owner_id: str, profile_id: str) -> bool:
        if not await self.collection.find_one({"profile_id": profile_id, "owner_id": owner_id}):
            return False
        await self.collection.delete_one({"profile_id": profile_id, "owner_id": owner_id})
        return True
