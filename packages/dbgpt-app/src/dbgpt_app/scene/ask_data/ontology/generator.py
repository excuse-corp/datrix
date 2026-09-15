"""Generate a reviewable global Ontology Markdown draft from active Scene docs."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from ..scene.markdown import SemanticMarkdownParser, split_scene_documents
from ..schemas.semantic import ParsedSemanticDocument, SemanticConfig
from .markdown import with_managed_frontmatter

_METRIC_HINTS = (
    "金额",
    "数量",
    "总数",
    "合计",
    "均值",
    "平均",
    "比例",
    "比率",
    "率",
    "预算",
    "成本",
    "费用",
    "收入",
    "回款",
    "amount",
    "count",
    "qty",
    "quantity",
    "rate",
    "ratio",
    "total",
    "sum",
    "cost",
    "revenue",
    "price",
    "budget",
    "fee",
)

_KEY_HINTS = ("id", "code", "no", "key", "编号", "编码", "标识")
_ENTITY_RULES = (
    (
        "project",
        "项目",
        ("project", "项目", "xmk", "xm_"),
        "项目、项目阶段、项目责任人及项目相关合同的业务对象。",
    ),
    (
        "contract",
        "合同",
        ("contract", "合同", "ht_"),
        "合同、协议及合同金额等合同管理业务对象。",
    ),
    (
        "department",
        "部门",
        ("department", "dept", "部门", "科室", "组织"),
        "组织、部门及部门归属口径。",
    ),
    (
        "person",
        "人员",
        (
            "person",
            "employee",
            "manager",
            "contact",
            "人员",
            "员工",
            "负责人",
            "联系人",
        ),
        "人员、员工、负责人及联系人角色。",
    ),
    (
        "customer",
        "客户",
        ("customer", "client", "客户", "甲方"),
        "客户、甲方及外部业务往来主体。",
    ),
    (
        "supplier",
        "供应商",
        ("supplier", "vendor", "供应商", "乙方"),
        "供应商、乙方及外部服务提供主体。",
    ),
)


def generate_ontology_markdown_from_scene_sources(
    scene_sources: list[Any], *, revision: int
) -> str:
    """Build a deterministic draft; humans still own final entity/relation approval."""
    sources = [_normalize_source(source) for source in scene_sources]
    sources = [source for source in sources if source["scene_id"]]
    sources.sort(key=lambda item: item["scene_id"])

    entities: dict[str, dict[str, str]] = {}
    metrics: dict[str, dict[str, Any]] = {}
    relation_rows: list[list[str]] = []
    binding_rows: list[list[str]] = []
    cross_scene_rows: list[list[str]] = []
    fields_by_scene: dict[str, list[dict[str, str]]] = {}
    entities_by_scene: dict[str, set[str]] = {}

    for source in sources:
        parsed = _parse_semantic(source.get("semantic_md") or "")
        config = parsed.config if parsed else None
        documents = _source_documents(source, parsed)
        field_rows = _dictionary_rows(documents.get("data_dictionary_md", ""))
        fields_by_scene[source["scene_id"]] = field_rows
        scene_entities = _entity_candidates(source, config, documents, field_rows)
        entities_by_scene[source["scene_id"]] = {item["id"] for item in scene_entities}
        for entity in scene_entities:
            _merge_entity(entities, entity)
        primary_entity_id = (
            scene_entities[0]["id"] if scene_entities else "business_entity"
        )

        for metric in _metric_candidates(config, field_rows):
            entity_id = metric.get("entity_id") or _metric_entity_id(
                metric, scene_entities, primary_entity_id
            )
            metric_id = _metric_id(entity_id, metric["key"])
            item = metrics.setdefault(
                metric_id,
                {
                    "id": metric_id,
                    "name": metric["name"],
                    "entity_id": entity_id,
                    "unit": metric.get("unit", ""),
                    "formula": metric.get("formula", ""),
                    "sources": set(),
                },
            )
            item["sources"].add(source["scene_id"])
            semantic_key = metric.get("semantic_key") or metric_id
            if semantic_key:
                binding_rows.append([source["scene_id"], metric_id, semantic_key])

    relation_rows.extend(_entity_relation_candidates(entities_by_scene))
    cross_scene_rows.extend(_cross_scene_candidates(fields_by_scene))

    entity_rows = [
        [
            item["id"],
            item["name"],
            item.get("aliases", ""),
            item.get("definition", ""),
            "、".join(
                sorted(
                    scene_id
                    for scene_id, entity_ids in entities_by_scene.items()
                    if item["id"] in entity_ids
                )
            ),
            "语义文档标题、业务语义或字段说明命中",
        ]
        for item in sorted(entities.values(), key=lambda value: value["id"])
    ]
    metric_rows = [
        [
            item["id"],
            item["name"],
            item["entity_id"],
            item.get("unit", ""),
            item.get("formula", ""),
            "、".join(sorted(item["sources"])),
            "无",
        ]
        for item in sorted(metrics.values(), key=lambda value: value["id"])
    ]

    sections = [
        "# 企业业务本体",
        "",
        "## 业务实体",
        "",
        _table(["ID", "名称", "别名", "定义", "来源场景", "证据"], entity_rows),
        "",
        "## 实体关系",
        "",
        _table(["ID", "主体", "关系", "客体", "基数", "说明"], relation_rows),
        "",
        "## 指标",
        "",
        _table(
            [
                "ID",
                "名称",
                "所属实体",
                "单位",
                "计算公式",
                "来源场景",
                "冲突说明",
            ],
            metric_rows,
        ),
        "",
        "## 场景绑定",
        "",
        _table(["场景", "Ontology 对象", "场景语义键"], sorted(binding_rows)),
        "",
        "## 跨场景关联",
        "",
        _table(
            [
                "ID",
                "左侧场景",
                "右侧场景",
                "业务实体",
                "关联键",
                "粒度",
                "业务含义",
            ],
            cross_scene_rows,
        ),
        "",
        "## 需求澄清规则",
        "",
        "- 当用户问题涉及多个候选业务实体或跨场景关系时，先确认业务口径和关联粒度。",
        "- 当比率、回款、预算等指标缺少分母、时间口径或零值处理时，先澄清再分析。",
        "",
    ]
    return with_managed_frontmatter("\n".join(sections), revision)


def _merge_entity(entities: dict[str, dict[str, str]], entity: dict[str, str]) -> None:
    current = entities.get(entity["id"])
    if current is None:
        entities[entity["id"]] = entity
        return
    aliases = {
        item.strip()
        for item in (
            f"{current.get('aliases', '')}、{entity.get('aliases', '')}".split("、")
        )
        if item.strip()
    }
    current["aliases"] = "、".join(sorted(aliases))
    if not current.get("definition") and entity.get("definition"):
        current["definition"] = entity["definition"]


def _entity_candidates(
    source: dict[str, Any],
    config: SemanticConfig | None,
    documents: dict[str, str],
    field_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    text_parts = [
        source.get("scene_id", ""),
        source.get("name", ""),
        source.get("description", ""),
        documents.get("business_semantics_md", ""),
        " ".join(f"{row.get('field', '')} {row.get('name', '')}" for row in field_rows),
    ]
    haystack = " ".join(str(part) for part in text_parts)
    primary_id = _candidate_entity_id(source, config)
    candidates: dict[str, dict[str, str]] = {}
    for entity_id, name, tokens, definition in _ENTITY_RULES:
        if not any(token.lower() in haystack.lower() for token in tokens):
            continue
        candidates[entity_id] = {
            "id": entity_id,
            "name": name,
            "aliases": "",
            "definition": definition,
        }
    if primary_id not in candidates:
        candidates[primary_id] = {
            "id": primary_id,
            "name": _candidate_entity_name(source, config),
            "aliases": "待确认",
            "definition": _first_sentence(
                str(source.get("description") or "")
                or documents.get("business_semantics_md", "")
                or f"由场景 {source['scene_id']} 提取的候选业务实体"
            ),
        }
    ordered = [candidates.pop(primary_id)]
    ordered.extend(candidates[key] for key in sorted(candidates))
    return ordered


def _entity_rule_id(text: str) -> str | None:
    lowered = str(text).lower()
    for entity_id, _, tokens, _ in _ENTITY_RULES:
        if any(token.lower() in lowered for token in tokens):
            return entity_id
    return None


def _entity_rule_name(entity_id: str) -> str:
    for candidate_id, name, _, _ in _ENTITY_RULES:
        if candidate_id == entity_id:
            return name
    return entity_id


def _metric_entity_id(
    metric: dict[str, str], entities: list[dict[str, str]], fallback: str
) -> str:
    text = f"{metric.get('key', '')} {metric.get('name', '')} {metric.get('field', '')}"
    detected = _entity_rule_id(text)
    available = {entity["id"] for entity in entities}
    if detected in available:
        return detected
    return fallback


def _metric_id(entity_id: str, key: str) -> str:
    normalized = _stable_id(key)
    return (
        normalized
        if normalized.startswith(f"{entity_id}_")
        else _stable_id(f"{entity_id}_{normalized}")
    )


def _entity_relation_candidates(
    entities_by_scene: dict[str, set[str]]
) -> list[list[str]]:
    """Suggest co-occurring entity relations for human review."""
    seen: set[str] = set()
    rows: list[list[str]] = []
    for scene_id, entity_ids in sorted(entities_by_scene.items()):
        ordered = sorted(entity_ids)
        for index, source in enumerate(ordered):
            for target in ordered[index + 1 :]:
                relation_id = _stable_id(f"{source}_{target}_relation")
                if relation_id in seen:
                    continue
                seen.add(relation_id)
                rows.append(
                    [
                        relation_id,
                        source,
                        "关联",
                        target,
                        "待确认",
                        (
                            f"场景 {scene_id} 的语义文档中共现，"
                            "业务关联口径需结合实际场景判断。"
                        ),
                    ]
                )
    return rows


def _normalize_source(source: Any) -> dict[str, Any]:
    snapshot = source.get("snapshot") if isinstance(source, dict) else source
    semantic_md = source.get("semantic_md") if isinstance(source, dict) else None
    projection = getattr(snapshot, "routing_projection", {}) or {}
    runtime = getattr(snapshot, "runtime_config", {}) or {}
    documents = runtime.get("documents", {}) if isinstance(runtime, dict) else {}
    if (
        not isinstance(semantic_md, str)
        or not semantic_md.strip()
    ) and isinstance(documents, dict):
        semantic_md = documents.get("semantic_md")
    source_hashes = getattr(snapshot, "source_hashes", None)
    scene_id = getattr(snapshot, "scene_id", "")
    if not scene_id and isinstance(source, dict):
        scene_id = source.get("scene_id", "")
    return {
        "snapshot": snapshot,
        "scene_id": str(scene_id or ""),
        "snapshot_id": str(getattr(snapshot, "snapshot_id", "") or ""),
        "revision_id": str(getattr(snapshot, "revision_id", "") or ""),
        "semantic_md_hash": str(getattr(source_hashes, "semantic_hash", "") or ""),
        "name": str(projection.get("name") or getattr(snapshot, "scene_id", "") or ""),
        "description": str(projection.get("description") or ""),
        "semantic_md": semantic_md,
        "documents": documents if isinstance(documents, dict) else {},
    }


def _parse_semantic(markdown: str) -> ParsedSemanticDocument | None:
    if not markdown.strip():
        return None
    try:
        return SemanticMarkdownParser().parse(markdown)
    except Exception:
        return None


def _source_documents(
    source: dict[str, Any], parsed: ParsedSemanticDocument | None
) -> dict[str, str]:
    if parsed is not None:
        return split_scene_documents(parsed.body_markdown)
    documents = (
        source.get("documents") if isinstance(source.get("documents"), dict) else {}
    )
    return {
        "data_dictionary_md": str(documents.get("data_dictionary_md") or ""),
        "business_semantics_md": str(documents.get("business_semantics_md") or ""),
    }


def _candidate_entity_id(source: dict[str, Any], config: SemanticConfig | None) -> str:
    scene_id = str((config.scene_id if config else source.get("scene_id")) or "")
    cleaned = re.sub(
        r"(_?(analysis|report|scene|query|management|detail|summary))+$",
        "",
        scene_id,
    )
    return _stable_id(cleaned or scene_id or "business_entity")


def _candidate_entity_name(
    source: dict[str, Any], config: SemanticConfig | None
) -> str:
    name = str(
        (config.name if config else source.get("name")) or source.get("scene_id") or ""
    )
    name = re.sub(r"(分析|报表|管理|查询|场景|统计|明细)$", "", name).strip()
    return name or str(source.get("name") or source.get("scene_id"))


def _metric_candidates(
    config: SemanticConfig | None,
    dictionary_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    if config is not None:
        for metric in config.metrics:
            key = metric.key
            candidates.append(
                {
                    "key": key,
                    "semantic_key": key,
                    "name": metric.name or key,
                    "unit": metric.unit or "",
                    "formula": "",
                }
            )
    if candidates:
        return candidates

    for row in dictionary_rows:
        field = row.get("field", "")
        name = row.get("name") or row.get("description") or field
        haystack = f"{field} {name} {row.get('type', '')}".lower()
        if not any(hint.lower() in haystack for hint in _METRIC_HINTS):
            continue
        candidates.append(
            {
                "key": _stable_id(field or name),
                "semantic_key": "",
                "name": name,
                "unit": _infer_unit(name),
                "formula": "",
            }
        )
    return candidates


def scene_semantic_keys_from_source(source: Any) -> set[str]:
    """Return semantic binding keys derived from Scene Markdown."""
    normalized = _normalize_source(source)
    parsed = _parse_semantic(normalized.get("semantic_md") or "")
    config = parsed.config if parsed else None
    documents = _source_documents(normalized, parsed)
    field_rows = _dictionary_rows(documents.get("data_dictionary_md", ""))
    scene_entities = _entity_candidates(normalized, config, documents, field_rows)
    primary_entity_id = scene_entities[0]["id"] if scene_entities else "business_entity"
    binding_entity_id = _binding_primary_entity_id(
        scene_entities, field_rows, primary_entity_id
    )
    binding_entity_ids = _binding_entity_ids(scene_entities, binding_entity_id)
    keys: set[str] = set()
    if config is not None:
        keys.update(str(item.key) for item in config.dimensions if item.key)
        keys.update(str(item.key) for item in config.metrics if item.key)
    amount_fields = False
    date_fields = False
    for row in field_rows:
        field = row.get("field") or row.get("name") or ""
        if not field:
            continue
        field_key = _stable_id(field)
        keys.add(field_key)
        amount_fields = amount_fields or _is_amount_like_field(row)
        date_fields = date_fields or _is_date_like_field(row)
        for entity_id in binding_entity_ids:
            for suffix in _field_binding_suffixes(entity_id, field_key, row):
                keys.add(f"{entity_id}.{suffix}")
    metric_candidates = _metric_candidates(config, field_rows)
    for metric in metric_candidates:
        entity_id = metric.get("entity_id") or _metric_entity_id(
            metric, scene_entities, primary_entity_id
        )
        metric_id = _metric_id(entity_id, metric["key"])
        keys.add(metric.get("semantic_key") or metric_id)
        keys.add(metric_id)
        keys.add(metric["key"])
        for alias_entity_id in binding_entity_ids:
            for suffix in _field_binding_suffixes(
                alias_entity_id,
                _stable_id(metric["key"]),
                metric,
            ):
                keys.add(f"{alias_entity_id}.{suffix}")
    for entity_id in binding_entity_ids:
        keys.add(f"{entity_id}.count")
        if amount_fields:
            keys.add(f"{entity_id}.amount_missing_count")
            keys.add(f"{entity_id}.missing_amount_count")
        if date_fields:
            keys.add(f"{entity_id}.date_missing_count")
            keys.add(f"{entity_id}.missing_date_count")
        if _has_contract_amount(field_rows) and _has_paid_amount(field_rows):
            keys.add(f"{entity_id}.contract_paid_ratio")
            keys.add(f"{entity_id}.contract_unpaid_balance")
            keys.add(f"{entity_id}.paid_ratio")
            keys.add(f"{entity_id}.unpaid_balance")
    return {key for key in keys if key}


def _binding_primary_entity_id(
    entities: list[dict[str, str]], field_rows: list[dict[str, str]], fallback: str
) -> str:
    available = {entity["id"] for entity in entities}
    scores = {entity_id: 0 for entity_id in available}
    for row in field_rows:
        text = " ".join(
            str(row.get(key) or "") for key in ("field", "name", "description")
        ).lower()
        for entity_id, _, tokens, _ in _ENTITY_RULES:
            if entity_id not in scores:
                continue
            if any(token.lower() in text for token in tokens):
                scores[entity_id] += 1
    known_ids = {entity_id for entity_id, *_ in _ENTITY_RULES}
    ranked = sorted(
        (
            (score, entity_id)
            for entity_id, score in scores.items()
            if entity_id in known_ids
        ),
        key=lambda item: (-item[0], item[1]),
    )
    if ranked and ranked[0][0] > 0:
        return ranked[0][1]
    return (
        fallback
        if fallback in available
        else (entities[0]["id"] if entities else fallback)
    )


def _binding_entity_ids(
    entities: list[dict[str, str]], primary_entity_id: str
) -> list[str]:
    known_ids = {entity_id for entity_id, *_ in _ENTITY_RULES}
    ordered = [primary_entity_id]
    ordered.extend(
        sorted(
            entity["id"]
            for entity in entities
            if entity["id"] in known_ids and entity["id"] != primary_entity_id
        )
    )
    return list(dict.fromkeys(item for item in ordered if item))


def _field_binding_suffixes(
    entity_id: str, field_key: str, row: dict[str, str]
) -> set[str]:
    suffixes = {field_key}
    for prefix, *_ in _ENTITY_RULES:
        marker = f"{prefix}_"
        if field_key.startswith(marker):
            suffixes.add(field_key[len(marker) :])
    marker = f"{entity_id}_"
    if field_key.startswith(marker):
        suffixes.add(field_key[len(marker) :])
    if _is_amount_like_field(row):
        for suffix in list(suffixes):
            if suffix and not suffix.endswith("_amount"):
                suffixes.add(f"{suffix}_amount")
    return {suffix for suffix in suffixes if suffix}


def _is_amount_like_field(row: dict[str, str]) -> bool:
    text = " ".join(
        str(row.get(key) or "") for key in ("field", "name", "description")
    ).lower()
    return any(
        token in text
        for token in (
            "金额",
            "预算",
            "成本",
            "费用",
            "收入",
            "回款",
            "付款",
            "amount",
            "budget",
            "cost",
            "fee",
            "revenue",
            "paid",
            "payment",
            "price",
        )
    )


def _is_date_like_field(row: dict[str, str]) -> bool:
    text = " ".join(
        str(row.get(key) or "") for key in ("field", "name", "type", "description")
    ).lower()
    return any(token in text for token in ("日期", "时间", "date", "time"))


def _has_contract_amount(field_rows: list[dict[str, str]]) -> bool:
    return any(
        "contract" in _stable_id(row.get("field") or row.get("name") or "")
        and _is_amount_like_field(row)
        for row in field_rows
    )


def _has_paid_amount(field_rows: list[dict[str, str]]) -> bool:
    return any(
        any(
            token in _stable_id(row.get("field") or row.get("name") or "")
            for token in ("paid", "payment")
        )
        and _is_amount_like_field(row)
        for row in field_rows
    )


def _dictionary_rows(markdown: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    lines = [line.strip() for line in markdown.replace("\r\n", "\n").split("\n")]
    index = 0
    while index + 1 < len(lines):
        if not _is_table_row(lines[index]) or not _is_table_divider(lines[index + 1]):
            index += 1
            continue
        headers = [_normalize_header(cell) for cell in _split_row(lines[index])]
        row_index = index + 2
        while row_index < len(lines) and _is_table_row(lines[row_index]):
            cells = _split_row(lines[row_index])
            if len(cells) == len(headers):
                mapped = dict(zip(headers, cells))
                rows.append(
                    {
                        "field": _first(
                            mapped,
                            "field",
                            "字段",
                            "字段名",
                            "目标字段",
                            "物理字段",
                            "英文字段",
                            "英文名",
                            "列名",
                            "column",
                            "columnname",
                            "targetcolumn",
                            "name",
                        ),
                        "name": _first(
                            mapped,
                            "name",
                            "名称",
                            "中文名",
                            "中文字段",
                            "中文名称",
                            "业务名称",
                            "含义",
                            "业务含义",
                            "描述",
                            "说明",
                        ),
                        "type": _first(mapped, "type", "类型", "数据类型", "datatype"),
                        "description": _first(
                            mapped,
                            "description",
                            "描述",
                            "说明",
                            "含义",
                            "业务含义",
                            "含义与来源口径",
                            "来源口径",
                        ),
                    }
                )
            row_index += 1
        index = row_index
    return [row for row in rows if row.get("field") or row.get("name")]


def _cross_scene_candidates(
    fields_by_scene: dict[str, list[dict[str, str]]]
) -> list[list[str]]:
    by_key: dict[str, list[tuple[str, str]]] = {}
    for scene_id, rows in fields_by_scene.items():
        for row in rows:
            field = row.get("field") or row.get("name")
            normalized = _stable_id(field)
            if len(normalized) < 2 or not any(
                hint in field.lower() for hint in _KEY_HINTS
            ):
                continue
            by_key.setdefault(normalized, []).append((scene_id, field))
    candidates: list[list[str]] = []
    for key, occurrences in sorted(by_key.items()):
        scene_ids = sorted({scene_id for scene_id, _ in occurrences})
        if len(scene_ids) < 2:
            continue
        left, right = scene_ids[:2]
        candidates.append(
            [
                _stable_id(f"{left}_{right}_{key}"),
                left,
                right,
                "待确认",
                key,
                "未明确",
                "同名或相似键跨场景共现，业务含义需结合实际口径判断",
            ]
        )
    return candidates


def _stable_id(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]+", "_", str(value).strip())
    text = re.sub(r"_+", "_", text).strip("_").lower()
    if not text:
        text = "item"
    if len(text) <= 96:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{text[:87]}_{digest}"


def _unique_id(candidate: str, seen: set[str], salt: str) -> str:
    value = candidate
    if value not in seen:
        seen.add(value)
        return value
    value = _stable_id(f"{candidate}_{salt}")
    counter = 2
    while value in seen:
        value = _stable_id(f"{candidate}_{salt}_{counter}")
        counter += 1
    seen.add(value)
    return value


def _infer_unit(name: str) -> str:
    text = name.lower()
    if any(token in text for token in ("率", "比例", "rate", "ratio", "%")):
        return "%"
    if any(
        token in text
        for token in (
            "金额",
            "成本",
            "费用",
            "收入",
            "预算",
            "amount",
            "cost",
            "revenue",
            "price",
            "budget",
            "fee",
        )
    ):
        return "元"
    return ""


def _first_sentence(value: str, max_len: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value).strip())
    text = re.split(r"[。.!！?？\n]", text)[0].strip() or text
    return text[:max_len]


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_escape_cell(value) for value in row) + " |")
    return "\n".join(lines)


def _escape_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _is_table_row(line: str) -> bool:
    return line.startswith("|") and line.endswith("|")


def _is_table_divider(line: str) -> bool:
    if not _is_table_row(line):
        return False
    cells = _split_row(line)
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells
    )


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _normalize_header(value: str) -> str:
    return re.sub(r"[\s_（）()：:]", "", value).lower()


def _first(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(_normalize_header(key), "").strip()
        if value:
            return _clean_cell(value)
    return ""


def _clean_cell(value: str) -> str:
    match = re.search(r"`([^`]+)`", value)
    if match:
        value = match.group(1)
    return value.replace("`", "").strip()


__all__ = [
    "generate_ontology_markdown_from_scene_sources",
    "scene_semantic_keys_from_source",
]
