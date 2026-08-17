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

# A global Ontology begins without sample business content. Server-owned
# revision metadata lives in storage/API fields, not in the editable Markdown.
DEFAULT_ONTOLOGY_MARKDOWN = "# 企业业务本体\n"


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

_FRONTMATTER = re.compile(
    r"\A\ufeff?\s*---\s*\n.*?\n\s*---\s*(?:\n|\Z)", re.DOTALL
)
_EMPTY_CONFLICT_VALUES = {"", "无", "none", "n/a", "na", "-"}
_NON_BUSINESS_SECTION_HEADERS = {
    "待管理员确认事项",
    "管理员确认事项",
    "草稿说明",
    "生成说明",
}
_NON_BUSINESS_BOILERPLATE_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"本草稿",
        r"草稿版?本体",
        r"全局业务本体草稿",
        r"由已激活场景.*生成",
        r"通过\s*LLM\s*生成",
        r"请人工确认",
        r"待管理员确认",
    )
]


def with_managed_frontmatter(document: str, revision: int) -> str:
    """Strip legacy frontmatter; identity and revision are server-owned fields."""
    del revision
    body = _FRONTMATTER.sub("", document.replace("\r\n", "\n").replace("\r", "\n"))
    body = _strip_non_business_boilerplate(body)
    body = body.lstrip("\n")
    return body if body.strip() else DEFAULT_ONTOLOGY_MARKDOWN


