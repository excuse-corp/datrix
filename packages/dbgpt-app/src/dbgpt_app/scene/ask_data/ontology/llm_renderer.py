"""Deterministic Markdown renderer for LLM Ontology JSON."""

from __future__ import annotations

from .llm_schemas import GlobalOntologySynthesis
from .markdown import with_managed_frontmatter


def render_ontology_markdown_from_synthesis(
    synthesis: GlobalOntologySynthesis,
    *,
    revision: int,
) -> str:
    sections = [
        "# 企业业务本体",
        "",
        "## 业务实体",
        "",
        _table(
            ["ID", "领域", "名称", "别名", "定义", "业务分析角色", "来源场景", "证据"],
            [
                [
                    entity.id,
                    entity.domain_id or "",
                    entity.name,
                    _join(entity.aliases),
                    entity.definition,
                    entity.business_analysis_role,
                    _join(entity.source_scenes),
                    _evidence(entity.evidence),
                ]
                for entity in synthesis.entities
            ],
        ),
        "",
        "## 实体关系",
        "",
        _table(
            ["ID", "主体", "关系", "客体", "基数", "分析含义", "说明"],
            [
                [
                    relation.id,
                    relation.source_entity_id,
                    relation.relation,
                    relation.target_entity_id,
                    relation.cardinality or "",
                    relation.analysis_meaning,
                    _evidence(relation.evidence),
                ]
                for relation in synthesis.relations
            ],
        ),
        "",
        "## 指标",
        "",
        _table(
            [
                "ID",
                "名称",
                "领域",
                "所属实体",
                "单位",
                "聚合方式",
                "统计粒度",
                "时间依据",
                "可比性",
                "计算公式",
                "分析含义",
                "常见解读",
                "来源场景",
                "冲突说明",
            ],
            [
                [
                    metric.id,
                    metric.name,
                    metric.domain_id or "",
                    metric.entity_id,
                    metric.unit or "",
                    metric.aggregation or "",
                    _join(metric.grain),
                    metric.time_basis or "",
                    metric.comparability or "",
                    metric.formula or "",
                    metric.analysis_meaning,
                    _metric_interpretation(metric),
                    _join(metric.source_scenes),
                    metric.conflict_note or "无",
                ]
                for metric in synthesis.metrics
            ],
        ),
        "",
        "## 指标关系",
        "",
        _table(
            ["ID", "主指标", "关系", "关联指标", "分析用途"],
            [
                [
                    relation.id,
                    relation.primary_metric_id,
                    relation.relation,
                    relation.related_metric_id,
                    relation.analysis_usage,
                ]
                for relation in synthesis.metric_relations
            ],
        ),
        "",
        "## 分析维度",
        "",
        _table(
            ["维度", "适用实体", "适用指标", "分析用途", "注意事项"],
            [
                [
                    dimension.name,
                    _join(dimension.applicable_entities),
                    _join(dimension.applicable_metrics),
                    dimension.analysis_usage,
                    _join(dimension.caveats),
                ]
                for dimension in synthesis.analysis_dimensions
            ],
        ),
        "",
        "## 场景绑定",
        "",
        _table(
            ["场景", "Ontology 对象", "场景语义键"],
            [
                [binding.scene, metric.id, binding.semantic_key]
                for metric in synthesis.metrics
                for binding in metric.scene_bindings
            ],
        ),
        "",
        "## 跨场景分析路径",
        "",
        _table(
            [
                "ID",
                "状态",
                "关系类型",
                "分析主题",
                "涉及场景",
                "关联实体",
                "关联键",
                "映射依据",
                "左侧时间依据",
                "右侧时间依据",
                "时间对齐条件",
                "粒度对齐条件",
                "推荐分析方式",
                "允许分析",
                "禁止推断",
                "注意事项",
                "证据",
            ],
            [
                [
                    path.id,
                    path.status,
                    path.relation_type,
                    path.topic,
                    _join(path.scenes),
                    _join(path.entities),
                    _join(path.join_keys),
                    path.mapping_source or "",
                    path.left_time_basis or "",
                    path.right_time_basis or "",
                    _join(path.time_alignment_conditions),
                    _join(path.grain_alignment),
                    path.recommended_analysis,
                    _join(path.allowed_analyses),
                    _join(path.forbidden_inferences),
                    _join(path.caveats),
                    _evidence(path.evidence),
                ]
                for path in synthesis.cross_scene_analysis_paths
            ],
        ),
        "",
        "## 跨场景关联",
        "",
        _table(
            [
                "ID",
                "状态",
                "关系类型",
                "左侧场景",
                "右侧场景",
                "业务实体",
                "关联键",
                "映射依据",
                "左侧时间依据",
                "右侧时间依据",
                "时间对齐条件",
                "粒度",
                "粒度对齐条件",
                "业务含义",
                "允许分析",
                "禁止推断",
                "证据",
            ],
            _cross_scene_join_rows(synthesis),
        ),
        "",
        "## 查询结果分析规则",
        "",
        *_result_rule_lines(synthesis),
        "",
        "## 需求澄清规则",
        "",
        *_clarification_rule_lines(synthesis),
        "",
    ]
    return with_managed_frontmatter("\n".join(sections), revision)


