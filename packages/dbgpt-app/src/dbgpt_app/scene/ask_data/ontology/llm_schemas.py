"""Pydantic contracts for LLM-generated analysis Ontology drafts."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field


GenerateFn = Callable[[str], Awaitable[str]]


class OntologySceneSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str = Field(min_length=1)
    snapshot_id: str = ""
    revision_id: str = ""
    semantic_md_hash: str = ""
    semantic_md: str = Field(min_length=1)
    name: str | None = None
    description: str | None = None


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    section: str | None = None
    reason: str | None = None


class SceneEntityCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    definition: str = Field(min_length=1)
    business_role: str = Field(min_length=1)
    entity_type: Literal[
        "core_business_object",
        "dimension",
        "actor",
        "organization",
        "external_party",
        "time",
        "status",
        "other",
    ]
    evidence: list[Evidence] = Field(default_factory=list)


class SceneMetricCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    unit: str | None = None
    formula: str | None = None
    owner_entity_local_id: str | None = None
    semantic_key: str | None = None
    analysis_meaning: str = Field(min_length=1)
    interpretation: str = Field(min_length=1)
    positive_direction: Literal["higher_better", "lower_better", "neutral", "depends"]
    common_anomalies: list[str] = Field(default_factory=list)
    recommended_dimensions: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class SceneRelationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_id: str = Field(min_length=1)
    source_entity_local_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    target_entity_local_id: str = Field(min_length=1)
    cardinality: str | None = None
    analysis_meaning: str = Field(min_length=1)
    evidence: list[Evidence] = Field(default_factory=list)


class SceneAnalysisCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1)
    suitable_questions: list[str] = Field(default_factory=list)
    recommended_dimensions: list[str] = Field(default_factory=list)
    recommended_metrics: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class SceneOntologyExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str = Field(min_length=1)
    scene_name: str = Field(min_length=1)
    entities: list[SceneEntityCandidate] = Field(default_factory=list)
    metrics: list[SceneMetricCandidate] = Field(default_factory=list)
    relations: list[SceneRelationCandidate] = Field(default_factory=list)
    analysis_capabilities: list[SceneAnalysisCapability] = Field(default_factory=list)
    join_key_candidates: list[str] = Field(default_factory=list)
    clarification_rules: list[str] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)


class SceneBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene: str = Field(min_length=1)
    semantic_key: str = Field(min_length=1)


class GlobalEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    definition: str = Field(min_length=1)
    business_analysis_role: str = Field(min_length=1)
    source_scenes: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class GlobalMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    entity_id: str = Field(min_length=1)
    unit: str | None = None
    formula: str | None = None
    analysis_meaning: str = Field(min_length=1)
    interpretation: str = Field(min_length=1)
    positive_direction: Literal["higher_better", "lower_better", "neutral", "depends"]
    common_anomalies: list[str] = Field(default_factory=list)
    recommended_dimensions: list[str] = Field(default_factory=list)
    related_metrics: list[str] = Field(default_factory=list)
    source_scenes: list[str] = Field(default_factory=list)
    scene_bindings: list[SceneBinding] = Field(default_factory=list)
    conflict_note: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class GlobalRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    source_entity_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    target_entity_id: str = Field(min_length=1)
    cardinality: str | None = None
    analysis_meaning: str = Field(min_length=1)
    evidence: list[Evidence] = Field(default_factory=list)


class MetricRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    primary_metric_id: str = Field(min_length=1)
    relation: Literal[
        "driver",
        "denominator",
        "numerator",
        "correlates_with",
        "explains",
        "segments_by",
        "risk_signal",
    ]
    related_metric_id: str = Field(min_length=1)
    analysis_usage: str = Field(min_length=1)


class AnalysisDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    applicable_entities: list[str] = Field(default_factory=list)
    applicable_metrics: list[str] = Field(default_factory=list)
    analysis_usage: str = Field(min_length=1)
    caveats: list[str] = Field(default_factory=list)


class CrossSceneAnalysisPath(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    scenes: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    join_keys: list[str] = Field(default_factory=list)
    recommended_analysis: str = Field(min_length=1)
    caveats: list[str] = Field(default_factory=list)


class ResultAnalysisRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    guidance: str = Field(min_length=1)
    applies_to_entities: list[str] = Field(default_factory=list)
    applies_to_metrics: list[str] = Field(default_factory=list)


class GlobalOntologySynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[GlobalEntity] = Field(default_factory=list)
    relations: list[GlobalRelation] = Field(default_factory=list)
    metrics: list[GlobalMetric] = Field(default_factory=list)
    metric_relations: list[MetricRelation] = Field(default_factory=list)
    analysis_dimensions: list[AnalysisDimension] = Field(default_factory=list)
    cross_scene_analysis_paths: list[CrossSceneAnalysisPath] = Field(
        default_factory=list
    )
    result_analysis_rules: list[ResultAnalysisRule] = Field(default_factory=list)
    clarification_rules: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class OntologyGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markdown: str
    synthesis: GlobalOntologySynthesis | None = None
    mode: Literal["llm", "rules"] = "llm"
    source_count: int = 0
    entity_count: int = 0
    metric_count: int = 0
    relation_count: int = 0
    analysis_rule_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AnalysisDimension",
    "CrossSceneAnalysisPath",
    "Evidence",
    "GenerateFn",
    "GlobalEntity",
    "GlobalMetric",
    "GlobalOntologySynthesis",
    "GlobalRelation",
    "MetricRelation",
    "OntologyGenerationResult",
    "OntologySceneSource",
    "ResultAnalysisRule",
    "SceneAnalysisCapability",
    "SceneBinding",
    "SceneEntityCandidate",
    "SceneMetricCandidate",
    "SceneOntologyExtraction",
    "SceneRelationCandidate",
]