def _strip_non_business_boilerplate(document: str) -> str:
    """Remove generation metadata that would pollute analysis prompts."""
    lines = document.split("\n")
    kept: list[str] = []
    skipping_section = False
    for line in lines:
        heading = re.match(r"^(#{2,6})\s+(.+?)\s*$", line)
        if heading:
            title = heading.group(2).strip()
            if title in _NON_BUSINESS_SECTION_HEADERS:
                skipping_section = True
                continue
            skipping_section = False
        if skipping_section:
            continue
        if _is_non_business_boilerplate_line(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def _is_non_business_boilerplate_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("|"):
        return False
    return any(
        pattern.search(stripped) for pattern in _NON_BUSINESS_BOILERPLATE_PATTERNS
    )


def is_legacy_default_ontology(document: str) -> bool:
    """Identify only the untouched bootstrap sample used by older releases."""
    return document.strip() == LEGACY_DEFAULT_ONTOLOGY_MARKDOWN.strip()


def _normalize_header(value: str) -> str:
    return re.sub(r"[\s_（）()：:]", "", value).lower()


def _split_row(line: str) -> list[str]:
    return [item.strip() for item in line.strip().strip("|").split("|")]


class OntologyMarkdownParser:
    """The Markdown document is readable by people and deterministic for services."""

    compiler_version = "2"

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
        graph = self._build_graph(
            tables,
            self._rule_lines(normalized),
            self._bullet_lines(normalized, "查询结果分析规则"),
            issues,
        )
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
        analysis_rules: list[tuple[str, int]],
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
                    data={
                        "analysis_role": cell(row, "业务分析角色", "分析角色"),
                        "source_scenes": self._aliases(cell(row, "来源场景", "场景")),
                        "evidence": cell(row, "证据"),
                    },
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
            conflict = cell(row, "冲突说明", "冲突")
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
                        "analysis_meaning": cell(row, "分析含义"),
                        "interpretation": cell(row, "常见解读", "解读"),
                        "common_anomalies": cell(row, "常见异常", "异常"),
                        "recommended_dimensions": cell(row, "推荐分析维度", "建议维度"),
                        "conflict_note": conflict,
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
                    data={
                        "cardinality": cell(row, "基数"),
                        "analysis_meaning": cell(row, "分析含义"),
                    },
                ),
                line,
            )

        for row, line in tables.get("指标关系", []):
            identifier = cell(row, "ID")
            primary = cell(row, "主指标")
            related = cell(row, "关联指标")
            relation = cell(row, "关系")
            usage = cell(row, "分析用途", "用途")
            if not identifier or not primary or not related or not relation:
                issues.append(
                    OntologyValidationIssue(
                        code="METRIC_RELATION_REQUIRED",
                        message="指标关系必须填写 ID、主指标、关系和关联指标。",
                        line=line,
                    )
                )
                continue
            self._add_edge(
                edges,
                issues,
                OntologyEdge(
                    id=identifier,
                    source=primary,
                    target=related,
                    type="metric_relation",
                    name=relation,
                    description=usage,
                    data={"analysis_usage": usage},
                ),
                line,
            )

        for row, line in tables.get("分析维度", []):
            name = cell(row, "维度", "名称")
            if not name:
                issues.append(
                    OntologyValidationIssue(
                        code="ANALYSIS_DIMENSION_REQUIRED",
                        message="分析维度必须填写维度名称。",
                        line=line,
                    )
                )
                continue
            identifier = (
                cell(row, "ID") or f"analysis_dimension_{self._stable_id(name)}"
            )
            add_node(
                OntologyNode(
                    id=identifier,
                    type="analysis_dimension",
                    name=name,
                    description=cell(row, "分析用途", "用途"),
                    data={
                        "applicable_entities": self._aliases(cell(row, "适用实体")),
                        "applicable_metrics": self._aliases(cell(row, "适用指标")),
                        "caveats": self._aliases(cell(row, "注意事项", "说明")),
                    },
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

        for row, line in tables.get("跨场景分析路径", []):
            identifier = cell(row, "ID")
            topic = cell(row, "分析主题", "主题")
            if not identifier or not topic:
                issues.append(
                    OntologyValidationIssue(
                        code="ANALYSIS_PATH_REQUIRED",
                        message="跨场景分析路径必须填写 ID 和分析主题。",
                        line=line,
                    )
                )
                continue
            add_node(
                OntologyNode(
                    id=identifier,
                    type="analysis_path",
                    name=topic,
                    description=cell(row, "推荐分析方式", "推荐分析"),
                    data={
                        "scenes": self._aliases(cell(row, "涉及场景", "场景")),
                        "entities": self._aliases(cell(row, "关联实体", "实体")),
                        "join_keys": self._aliases(cell(row, "关联键")),
                        "caveats": self._aliases(cell(row, "注意事项", "说明")),
                    },
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

        for number, (text, line) in enumerate(analysis_rules, start=1):
            rule_id = f"analysis_rule_{number}"
            trigger, guidance = self._split_rule(text)
            add_node(
                OntologyNode(
                    id=rule_id,
                    type="analysis_rule",
                    name=trigger or f"查询结果分析规则 {number}",
                    description=guidance or text,
                    data={"trigger": trigger, "guidance": guidance or text},
                ),
                line,
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
    def _stable_id(value: str) -> str:
        text = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]+", "_", str(value).strip())
        text = re.sub(r"_+", "_", text).strip("_").lower()
        return text or "item"

    @staticmethod
    def _split_rule(value: str) -> tuple[str, str]:
        parts = re.split(r"[：:]", value, maxsplit=1)
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
        return value.strip(), value.strip()

    @staticmethod
    def _bullet_lines(document: str, section: str) -> list[tuple[str, int]]:
        lines = document.split("\n")
        in_section = False
        rules: list[tuple[str, int]] = []
        for line_number, line in enumerate(lines, start=1):
            heading = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
            if heading:
                in_section = heading.group(1).strip() == section
                continue
            if in_section:
                match = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
                if match:
                    rules.append((match.group(1), line_number))
        return rules

    @staticmethod
    def _rule_lines(document: str) -> list[tuple[str, int]]:
        """Rules stay prose but become visible to the main Agent as guidance."""
        return OntologyMarkdownParser._bullet_lines(document, "需求澄清规则")


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
    edges = [edge.model_dump(mode="json") for edge in graph.edges]
    metric_relations = [edge for edge in edges if edge.get("type") == "metric_relation"]
    relation_edges = [edge for edge in edges if edge.get("type") == "relation"]
    cross_scene_join_edges = [
        edge for edge in edges if edge.get("type") == "cross_scene_join"
    ]
    return {
        "entities": by_type["entity"],
        "metrics": by_type["metric"],
        "relations": relation_edges,
        "scenes": by_type["scene"],
        "rules": by_type["rule"],
        "clarification_rules": [
            node.get("description", "") for node in by_type["rule"]
        ],
        "analysis_dimensions": by_type["analysis_dimension"],
        "metric_relations": metric_relations,
        "cross_scene_analysis_paths": by_type["analysis_path"],
        "cross_scene_joins": cross_scene_join_edges,
        "result_analysis_rules": by_type["analysis_rule"],
        "edges": edges,
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
