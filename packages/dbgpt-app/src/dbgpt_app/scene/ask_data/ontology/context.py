"""Bounded global-Ontology context for the main Agent only."""

from __future__ import annotations

import re
from typing import Any

from .schemas import OntologySnapshot


def relevant_ontology_projection(
    snapshot: OntologySnapshot,
    question: str | None = None,
    *,
    max_nodes: int = 24,
    scene_ids: list[str] | None = None,
    result_columns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Select a small connected subgraph; never expose scene Markdown or SQL."""
    graph = snapshot.graph_json
    prompt_projection = snapshot.prompt_projection_json or {}
    nodes = list(graph.get("nodes", []))
    edges = list(graph.get("edges", []))
    selected_scene_ids = {str(item) for item in (scene_ids or []) if str(item)}
    column_keys = {
        str(column.get("key"))
        for column in (result_columns or [])
        if isinstance(column, dict) and column.get("key")
    }
    metric_column_keys = {
        str(column.get("key"))
        for column in (result_columns or [])
        if isinstance(column, dict)
        and column.get("key")
        and str(column.get("type") or "").lower() == "metric"
    }
    if not metric_column_keys:
        metric_column_keys = set(column_keys)
    question_text = (question or "").strip().lower()
    terms = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", question_text))
    terms |= {item.lower() for item in column_keys}

    def score(item: dict[str, Any]) -> int:
        data = item.get("data", {}) or {}
        item_id = str(item.get("id") or "")
        item_type = str(item.get("type") or "")
        score_value = 0
        if item_id in column_keys:
            score_value += 6
        if item_type == "scene" and item_id in selected_scene_ids:
            score_value += 5
        source_scene = str(data.get("source_scene") or "")
        if source_scene and _split_values(source_scene) & selected_scene_ids:
            score_value += 3
        semantic_key = str(data.get("semantic_key") or "")
        if semantic_key and semantic_key in column_keys:
            score_value += 6
        if not terms:
            return score_value
        haystack = " ".join(
            [
                item_id,
                str(item.get("name", "")),
                " ".join(str(alias) for alias in item.get("aliases", [])),
                str(item.get("description", "")),
                " ".join(str(value) for value in data.values()),
            ]
        ).lower()
        return score_value + sum(1 for term in terms if term in haystack)

    matched = [node for node in nodes if score(node) > 0]
    if not matched and (selected_scene_ids or column_keys):
        matched = [
            node
            for node in nodes
            if node.get("id") in selected_scene_ids
            or node.get("id") in column_keys
            or str((node.get("data") or {}).get("source_scene") or "")
            in selected_scene_ids
        ]
    if not matched:
        matched = [
            node for node in nodes if node.get("type") in {"entity", "metric", "scene"}
        ]
    selected_ids = {str(node.get("id")) for node in matched[:max_nodes]}
    selected_edges = [
        edge
        for edge in edges
        if edge.get("source") in selected_ids or edge.get("target") in selected_ids
    ]
    for edge in selected_edges:
        if len(selected_ids) >= max_nodes:
            break
        selected_ids.add(str(edge.get("source")))
        selected_ids.add(str(edge.get("target")))
    selected_nodes = [node for node in nodes if node.get("id") in selected_ids]
    selected_node_ids = {str(node.get("id")) for node in selected_nodes}
    entities = _select_projection_items(
        prompt_projection.get("entities", []),
        selected_node_ids,
        score,
        limit=max(6, max_nodes // 3),
    )
    metric_bindings = _metric_bindings_for_columns(
        prompt_projection.get("edges", []) or edges,
        selected_scene_ids,
        metric_column_keys,
    )
    metrics = _select_projection_items(
        prompt_projection.get("metrics", []),
        selected_node_ids,
        score,
        limit=max(8, max_nodes // 2),
        preferred_ids=metric_column_keys | metric_bindings,
    )
    entity_ids = {str(item.get("id")) for item in entities}
    metric_ids = {str(item.get("id")) for item in metrics}
    for metric in metrics:
        entity = str((metric.get("data") or {}).get("entity") or "")
        if entity:
            entity_ids.add(entity)
    analysis_dimensions = [
        item
        for item in prompt_projection.get("analysis_dimensions", [])
        if score(item) > 0
        or set((item.get("data") or {}).get("applicable_entities", [])) & entity_ids
        or set((item.get("data") or {}).get("applicable_metrics", [])) & metric_ids
    ][:8]
    metric_relations = [
        item
        for item in prompt_projection.get("metric_relations", [])
        if item.get("source") in metric_ids or item.get("target") in metric_ids
    ][:12]
    cross_scene_analysis_paths = [
        item
        for item in prompt_projection.get("cross_scene_analysis_paths", [])
        if score(item) > 0 or set((item.get("data") or {}).get("entities", [])) & entity_ids
    ][:6]
    result_analysis_rules = [
        item
        for item in prompt_projection.get("result_analysis_rules", [])
        if score(item) > 0
        or not (item.get("data") or {}).get("applies_to_metrics")
        or set((item.get("data") or {}).get("applies_to_metrics", [])) & metric_ids
    ][:8]
    return {
        "ontology_snapshot_id": snapshot.snapshot_id,
        "revision": snapshot.revision,
        "matched_scene_ids": sorted(selected_scene_ids),
        "matched_column_keys": sorted(column_keys),
        "nodes": selected_nodes[:max_nodes],
        "edges": [
            edge
            for edge in edges
            if edge.get("source") in selected_ids and edge.get("target") in selected_ids
        ][: max_nodes * 2],
        "entities": entities,
        "metrics": metrics,
        "analysis_dimensions": analysis_dimensions,
        "metric_relations": metric_relations,
        "cross_scene_analysis_paths": cross_scene_analysis_paths,
        "result_analysis_rules": result_analysis_rules,
        "clarification_rules": prompt_projection.get("clarification_rules", [])[:8],
    }


def render_main_agent_ontology_context(
    snapshot: OntologySnapshot | None,
    question: str | None = None,
    *,
    scene_ids: list[str] | None = None,
    result_columns: list[dict[str, Any]] | None = None,
) -> str:
    if snapshot is None:
        return ""
    projection = relevant_ontology_projection(
        snapshot,
        question,
        scene_ids=scene_ids,
        result_columns=result_columns,
    )
    lines = [f"全局业务 Ontology（版本 {projection['revision']}）："]
    if projection.get("matched_scene_ids") or projection.get("matched_column_keys"):
        lines.append(
            "- 命中结果："
            f"场景 {', '.join(projection.get('matched_scene_ids') or []) or '未指定'}；"
            f"字段 {', '.join(projection.get('matched_column_keys') or []) or '未指定'}"
        )
    for node in projection["nodes"]:
        if node.get("type") == "rule":
            lines.append(f"- 澄清规则：{node.get('description')}")
            continue
        if node.get("type") not in {"entity", "metric", "scene"}:
            continue
        aliases = "、".join(node.get("aliases", []))
        details = node.get("data", {}) or {}
        suffix = f"；别名：{aliases}" if aliases else ""
        if node.get("type") == "metric" and details.get("source_scene"):
            suffix += f"；由场景 {details['source_scene']} 提供"
        lines.append(f"- {node.get('name')}（{node.get('id')}）{suffix}")
    for edge in projection["edges"]:
        if edge.get("type") in {"relation", "cross_scene_join"}:
            relation = f"{edge.get('source')} —{edge.get('name')}→ {edge.get('target')}"
            lines.append(f"- 关系：{relation}")
    for metric in projection.get("metrics", []):
        details = metric.get("data", {}) or {}
        meaning = details.get("analysis_meaning")
        interpretation = details.get("interpretation")
        if meaning or interpretation:
            lines.append(
                f"- 指标解读：{metric.get('name')}（{metric.get('id')}）："
                f"{meaning or ''}{'；' if meaning and interpretation else ''}{interpretation or ''}"
            )
    for dimension in projection.get("analysis_dimensions", []):
        if dimension.get("description"):
            lines.append(f"- 建议分析维度：{dimension.get('name')}：{dimension.get('description')}")
    for edge in projection.get("metric_relations", []):
        usage = (edge.get("data") or {}).get("analysis_usage") or edge.get("description")
        lines.append(
            f"- 指标关系：{edge.get('source')} —{edge.get('name')}→ {edge.get('target')}"
            f"{f'；用途：{usage}' if usage else ''}"
        )
    for path in projection.get("cross_scene_analysis_paths", []):
        if path.get("description"):
            lines.append(f"- 跨场景分析路径：{path.get('name')}：{path.get('description')}")
    for rule in projection.get("result_analysis_rules", []):
        guidance = (rule.get("data") or {}).get("guidance") or rule.get("description")
        if guidance:
            lines.append(f"- 查询结果分析规则：{guidance}")
    for rule in projection.get("clarification_rules", []):
        lines.append(f"- 需求澄清规则：{rule}")
    return "\n".join(lines)


def _select_projection_items(
    items: list[dict[str, Any]],
    selected_ids: set[str],
    score,
    *,
    limit: int,
    preferred_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    preferred_ids = preferred_ids or set()
    matched = [
        item
        for item in items
        if item.get("id") in selected_ids
        or item.get("id") in preferred_ids
        or score(item) > 0
    ]
    if not matched:
        matched = list(items)
    return matched[:limit]


def _metric_bindings_for_columns(
    edges: list[dict[str, Any]],
    scene_ids: set[str],
    column_keys: set[str],
) -> set[str]:
    if not column_keys:
        return set()
    metric_ids: set[str] = set()
    for edge in edges:
        if edge.get("type") != "scene_binding":
            continue
        if scene_ids and edge.get("source") not in scene_ids:
            continue
        semantic_key = str((edge.get("data") or {}).get("semantic_key") or "")
        if semantic_key in column_keys:
            metric_ids.add(str(edge.get("target")))
    return metric_ids


def _split_values(value: str) -> set[str]:
    return {
        item.strip()
        for item in re.split(r"[,，、;\s]+", str(value or ""))
        if item.strip()
    }


__all__ = ["relevant_ontology_projection", "render_main_agent_ontology_context"]
