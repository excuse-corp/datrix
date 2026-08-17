"""Validation for LLM-generated Ontology JSON."""

from __future__ import annotations

import re

from .generator import scene_semantic_keys_from_source
from .llm_schemas import (
    GlobalOntologySynthesis,
    OntologySceneSource,
    SceneOntologyExtraction,
)


class OntologyLLMValidationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def validate_sources_have_semantic_md(sources: list[OntologySceneSource]) -> None:
    missing = [source.scene_id for source in sources if not source.semantic_md.strip()]
    if missing:
        raise OntologyLLMValidationError(
            "ONTOLOGY_SOURCE_SEMANTIC_MD_MISSING",
            f"场景语义文档缺失，无法生成本体：{', '.join(sorted(missing))}",
        )


def validate_scene_extraction(
    extraction: SceneOntologyExtraction,
    source: OntologySceneSource,
) -> list[str]:
    warnings: list[str] = []
    if extraction.scene_id != source.scene_id:
        raise OntologyLLMValidationError(
            "ONTOLOGY_LLM_REFERENCE_INVALID",
            f"模型输出的 scene_id 与输入不一致：{extraction.scene_id}",
        )
    entity_ids = {entity.local_id for entity in extraction.entities}
    for metric in extraction.metrics:
        if metric.owner_entity_local_id and metric.owner_entity_local_id not in entity_ids:
            raise OntologyLLMValidationError(
                "ONTOLOGY_LLM_REFERENCE_INVALID",
                (
                    f"指标 {metric.local_id} 引用了不存在的实体："
                    f"{metric.owner_entity_local_id}"
                ),
            )
    for relation in extraction.relations:
        if relation.source_entity_local_id not in entity_ids:
            raise OntologyLLMValidationError(
                "ONTOLOGY_LLM_REFERENCE_INVALID",
                (
                    f"关系 {relation.local_id} 的主体不存在："
                    f"{relation.source_entity_local_id}"
                ),
            )
        if relation.target_entity_local_id not in entity_ids:
            raise OntologyLLMValidationError(
                "ONTOLOGY_LLM_REFERENCE_INVALID",
                (
                    f"关系 {relation.local_id} 的客体不存在："
                    f"{relation.target_entity_local_id}"
                ),
            )
    for quote in _evidence_quotes(extraction):
        if not _quote_matches(quote, source.semantic_md):
            warnings.append(
                f"场景 {source.scene_id} 的证据片段未在语义文档中直接命中：{quote[:32]}"
            )
    return warnings


