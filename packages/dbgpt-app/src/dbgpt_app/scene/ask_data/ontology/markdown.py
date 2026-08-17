"""Parse the editable global Ontology Markdown into a deterministic graph."""

from __future__ import annotations

import re
from collections import defaultdict

from .schemas import (
    OntologyCompilation,
    OntologyEdge,
    OntologyGraph,
    OntologyNode,
    OntologyValidationIssue,
)

LEGACY_DEFAULT_ONTOLOGY_MARKDOWN = """---
schema_version: "1"
ontology_id: "global_business"
---

# 企业业务本体

> 本文档描述主 Agent 可理解的业务概念、关系、指标与场景能力。
> 它不保存实时数据，也不能包含 SQL 或连接信息。

## 业务实体

| ID | 名称 | 别名 | 定义 |
| --- | --- | --- | --- |
| customer | 客户 | 客户单位、甲方 | 与公司发生业务关系的主体 |
| contract | 合同 | 协议 | 与客户签订的有效业务协议 |

## 实体关系

| ID | 主体 | 关系 | 客体 | 基数 | 说明 |
| --- | --- | --- | --- | --- |
| customer_signs_contract | customer | 签订 | contract | 1:N | 客户可以签订多份合同 |

## 指标

| ID | 名称 | 所属实体 | 单位 | 计算公式 | 来源场景 |
| --- | --- | --- | --- | --- |
| contract_amount | 合同金额 | contract | 元 |  | contract_analysis |

## 场景绑定

| 场景 | Ontology 对象 | 场景语义键 |
| --- | --- | --- |
| contract_analysis | contract_amount | contract_amount |

## 跨场景关联

| ID | 左侧场景 | 右侧场景 | 业务实体 | 关联键 | 粒度 |
| --- | --- | --- | --- | --- |

## 需求澄清规则

- 查询合同金额且未给出时间口径时，询问使用签约日期、生效日期还是其他业务日期。
"""

# A global Ontology begins with identity only. Business objects are generated
# from accepted active Scene Snapshots, never invented by a sample document.
DEFAULT_ONTOLOGY_MARKDOWN = """---
schema_version: "1"
ontology_id: "global_business"
revision: 1
---
"""


class OntologyMarkdownError(ValueError):
    def __init__(self, code: str, message: str, line: int | None = None):
        self.code = code
        self.line = line
        super().__init__(message)


_FORBIDDEN_TERMS = re.compile(
    r"\b(?:select|insert|update|delete|drop|alter|truncate|exec|execute|"
    r"connection_string|password|api[_-]?key|python|bash|shell|powershell|"
    r"subprocess|os\.system|curl|wget)\b",
    re.IGNORECASE,
)

_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", re.DOTALL)


def with_managed_frontmatter(document: str, revision: int) -> str:
    """Keep identity and revision server-owned while preserving the user body."""
    body = _FRONTMATTER.sub("", document.replace("\r\n", "\n").replace("\r", "\n"))
    header = (
        "---\n"
        'schema_version: "1"\n'
        'ontology_id: "global_business"\n'
        f"revision: {revision}\n"
        "---\n\n"
    )
    return header + body.lstrip("\n")


def is_legacy_default_ontology(document: str) -> bool:
    """Identify only the untouched bootstrap sample used by older releases."""
    return document.strip() == LEGACY_DEFAULT_ONTOLOGY_MARKDOWN.strip()


def _normalize_header(value: str) -> str:
    return re.sub(r"[\s_（）()：:]", "", value).lower()


def _split_row(line: str) -> list[str]:
    return [item.strip() for item in line.strip().strip("|").split("|")]


