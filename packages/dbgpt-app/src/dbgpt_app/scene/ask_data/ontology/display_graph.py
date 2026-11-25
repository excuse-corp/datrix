"""Project structured references and reviewable rule mentions onto the graph."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from .schemas import OntologyEdge, OntologyGraph

RULE_REFERENCE = "rule_reference"
ATTRIBUTE_REFERENCE = "attribute_reference"

_REFERENCE_FIELDS = {
    "applicable_entities": ("entity", "适用实体"),
    "applies_to_entities": ("entity", "适用实体"),
    "applicable_metrics": ("metric", "适用指标"),
    "applies_to_metrics": ("metric", "适用指标"),
    "entities": ("entity", "关联实体"),
    "scenes": ("scene", "涉及场景"),
    "source_scene": ("scene", "来源场景"),
    "source_scenes": ("scene", "来源场景"),
}
_RULE_SCOPE_SUFFIX = re.compile(
    r"（(?P<scope>适用(?:实体|指标)[:：][^（）]+)）\s*[。.]?$"
)


def _values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [
            item.strip() for item in value if isinstance(item, str) and item.strip()
        ]
    return []


def _reference_id(kind: str, source: str, target: str, label: str) -> str:
    key = json.dumps([source, target, label], ensure_ascii=False).encode()
    return f"{kind}_{hashlib.sha256(key).hexdigest()[:32]}"


def with_display_references(
    graph: OntologyGraph, *, include_rule_mentions: bool = True
) -> OntologyGraph:
    nodes = {node.id: node for node in graph.nodes}
    edges = {edge.id: edge for edge in graph.edges}
    explicit_scene_sources = {
        (edge.source, edge.target) for edge in graph.edges if edge.type == "provided_by"
    }
    for edge in graph.edges:
        if edge.type == "scene_binding":
            explicit_scene_sources.add((edge.target, edge.source))

    mentions: dict[str, set[str]] = defaultdict(set)
    if include_rule_mentions:
        for node in graph.nodes:
            if node.type in {"entity", "metric"}:
                for term in (node.id, node.name, *node.aliases):
                    term = term.strip()
                    if len(term) >= 3:
                        mentions[term].add(node.id)
    terms = sorted(
        (term for term, ids in mentions.items() if len(ids) == 1),
        key=lambda term: (-len(term), term),
    )
    mention_pattern = (
        re.compile(
            "|".join(
                (
                    rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])"
                    if term.isascii()
                    else re.escape(term)
                )
                for term in terms
            )
        )
        if terms
        else None
    )

    for node in graph.nodes:

        def add_reference(
            target: str,
            label: str,
            field: str,
            evidence: str,
            kind: str = ATTRIBUTE_REFERENCE,
        ) -> None:
            if target == node.id:
                return
            edge_id = _reference_id(kind, node.id, target, label)
            if edge_id in edges:
                return
            edges[edge_id] = OntologyEdge(
                id=edge_id,
                source=node.id,
                target=target,
                type=kind,
                name=label,
                description=evidence,
                data={
                    "reference_field": field,
                    "reference_value": target,
                    "evidence": evidence,
                },
            )

        references = dict(node.data)
        rule_text = node.description
        if node.type == "analysis_rule":
            trigger = node.data.get("trigger")
            if trigger and trigger != rule_text:
                rule_text = f"{trigger}：{rule_text}"
        if node.type in {"rule", "analysis_rule"}:
            scope = _RULE_SCOPE_SUFFIX.search(rule_text)
            if scope:
                for item in re.split("[；;]", scope["scope"]):
                    field, _, value = item.partition("：" if "：" in item else ":")
                    key = {
                        "适用实体": "applies_to_entities",
                        "适用指标": "applies_to_metrics",
                    }.get(field.strip())
                    if key:
                        references[key] = [
                            *_values(references.get(key)),
                            *re.split("[、,，]", value),
                        ]
                rule_text = rule_text[: scope.start()]

        structured_targets = set()
        for field, (target_type, label) in _REFERENCE_FIELDS.items():
            for target in _values(references.get(field)):
                if target not in nodes or nodes[target].type != target_type:
                    continue
                structured_targets.add(target)
                if label == "来源场景" and (node.id, target) in explicit_scene_sources:
                    continue
                add_reference(target, label, field, target)

        for provenance in node.provenance:
            target = provenance.get("scene_id", "")
            if (
                target in nodes
                and nodes[target].type == "scene"
                and (node.id, target) not in explicit_scene_sources
            ):
                add_reference(target, "来源场景", "provenance.scene_id", target)

        formula = node.data.get("formula")
        if node.type == "metric" and isinstance(formula, str):
            for target in dict.fromkeys(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", formula)):
                if target in nodes and nodes[target].type == "metric":
                    add_reference(target, "公式引用", "formula", formula)

        if node.type not in {"rule", "analysis_rule"} or not mention_pattern:
            continue
        for match in mention_pattern.finditer(rule_text):
            target = next(iter(mentions[match[0]]))
            if target not in structured_targets:
                add_reference(
                    target,
                    "规则适用对象",
                    "规则原文",
                    rule_text,
                    RULE_REFERENCE,
                )

    return graph.model_copy(update={"edges": list(edges.values())})