def _metric_interpretation(metric) -> str:
    parts = [metric.interpretation]
    if metric.common_anomalies:
        parts.append(f"常见异常：{_join(metric.common_anomalies)}")
    if metric.recommended_dimensions:
        parts.append(f"建议维度：{_join(metric.recommended_dimensions)}")
    if metric.positive_direction:
        parts.append(f"方向：{metric.positive_direction}")
    return "；".join(part for part in parts if part)


def _cross_scene_join_rows(synthesis: GlobalOntologySynthesis) -> list[list[str]]:
    rows: list[list[str]] = []
    for path in synthesis.cross_scene_analysis_paths:
        if len(path.scenes) < 2 or not path.join_keys:
            continue
        left, right = path.scenes[0], path.scenes[1]
        entity = path.entities[0] if path.entities else ""
        rows.append(
            [
                f"{path.id}_join",
                path.status,
                path.relation_type,
                left,
                right,
                entity,
                _join(path.join_keys),
                path.mapping_source or "",
                path.left_time_basis or "",
                path.right_time_basis or "",
                _join(path.time_alignment_conditions),
                _join(path.grain_alignment) or "待确认",
                _join(path.grain_alignment),
                path.recommended_analysis,
                _join(path.allowed_analyses),
                _join(path.forbidden_inferences),
                _evidence(path.evidence),
            ]
        )
    return rows


def _result_rule_lines(synthesis: GlobalOntologySynthesis) -> list[str]:
    if not synthesis.result_analysis_rules:
        return [
            "- 当查询结果返回后，先回答用户问题，"
            "再结合指标含义解释总量、结构、异常和建议。"
        ]
    return [
        (
            f"- {rule.trigger}：{rule.guidance}"
            f"{_applies_suffix(rule.applies_to_entities, rule.applies_to_metrics)}"
        )
        for rule in synthesis.result_analysis_rules
    ]


def _clarification_rule_lines(synthesis: GlobalOntologySynthesis) -> list[str]:
    if not synthesis.clarification_rules:
        return ["- 当用户问题缺少指标口径、时间口径或归属口径时，先澄清再分析。"]
    return [f"- {item}" for item in synthesis.clarification_rules]


def _applies_suffix(entities: list[str], metrics: list[str]) -> str:
    parts = []
    if entities:
        parts.append(f"适用实体：{_join(entities)}")
    if metrics:
        parts.append(f"适用指标：{_join(metrics)}")
    return f"（{'；'.join(parts)}）" if parts else ""


def _evidence(items) -> str:
    values = []
    for item in items:
        quote = item.quote.strip().replace("\n", " ")
        values.append(f"{item.scene_id}: {quote}")
    return "；".join(values)


def _join(values: list[str]) -> str:
    return "、".join(str(item).strip() for item in values if str(item).strip())


def _table(headers: list[str], rows: list[list[str]]) -> str:
    safe_headers = [_escape_cell(item) for item in headers]
    lines = [
        "| " + " | ".join(safe_headers) + " |",
        "| " + " | ".join("---" for _ in safe_headers) + " |",
    ]
    for row in rows:
        cells = [_escape_cell(item) for item in row]
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        lines.append("| " + " | ".join(cells[: len(headers)]) + " |")
    return "\n".join(lines)


def _escape_cell(value: object) -> str:
    text = str(value or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text.replace("|", "/").strip()


__all__ = ["render_ontology_markdown_from_synthesis"]