def validate_global_synthesis(
    synthesis: GlobalOntologySynthesis,
    sources: list[OntologySceneSource],
) -> list[str]:
    scene_ids = {source.scene_id for source in sources}
    source_by_scene = {source.scene_id: source for source in sources}
    warnings: list[str] = []

    entity_ids = {entity.id for entity in synthesis.entities}
    metric_ids = {metric.id for metric in synthesis.metrics}

    for entity in synthesis.entities:
        _assert_scenes(entity.source_scenes, scene_ids, entity.id)
        warnings.extend(_evidence_warnings(entity.evidence, source_by_scene))

    for metric in synthesis.metrics:
        _assert_scenes(metric.source_scenes, scene_ids, metric.id)
        if metric.entity_id not in entity_ids:
            raise OntologyLLMValidationError(
                "ONTOLOGY_LLM_REFERENCE_INVALID",
                f"指标 {metric.id} 引用了不存在的所属实体：{metric.entity_id}",
            )
        for related_metric in metric.related_metrics:
            if related_metric not in metric_ids:
                raise OntologyLLMValidationError(
                    "ONTOLOGY_LLM_REFERENCE_INVALID",
                    f"指标 {metric.id} 引用了不存在的关联指标：{related_metric}",
                )
        for binding in metric.scene_bindings:
            if binding.scene not in scene_ids:
                raise OntologyLLMValidationError(
                    "ONTOLOGY_LLM_REFERENCE_INVALID",
                    f"指标 {metric.id} 绑定了不存在的场景：{binding.scene}",
                )
            keys = scene_semantic_keys_from_source(
                source_by_scene[binding.scene].model_dump(mode="json")
            )
            if binding.semantic_key not in keys:
                warnings.append(
                    (
                        f"指标 {metric.id} 的语义键 {binding.semantic_key} "
                        f"未在场景 {binding.scene} 中直接命中"
                    )
                )
        warnings.extend(_evidence_warnings(metric.evidence, source_by_scene))

    for relation in synthesis.relations:
        if relation.source_entity_id not in entity_ids:
            raise OntologyLLMValidationError(
                "ONTOLOGY_LLM_REFERENCE_INVALID",
                f"关系 {relation.id} 的主体不存在：{relation.source_entity_id}",
            )
        if relation.target_entity_id not in entity_ids:
            raise OntologyLLMValidationError(
                "ONTOLOGY_LLM_REFERENCE_INVALID",
                f"关系 {relation.id} 的客体不存在：{relation.target_entity_id}",
            )
        warnings.extend(_evidence_warnings(relation.evidence, source_by_scene))

    for relation in synthesis.metric_relations:
        if relation.primary_metric_id not in metric_ids:
            raise OntologyLLMValidationError(
                "ONTOLOGY_LLM_REFERENCE_INVALID",
                f"指标关系 {relation.id} 的主指标不存在：{relation.primary_metric_id}",
            )
        if relation.related_metric_id not in metric_ids:
            raise OntologyLLMValidationError(
                "ONTOLOGY_LLM_REFERENCE_INVALID",
                f"指标关系 {relation.id} 的关联指标不存在：{relation.related_metric_id}",
            )

    for dimension in synthesis.analysis_dimensions:
        unknown_entities = [
            item for item in dimension.applicable_entities if item not in entity_ids
        ]
        unknown_metrics = [
            item for item in dimension.applicable_metrics if item not in metric_ids
        ]
        if unknown_entities or unknown_metrics:
            raise OntologyLLMValidationError(
                "ONTOLOGY_LLM_REFERENCE_INVALID",
                (
                    f"分析维度 {dimension.id} 引用了不存在的实体或指标："
                    f"{', '.join(unknown_entities + unknown_metrics)}"
                ),
            )

    for path in synthesis.cross_scene_analysis_paths:
        _assert_scenes(path.scenes, scene_ids, path.id)
        unknown_entities = [item for item in path.entities if item not in entity_ids]
        if unknown_entities:
            raise OntologyLLMValidationError(
                "ONTOLOGY_LLM_REFERENCE_INVALID",
                f"跨场景路径 {path.id} 引用了不存在的实体：{', '.join(unknown_entities)}",
            )

    for rule in synthesis.result_analysis_rules:
        unknown_entities = [
            item for item in rule.applies_to_entities if item not in entity_ids
        ]
        unknown_metrics = [
            item for item in rule.applies_to_metrics if item not in metric_ids
        ]
        if unknown_entities or unknown_metrics:
            raise OntologyLLMValidationError(
                "ONTOLOGY_LLM_REFERENCE_INVALID",
                (
                    f"分析规则 {rule.id} 引用了不存在的实体或指标："
                    f"{', '.join(unknown_entities + unknown_metrics)}"
                ),
            )
    return warnings


def _assert_scenes(values: list[str], scene_ids: set[str], object_id: str) -> None:
    unknown = [scene_id for scene_id in values if scene_id not in scene_ids]
    if unknown:
        raise OntologyLLMValidationError(
            "ONTOLOGY_LLM_REFERENCE_INVALID",
            f"{object_id} 引用了不存在的场景：{', '.join(unknown)}",
        )


def _evidence_warnings(evidence, source_by_scene: dict[str, OntologySceneSource]):
    warnings: list[str] = []
    for item in evidence:
        source = source_by_scene.get(item.scene_id)
        if source is None:
            raise OntologyLLMValidationError(
                "ONTOLOGY_LLM_REFERENCE_INVALID",
                f"证据引用了不存在的场景：{item.scene_id}",
            )
        if not _quote_matches(item.quote, source.semantic_md):
            warnings.append(
                f"场景 {item.scene_id} 的证据片段未在语义文档中直接命中：{item.quote[:32]}"
            )
    return warnings


def _evidence_quotes(extraction: SceneOntologyExtraction) -> list[str]:
    quotes: list[str] = []
    for entity in extraction.entities:
        quotes.extend(item.quote for item in entity.evidence)
    for metric in extraction.metrics:
        quotes.extend(item.quote for item in metric.evidence)
    for relation in extraction.relations:
        quotes.extend(item.quote for item in relation.evidence)
    return quotes


def _quote_matches(quote: str, markdown: str) -> bool:
    quote = _normalize_quote(quote)
    markdown = _normalize_quote(markdown)
    if not quote:
        return True
    if quote in markdown:
        return True
    compact_quote = re.sub(r"\s+", "", quote)
    compact_markdown = re.sub(r"\s+", "", markdown)
    if compact_quote and compact_quote in compact_markdown:
        return True
    tokens = [token for token in re.split(r"[\s，。；、,.;:：|`]+", quote) if token]
    if not tokens:
        return True
    hits = sum(1 for token in tokens if token in markdown)
    return hits / len(tokens) >= 0.6


def _normalize_quote(value: str) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


__all__ = [
    "OntologyLLMValidationError",
    "validate_global_synthesis",
    "validate_scene_extraction",
    "validate_sources_have_semantic_md",
]