class OntologyMarkdownParser:
    """The Markdown document is readable by people and deterministic for services."""

    compiler_version = "1"

    def compile(self, document: str) -> OntologyCompilation:
        if not isinstance(document, str) or not document.strip():
            raise OntologyMarkdownError("EMPTY_DOCUMENT", "本体文档不能为空。")
        normalized = document.replace("\r\n", "\n").replace("\r", "\n")
        issues: list[OntologyValidationIssue] = []
        for line_number, line in enumerate(normalized.split("\n"), start=1):
            if _FORBIDDEN_TERMS.search(line):
                issues.append(
                    OntologyValidationIssue(
                        code="FORBIDDEN_CONTENT",
                        message="本体文档不能包含 SQL、连接信息或可执行配置。",
                        line=line_number,
                    )
                )
        tables = self._tables(normalized, issues)
        graph = self._build_graph(tables, self._rule_lines(normalized), issues)
        projection = build_prompt_projection(graph)
        return OntologyCompilation(
            graph=graph, issues=issues, prompt_projection=projection
        )

    def _tables(
        self, document: str, issues: list[OntologyValidationIssue]
    ) -> dict[str, list[tuple[dict[str, str], int]]]:
        lines = document.split("\n")
        tables: dict[str, list[tuple[dict[str, str], int]]] = {}
        for index, line in enumerate(lines):
            heading = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
            if not heading:
                continue
            title = heading.group(1).strip()
            rows: list[str] = []
            row_lines: list[int] = []
            for offset, candidate in enumerate(lines[index + 1 :], start=index + 2):
                if re.match(r"^#{1,3}\s+", candidate):
                    break
                if candidate.strip().startswith("|"):
                    rows.append(candidate)
                    row_lines.append(offset)
            if len(rows) < 2:
                continue
            headers = [_normalize_header(item) for item in _split_row(rows[0])]
            separator = _split_row(rows[1])
            if not headers or not all(
                re.fullmatch(r":?-{3,}:?", item) for item in separator
            ):
                issues.append(
                    OntologyValidationIssue(
                        code="INVALID_TABLE",
                        message=f"“{title}”表格缺少合法的表头分隔行。",
                        line=row_lines[1] if len(row_lines) > 1 else index + 1,
                    )
                )
                continue
            records = []
            for row, line_number in zip(rows[2:], row_lines[2:]):
                cells = _split_row(row)
                if len(cells) != len(headers):
                    issues.append(
                        OntologyValidationIssue(
                            code="INVALID_TABLE_ROW",
                            message=f"“{title}”表格列数与表头不一致。",
                            line=line_number,
                        )
                    )
                    continue
                records.append((dict(zip(headers, cells)), line_number))
            tables[title] = records
        return tables

    def _build_graph(
        self,
        tables: dict[str, list[tuple[dict[str, str], int]]],
        rules: list[tuple[str, int]],
        issues: list[OntologyValidationIssue],
    ) -> OntologyGraph:
        nodes: dict[str, OntologyNode] = {}
        edges: dict[str, OntologyEdge] = {}

        def cell(row: dict[str, str], *names: str) -> str:
            for name in names:
                value = row.get(_normalize_header(name), "").strip()
                if value:
                    return value
            return ""

        def add_node(node: OntologyNode, line: int) -> None:
            if node.id in nodes:
                issues.append(
                    OntologyValidationIssue(
                        code="DUPLICATE_NODE_ID",
                        message=f"重复的 Ontology ID：{node.id}。",
                        line=line,
                        path=node.id,
                    )
                )
                return
            nodes[node.id] = node

        def add_scene(scene_id: str, line: int) -> None:
            if scene_id and scene_id not in nodes:
                add_node(OntologyNode(id=scene_id, type="scene", name=scene_id), line)

        for row, line in tables.get("业务实体", []):
            identifier = cell(row, "ID")
            name = cell(row, "名称")
            if not identifier or not name:
                issues.append(
                    OntologyValidationIssue(
                        code="ENTITY_REQUIRED",
                        message="业务实体必须填写 ID 和名称。",
                        line=line,
                    )
                )
                continue
            add_node(
                OntologyNode(
                    id=identifier,
                    type="entity",
                    name=name,
                    aliases=self._aliases(cell(row, "别名")),
                    description=cell(row, "定义", "说明"),
                ),
                line,
            )

        for row, line in tables.get("指标", []):
            identifier = cell(row, "ID")
            name = cell(row, "名称")
            entity = cell(row, "所属实体", "实体")
            if not identifier or not name or not entity:
                issues.append(
                    OntologyValidationIssue(
                        code="METRIC_REQUIRED",
                        message="指标必须填写 ID、名称和所属实体。",
                        line=line,
                    )
                )
                continue
            source_scene = cell(row, "来源场景", "场景")
            add_node(
                OntologyNode(
                    id=identifier,
                    type="metric",
                    name=name,
                    data={
                        "entity": entity,
                        "unit": cell(row, "单位"),
                        "formula": cell(row, "计算公式", "公式"),
                        "zero_policy": cell(row, "零值处理", "零值策略"),
                        "source_scene": source_scene,
                    },
                ),
                line,
            )
            if source_scene:
                add_scene(source_scene, line)
                self._add_edge(
                    edges,
                    issues,
                    OntologyEdge(
                        id=f"provided_by_{identifier}_{source_scene}",
                        source=identifier,
                        target=source_scene,
                        type="provided_by",
                        name="由场景提供",
                    ),
                    line,
                )
            if entity:
                self._add_edge(
                    edges,
                    issues,
                    OntologyEdge(
                        id=f"belongs_to_{identifier}_{entity}",
                        source=identifier,
                        target=entity,
                        type="belongs_to",
                        name="属于实体",
                    ),
                    line,
                )

        for row, line in tables.get("实体关系", []):
            identifier = cell(row, "ID")
            source = cell(row, "主体")
            target = cell(row, "客体")
            relation = cell(row, "关系")
            if not identifier or not source or not target or not relation:
                issues.append(
                    OntologyValidationIssue(
                        code="RELATION_REQUIRED",
                        message="实体关系必须填写 ID、主体、关系和客体。",
                        line=line,
                    )
                )
                continue
            self._add_edge(
                edges,
                issues,
                OntologyEdge(
                    id=identifier,
                    source=source,
                    target=target,
                    type="relation",
                    name=relation,
                    description=cell(row, "说明", "定义"),
                    data={"cardinality": cell(row, "基数")},
                ),
                line,
            )

        for row, line in tables.get("场景绑定", []):
            scene = cell(row, "场景")
            object_id = cell(row, "Ontology对象", "Ontology 对象", "对象")
            semantic_key = cell(row, "场景语义键", "语义键")
            if not scene or not object_id or not semantic_key:
                issues.append(
                    OntologyValidationIssue(
                        code="SCENE_BINDING_REQUIRED",
                        message="场景绑定必须填写场景、Ontology 对象和场景语义键。",
                        line=line,
                    )
                )
                continue
            add_scene(scene, line)
            self._add_edge(
                edges,
                issues,
                OntologyEdge(
                    id=f"binding_{scene}_{object_id}",
                    source=scene,
                    target=object_id,
                    type="scene_binding",
                    name="场景语义绑定",
                    data={"semantic_key": semantic_key},
                ),
                line,
            )

        for row, line in tables.get("跨场景关联", []):
            identifier = cell(row, "ID")
            left = cell(row, "左侧场景")
            right = cell(row, "右侧场景")
            if not identifier or not left or not right:
                issues.append(
                    OntologyValidationIssue(
                        code="CROSS_SCENE_JOIN_REQUIRED",
                        message="跨场景关联必须填写 ID、左侧场景和右侧场景。",
                        line=line,
                    )
                )
                continue
            business_key = cell(row, "关联键")
            grain = cell(row, "粒度")
            if not business_key or not grain:
                issues.append(
                    OntologyValidationIssue(
                        code="CROSS_SCENE_JOIN_GRAIN_REQUIRED",
                        message="跨场景关联必须明确关联键和粒度。",
                        line=line,
                        path=identifier,
                    )
                )
            add_scene(left, line)
            add_scene(right, line)
            self._add_edge(
                edges,
                issues,
                OntologyEdge(
                    id=identifier,
                    source=left,
                    target=right,
                    type="cross_scene_join",
                    name="跨场景关联",
                    data={
                        "entity": cell(row, "业务实体", "实体"),
                        "business_key": business_key,
                        "grain": grain,
                    },
                ),
                line,
            )

        for number, (text, line) in enumerate(rules, start=1):
            rule_id = f"clarification_rule_{number}"
            add_node(
                OntologyNode(
                    id=rule_id,
                    type="rule",
                    name=f"需求澄清规则 {number}",
                    description=text,
                ),
                line,
            )

        self._validate_metric_formulas(nodes, issues)
        for edge in edges.values():
            if edge.source not in nodes:
                issues.append(
                    OntologyValidationIssue(
                        code="UNKNOWN_RELATION_SOURCE",
                        message=f"关系 {edge.id} 的主体不存在：{edge.source}。",
                        path=edge.id,
                    )
                )
            if edge.target not in nodes:
                issues.append(
                    OntologyValidationIssue(
                        code="UNKNOWN_RELATION_TARGET",
                        message=f"关系 {edge.id} 的客体不存在：{edge.target}。",
                        path=edge.id,
                    )
                )
        return OntologyGraph(nodes=list(nodes.values()), edges=list(edges.values()))

    @staticmethod
    def _validate_metric_formulas(
        nodes: dict[str, OntologyNode], issues: list[OntologyValidationIssue]
    ) -> None:
        """Formulae remain declarative and may reference only declared metrics."""
        metrics = {
            node_id: node for node_id, node in nodes.items() if node.type == "metric"
        }
        dependencies: dict[str, set[str]] = {}
        for metric_id, node in metrics.items():
            details = node.data or {}
            formula = str(details.get("formula") or "").strip()
            if not formula:
                continue
            references = {
                token
                for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", formula)
                if token.lower()
                not in {"nullif", "case", "when", "then", "else", "end", "zero"}
            }
            unknown = sorted(
                reference for reference in references if reference not in metrics
            )
            if unknown:
                issues.append(
                    OntologyValidationIssue(
                        code="UNKNOWN_FORMULA_METRIC",
                        message=(
                            f"指标 {metric_id} 的公式引用了不存在的指标："
                            f"{', '.join(unknown)}。"
                        ),
                        path=metric_id,
                    )
                )
            dependencies[metric_id] = references & set(metrics)
            if "/" in formula and not str(details.get("zero_policy") or "").strip():
                issues.append(
                    OntologyValidationIssue(
                        code="RATIO_ZERO_POLICY_REQUIRED",
                        message=f"比率指标 {metric_id} 必须定义零值处理。",
                        path=metric_id,
                    )
                )
            if "+" in formula or "-" in formula:
                referenced_units = {
                    str(metrics[reference].data.get("unit") or "")
                    for reference in dependencies[metric_id]
                }
                if len(referenced_units) > 1 or "" in referenced_units:
                    issues.append(
                        OntologyValidationIssue(
                            code="FORMULA_UNIT_MISMATCH",
                            message=(
                                f"指标 {metric_id} 的加减公式使用了不一致或缺失的单位。"
                            ),
                            path=metric_id,
                        )
                    )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(metric_id: str) -> None:
            if metric_id in visiting:
                issues.append(
                    OntologyValidationIssue(
                        code="CYCLIC_METRIC_FORMULA",
                        message=f"指标公式存在循环引用：{metric_id}。",
                        path=metric_id,
                    )
                )
                return
            if metric_id in visited:
                return
            visiting.add(metric_id)
            for dependency in dependencies.get(metric_id, set()):
                visit(dependency)
            visiting.remove(metric_id)
            visited.add(metric_id)

        for metric_id in dependencies:
            visit(metric_id)

    @staticmethod
    def _add_edge(
        edges: dict[str, OntologyEdge],
        issues: list[OntologyValidationIssue],
        edge: OntologyEdge,
        line: int,
    ) -> None:
        if edge.id in edges:
            issues.append(
                OntologyValidationIssue(
                    code="DUPLICATE_EDGE_ID",
                    message=f"重复的关系 ID：{edge.id}。",
                    line=line,
                    path=edge.id,
                )
            )
            return
        edges[edge.id] = edge

    @staticmethod
    def _aliases(value: str) -> list[str]:
        return [item.strip() for item in re.split(r"[,，、\n]", value) if item.strip()]

    @staticmethod
    def _rule_lines(document: str) -> list[tuple[str, int]]:
        """Rules stay prose but become visible to the main Agent as guidance."""
        lines = document.split("\n")
        in_section = False
        rules: list[tuple[str, int]] = []
        for line_number, line in enumerate(lines, start=1):
            heading = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
            if heading:
                in_section = heading.group(1).strip() == "需求澄清规则"
                continue
            if in_section:
                match = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
                if match:
                    rules.append((match.group(1), line_number))
        return rules


def build_prompt_projection(graph: OntologyGraph) -> dict:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for node in graph.nodes:
        by_type[node.type].append(
            {
                "id": node.id,
                "name": node.name,
                "aliases": node.aliases,
                "description": node.description,
                "data": node.data,
            }
        )
    return {
        "entities": by_type["entity"],
        "metrics": by_type["metric"],
        "scenes": by_type["scene"],
        "rules": by_type["rule"],
        "relations": [edge.model_dump(mode="json") for edge in graph.edges],
    }


__all__ = [
    "DEFAULT_ONTOLOGY_MARKDOWN",
    "LEGACY_DEFAULT_ONTOLOGY_MARKDOWN",
    "OntologyMarkdownError",
    "OntologyMarkdownParser",
    "build_prompt_projection",
    "is_legacy_default_ontology",
    "with_managed_frontmatter",
]
