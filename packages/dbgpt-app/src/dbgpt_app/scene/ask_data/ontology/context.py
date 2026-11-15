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
    authorized_scene_ids: list[str] | None = None,
    is_platform_admin: bool = True,
    result_columns: list[dict[str, Any]] | None = None,
    result_profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Select a small connected subgraph; never expose scene Markdown or SQL."""
    graph = snapshot.graph_json
    prompt_projection = snapshot.prompt_projection_json or {}
    nodes = list(graph.get("nodes", []))
    edges = list(graph.get("edges", []))
    authorized = {str(item) for item in (authorized_scene_ids or []) if str(item)}

    def source_scenes(item: dict[str, Any]) -> set[str]:
        data = item.get("data", {}) or {}
        values: set[str] = set()
        for raw in (
            item.get("source_scenes"),
            item.get("scenes"),
            data.get("source_scene"),
            data.get("source_scenes"),
            data.get("scenes"),
        ):
            if isinstance(raw, str):
                values.update(_split_values(raw))
            elif isinstance(raw, (list, tuple, set)):
                values.update(str(value) for value in raw if value)
        for provenance in item.get("provenance", []) or []:
            if isinstance(provenance, dict) and provenance.get("scene_id"):
                values.add(str(provenance["scene_id"]))
        return values

    def source_is_visible(item: dict[str, Any]) -> bool:
        if is_platform_admin:
            return True
        sources = source_scenes(item)
        if not sources and str(item.get("id") or "") in authorized:
            sources.add(str(item["id"]))
        return bool(sources) and sources.issubset(authorized)

    if authorized_scene_ids is not None and not is_platform_admin:
        nodes = [node for node in nodes if source_is_visible(node)]
        visible_node_ids = {str(node.get("id")) for node in nodes}
        nodes_by_id = {str(node.get("id")): node for node in nodes}

        def edge_is_visible(edge: dict[str, Any]) -> bool:
            edge_sources = source_scenes(edge)
            if edge_sources:
                return edge_sources.issubset(authorized)
            source_node = nodes_by_id.get(str(edge.get("source")))
            target_node = nodes_by_id.get(str(edge.get("target")))
            if source_node is None or target_node is None:
                return False
            endpoint_sources = source_scenes(source_node) | source_scenes(target_node)
            return bool(endpoint_sources) and endpoint_sources.issubset(authorized)

        scene_nodes = {
            str(node.get("id")) for node in nodes if node.get("type") == "scene"
        }
        edges = [
            edge
            for edge in edges
            if edge.get("source") in visible_node_ids
            and edge.get("target") in visible_node_ids
            and edge_is_visible(edge)
            and not (
                edge.get("type") == "cross_scene_join"
                and (
                    not (edge.get("data") or {}).get("scenes")
                    or not set((edge.get("data") or {}).get("scenes", [])).issubset(
                        authorized
                    )
                )
            )
            and not (
                edge.get("type") == "scene_binding"
                and edge.get("source") not in scene_nodes
                and edge.get("target") not in scene_nodes
            )
        ]
        prompt_projection = {
            **prompt_projection,
            **{
                key: [
                    item
                    for item in prompt_projection.get(key, [])
                    if source_is_visible(item)
                ]
                for key in (
                    "entities",
                    "metrics",
                    "analysis_dimensions",
                    "metric_relations",
                    "cross_scene_analysis_paths",
                    "result_analysis_rules",
                )
            },
            "clarification_rules": [],
        }
        visible_node_ids = {str(node.get("id")) for node in nodes}
        prompt_edges = prompt_projection.get("edges", [])
        prompt_projection["edges"] = [
            edge
            for edge in prompt_edges
            if edge.get("source") in visible_node_ids
            and edge.get("target") in visible_node_ids
            and edge_is_visible(edge)
            and (
                edge.get("type") != "cross_scene_join"
                or (
                    bool((edge.get("data") or {}).get("scenes"))
                    and set((edge.get("data") or {}).get("scenes", [])).issubset(
                        authorized
                    )
                )
            )
        ]
    selected_scene_ids = {str(item) for item in (scene_ids or []) if str(item)}

    def confirmed_bridge_is_in_scope(item: dict[str, Any]) -> bool:
        data = item.get("data", {}) or {}
        bridge_scenes = {str(value) for value in (data.get("scenes") or []) if value}
        return (
            data.get("status") == "confirmed"
            and len(bridge_scenes) == 2
            and bool(selected_scene_ids)
            and bridge_scenes.issubset(selected_scene_ids)
        )

    column_keys = {
        str(column.get("key"))
        for column in (result_columns or [])
        if isinstance(column, dict) and column.get("key")
    }
    normalized_profiles = [
        {
            "scene_id": str(profile.get("scene_id") or ""),
            "grain": _display_values(profile.get("grain")),
            "grain_semantic_keys": _display_values(profile.get("grain_semantic_keys")),
            "time_grain": profile.get("time_grain"),
            "time_basis": profile.get("time_basis"),
            "time_range": profile.get("time_range"),
        }
        for profile in (result_profiles or [])
        if isinstance(profile, dict) and profile.get("scene_id")
    ]
    profiles_by_scene = {
        profile["scene_id"]: profile for profile in normalized_profiles
    }
    metric_result_keys = {
        str(column.get("key"))
        for column in (result_columns or [])
        if isinstance(column, dict)
        and column.get("key")
        and str(column.get("type") or "").lower() == "metric"
    }
    metric_result_scenes = {
        str(column.get("scene_id"))
        for column in (result_columns or [])
        if isinstance(column, dict)
        and column.get("scene_id")
        and column.get("key")
        and str(column.get("type") or "").lower() == "metric"
    }

    def bridge_is_aligned(item: dict[str, Any]) -> bool:
        if not confirmed_bridge_is_in_scope(item):
            return False
        data = item.get("data", {}) or {}
        bridge_scenes = [str(value) for value in (data.get("scenes") or []) if value]
        if len(bridge_scenes) != 2 or not set(bridge_scenes).issubset(
            metric_result_scenes
        ):
            return False
        profiles = [profiles_by_scene.get(scene) for scene in bridge_scenes]
        if any(profile is None for profile in profiles):
            return False
        expected_time_bases = [
            str(data.get("left_time_basis") or "").strip(),
            str(data.get("right_time_basis") or "").strip(),
        ]
        if not all(expected_time_bases) or any(
            str(profile.get("time_basis") or "").strip() != expected
            for profile, expected in zip(profiles, expected_time_bases)
        ):
            return False
        left, right = profiles
        if not left.get("time_grain") or left.get("time_grain") != right.get(
            "time_grain"
        ):
            return False
        left_range = _time_range_signature(left.get("time_range"))
        right_range = _time_range_signature(right.get("time_range"))
        if not left_range or left_range != right_range:
            return False
        left_grain = set(left.get("grain_semantic_keys") or left.get("grain") or [])
        right_grain = set(right.get("grain_semantic_keys") or right.get("grain") or [])
        return bool(left_grain and left_grain == right_grain)

    edges = [
        edge
        for edge in edges
        if edge.get("type") != "cross_scene_join" or bridge_is_aligned(edge)
    ]
    nodes = [
        node
        for node in nodes
        if node.get("type") != "analysis_path" or bridge_is_aligned(node)
    ]
    prompt_projection = {
        **prompt_projection,
        "cross_scene_analysis_paths": [
            item
            for item in prompt_projection.get("cross_scene_analysis_paths", [])
            if bridge_is_aligned(item)
        ],
        "edges": [
            edge
            for edge in prompt_projection.get("edges", [])
            if edge.get("type") != "cross_scene_join" or bridge_is_aligned(edge)
        ],
    }
    metric_column_keys = {
        str(column.get("key"))
        for column in (result_columns or [])
        if isinstance(column, dict)
        and column.get("key")
        and str(column.get("type") or "").lower() == "metric"
    }
    metric_semantic_keys = {
        str(column.get("semantic_key"))
        for column in (result_columns or [])
        if isinstance(column, dict)
        and column.get("semantic_key")
        and str(column.get("type") or "").lower() == "metric"
    }
    question_text = (question or "").strip().lower()
    terms = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", question_text))
    terms |= {
        str(value).lower()
        for column in (result_columns or [])
        if isinstance(column, dict)
        for value in (
            column.get("key"),
            column.get("semantic_key"),
            column.get("label"),
        )
        if value
    }

    def score(item: dict[str, Any]) -> int:
        data = item.get("data", {}) or {}
        item_id = str(item.get("id") or "")
        item_type = str(item.get("type") or "")
        score_value = 0
        if item_id in column_keys:
            score_value += 100
        if item_type == "scene" and item_id in selected_scene_ids:
            score_value += 5
        source_scene = str(data.get("source_scene") or "")
        if source_scene and _split_values(source_scene) & selected_scene_ids:
            score_value += 3
        semantic_key = str(data.get("semantic_key") or "")
        if semantic_key and semantic_key in column_keys:
            score_value += 90
        if semantic_key and semantic_key in metric_semantic_keys:
            score_value += 95
        if item_id in metric_column_keys:
            score_value += 100
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

    matched = sorted(
        (node for node in nodes if score(node) > 0),
        key=lambda node: (-score(node), str(node.get("id") or "")),
    )
    selected_ids = {str(node.get("id")) for node in matched[:max_nodes]}
    selected_nodes = [
        node for node in matched[:max_nodes] if node.get("id") in selected_ids
    ]
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
        metric_column_keys | metric_semantic_keys,
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
    cross_scene_analysis_paths = []
    for path in prompt_projection.get("cross_scene_analysis_paths", [])[:6]:
        path = dict(path)
        data = dict(path.get("data", {}) or {})
        path_scenes = [str(value) for value in data.get("scenes", []) if value]
        data["result_profiles"] = [
            profiles_by_scene[scene_id]
            for scene_id in path_scenes
            if scene_id in profiles_by_scene
        ]
        data["alignment_status"] = "verified"
        path["data"] = data
        cross_scene_analysis_paths.append(path)
    result_analysis_rules = [
        item
        for item in prompt_projection.get("result_analysis_rules", [])
        if score(item) > 0
        or (
            bool((item.get("data") or {}).get("applies_to_metrics"))
            and set((item.get("data") or {}).get("applies_to_metrics", [])) & metric_ids
        )
        or (
            bool((item.get("data") or {}).get("applies_to_entities"))
            and set((item.get("data") or {}).get("applies_to_entities", []))
            & entity_ids
        )
    ][:8]
    return {
        "ontology_snapshot_id": snapshot.snapshot_id,
        "revision": snapshot.revision,
        "matched_scene_ids": sorted(selected_scene_ids),
        "matched_column_keys": sorted(column_keys),
        "result_profiles": normalized_profiles,
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
        "clarification_rules": [],
        "matched_bindings": _matched_bindings_for_columns(
            prompt_projection.get("edges", []) or edges,
            selected_scene_ids,
            result_columns or [],
            metric_ids,
        ),
        "unmatched_columns": sorted(
            key
            for key in metric_result_keys
            if not _column_is_bound(
                key,
                result_columns or [],
                prompt_projection.get("edges", []) or edges,
                selected_scene_ids,
                metric_ids,
            )
        ),
    }


def render_main_agent_ontology_context(
    snapshot: OntologySnapshot | None,
    question: str | None = None,
    *,
    scene_ids: list[str] | None = None,
    authorized_scene_ids: list[str] | None = None,
    is_platform_admin: bool = True,
    result_columns: list[dict[str, Any]] | None = None,
    result_profiles: list[dict[str, Any]] | None = None,
    max_nodes: int = 16,
) -> str:
    if snapshot is None:
        return ""
    projection = relevant_ontology_projection(
        snapshot,
        question,
        scene_ids=scene_ids,
        authorized_scene_ids=authorized_scene_ids,
        is_platform_admin=is_platform_admin,
        result_columns=result_columns,
        result_profiles=result_profiles,
        max_nodes=max_nodes,
    )
    has_context = any(
        projection.get(key)
        for key in (
            "nodes",
            "edges",
            "entities",
            "metrics",
            "analysis_dimensions",
            "metric_relations",
            "cross_scene_analysis_paths",
            "result_analysis_rules",
            "matched_bindings",
        )
    )
    if not has_context and not projection.get("unmatched_columns"):
        return ""
    lines = [f"全局业务 Ontology（版本 {projection['revision']}）："]
    if projection.get("unmatched_columns"):
        lines.append(
            "- 以下结果列没有已确认的本体指标绑定："
            + "、".join(projection["unmatched_columns"])
            + "。请根据本次结果和数据口径解释，不要推测其业务含义。"
        )
    if projection.get("matched_scene_ids") or projection.get("matched_column_keys"):
        lines.append(
            "- 命中结果："
            f"场景 {', '.join(projection.get('matched_scene_ids') or []) or '未指定'}；"
            f"字段 {', '.join(projection.get('matched_column_keys') or []) or '未指定'}"
        )
    for profile in projection.get("result_profiles", []):
        details = []
        if profile.get("grain"):
            details.append("结果粒度：" + "、".join(profile["grain"]))
        if profile.get("time_grain"):
            details.append(f"时间粒度：{profile['time_grain']}")
        if profile.get("time_basis"):
            details.append(f"时间依据：{profile['time_basis']}")
        if profile.get("time_range"):
            details.append(f"时间范围：{profile['time_range']}")
        if details:
            lines.append(
                f"- 场景 {profile['scene_id']} 本次结果：" + "；".join(details)
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
        if details.get("domain_id"):
            suffix += f"；领域：{details['domain_id']}"
        if node.get("type") == "metric" and details.get("source_scene"):
            suffix += f"；由场景 {details['source_scene']} 提供"
        lines.append(f"- {node.get('name')}（{node.get('id')}）{suffix}")
    path_join_ids = {
        f"{path.get('id')}_join"
        for path in projection.get("cross_scene_analysis_paths", [])
        if path.get("id")
    }
    for edge in projection["edges"]:
        if edge.get("type") == "cross_scene_join":
            if edge.get("id") in path_join_ids:
                continue
            details = edge.get("data", {}) or {}
            lines.append(
                f"- 已确认跨场景关联（本次需核对条件）："
                f"{edge.get('source')} —{edge.get('name')}→ {edge.get('target')}"
            )
            lines.append(
                "  - 只有核对本次查询的时间和统计粒度均满足桥接条件后，才可进行该项分析；"
                "未满足或无法核对时，说明不能直接比较。"
            )
            for key, label in (
                ("mapping_source", "映射依据"),
                ("left_time_basis", "左侧时间依据"),
                ("right_time_basis", "右侧时间依据"),
                ("grain", "统计粒度"),
            ):
                if details.get(key):
                    lines.append(f"  - {label}：{details[key]}")
            for key, label in (
                ("time_alignment_conditions", "时间对齐条件"),
                ("grain_alignment", "粒度对齐条件"),
                ("allowed_analyses", "允许分析"),
                ("forbidden_inferences", "禁止推断"),
            ):
                values = details.get(key) or []
                display_values = _display_values(values)
                if display_values:
                    lines.append(f"  - {label}：" + "、".join(display_values))
            for profile in projection.get("result_profiles", []):
                if profile.get("scene_id") not in (details.get("scenes") or []):
                    continue
                lines.append(
                    f"  - 本次 {profile['scene_id']} 结果："
                    f"粒度 {', '.join(profile.get('grain') or []) or '未识别'}；"
                    f"时间粒度 {profile.get('time_grain') or '未识别'}；"
                    f"时间依据 {profile.get('time_basis') or '未识别'}"
                )
        elif edge.get("type") == "relation":
            relation = f"{edge.get('source')} —{edge.get('name')}→ {edge.get('target')}"
            lines.append(f"- 关系：{relation}")
    for metric in projection.get("metrics", []):
        details = metric.get("data", {}) or {}
        meaning = details.get("analysis_meaning")
        interpretation = details.get("interpretation")
        descriptors = [
            f"聚合：{details['aggregation']}" if details.get("aggregation") else "",
            f"单位：{details['unit']}" if details.get("unit") else "",
            f"统计粒度：{details['grain']}" if details.get("grain") else "",
            f"时间依据：{details['time_basis']}" if details.get("time_basis") else "",
        ]
        explanation = "；".join(
            value for value in (meaning, interpretation, *descriptors) if value
        )
        if explanation:
            lines.append(
                f"- 指标解读：{metric.get('name')}（{metric.get('id')}）：{explanation}"
            )
        if details.get("conflict_note"):
            lines.append(
                f"- 指标口径冲突：{metric.get('name')}：{details['conflict_note']}"
            )
    for dimension in projection.get("analysis_dimensions", []):
        if dimension.get("description"):
            lines.append(
                f"- 建议分析维度：{dimension.get('name')}：{dimension.get('description')}"
            )
    for edge in projection.get("metric_relations", []):
        usage = (edge.get("data") or {}).get("analysis_usage") or edge.get(
            "description"
        )
        lines.append(
            f"- 指标关系：{edge.get('source')} —{edge.get('name')}→ {edge.get('target')}"
            f"{f'；用途：{usage}' if usage else ''}"
        )
    for path in projection.get("cross_scene_analysis_paths", []):
        details = path.get("data", {}) or {}
        description = path.get("description")
        if description:
            lines.append(
                f"- 已确认跨场景分析路径（本次需核对条件）：{path.get('name')}：{description}"
            )
        lines.append(
            "  - 只有核对本次查询的时间和统计粒度均满足桥接条件后，才可进行该项分析；"
            "未满足或无法核对时，说明不能直接比较。"
        )
        if details.get("mapping_source"):
            lines.append(f"  - 映射依据：{details['mapping_source']}")
        if details.get("time_alignment_conditions"):
            lines.append(
                "  - 时间对齐条件：" + "、".join(details["time_alignment_conditions"])
            )
        if details.get("grain_alignment"):
            lines.append("  - 粒度对齐条件：" + "、".join(details["grain_alignment"]))
        if details.get("allowed_analyses"):
            lines.append("  - 允许分析：" + "、".join(details["allowed_analyses"]))
        if details.get("forbidden_inferences"):
            lines.append("  - 禁止推断：" + "、".join(details["forbidden_inferences"]))
        if details.get("caveats"):
            lines.append("  - 注意事项：" + "、".join(details["caveats"]))
        for profile in details.get("result_profiles", []):
            lines.append(
                f"  - 本次 {profile['scene_id']} 结果："
                f"粒度 {', '.join(_display_values(profile.get('grain'))) or '未识别'}；"
                f"时间粒度 {profile.get('time_grain') or '未识别'}；"
                f"时间依据 {profile.get('time_basis') or '未识别'}"
            )
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
    return sorted(
        matched,
        key=lambda item: (
            -int(item.get("id") in preferred_ids),
            -score(item),
            str(item.get("id") or ""),
        ),
    )[:limit]


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


def _matched_bindings_for_columns(
    edges: list[dict[str, Any]],
    scene_ids: set[str],
    result_columns: list[dict[str, Any]],
    selected_metric_ids: set[str],
) -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    for edge in edges:
        if edge.get("type") != "scene_binding":
            continue
        scene_id = str(edge.get("source") or "")
        metric_id = str(edge.get("target") or "")
        if scene_ids and scene_id not in scene_ids:
            continue
        if selected_metric_ids and metric_id not in selected_metric_ids:
            continue
        semantic_key = str((edge.get("data") or {}).get("semantic_key") or "")
        for column in result_columns:
            if not isinstance(column, dict):
                continue
            if str(column.get("type") or "").lower() != "metric":
                continue
            key = str(column.get("key") or "")
            column_semantic_key = str(column.get("semantic_key") or "")
            if semantic_key and semantic_key in {key, column_semantic_key}:
                bindings.append(
                    {
                        "scene_id": scene_id,
                        "result_key": key,
                        "semantic_key": semantic_key,
                        "metric_id": metric_id,
                        "matched_by": (
                            "semantic_key"
                            if column_semantic_key == semantic_key
                            else "result_key"
                        ),
                        "confidence": "exact",
                        "evidence": "scene_binding",
                    }
                )
    return sorted(
        bindings,
        key=lambda item: (item["scene_id"], item["result_key"], item["metric_id"]),
    )


def _column_is_bound(
    key: str,
    result_columns: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    scene_ids: set[str],
    selected_metric_ids: set[str],
) -> bool:
    column = next(
        (
            item
            for item in result_columns
            if isinstance(item, dict) and str(item.get("key") or "") == key
        ),
        {},
    )
    semantic_key = str(column.get("semantic_key") or "")
    for edge in edges:
        if edge.get("type") != "scene_binding":
            continue
        if scene_ids and str(edge.get("source") or "") not in scene_ids:
            continue
        if (
            selected_metric_ids
            and str(edge.get("target") or "") not in selected_metric_ids
        ):
            continue
        bound_key = str((edge.get("data") or {}).get("semantic_key") or "")
        if bound_key and bound_key in {key, semantic_key}:
            return True
    return False


def _split_values(value: str) -> set[str]:
    return {
        item.strip()
        for item in re.split(r"[,，、;\s]+", str(value or ""))
        if item.strip()
    }


def _display_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [
            item.strip() for item in re.split(r"[,，、;；]+", value) if item.strip()
        ]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _time_range_signature(value: Any) -> tuple[str, str, str] | str | None:
    if isinstance(value, dict):
        start = str(value.get("start") or "")
        end = str(value.get("end_exclusive") or value.get("end") or "")
        timezone = str(value.get("timezone") or "")
        return (start, end, timezone) if start and end else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = ["relevant_ontology_projection", "render_main_agent_ontology_context"]
