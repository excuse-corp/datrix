"""Build a deterministic business-caliber explanation for executed SQL."""

from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp

from ..query.capabilities import build_query_capabilities, schema_config
from ..schemas.snapshot import Snapshot

_FIELD_WORDS = {
    "amount": "金额",
    "category": "类别",
    "city": "城市",
    "code": "编号",
    "contract": "合同",
    "count": "数量",
    "customer": "客户",
    "date": "日期",
    "day": "日期",
    "department": "部门",
    "dept": "部门",
    "employee": "员工",
    "fee": "费用",
    "id": "",
    "month": "月份",
    "name": "名称",
    "no": "编号",
    "number": "数量",
    "price": "价格",
    "project": "项目",
    "record": "记录",
    "records": "记录",
    "region": "地区",
    "revenue": "收入",
    "row": "记录",
    "rows": "记录",
    "sales": "销售额",
    "sign": "签署",
    "signed": "签署",
    "status": "状态",
    "supplier": "供应商",
    "time": "时间",
    "total": "总计",
    "type": "类型",
    "user": "用户",
    "vendor": "供应商",
    "year": "年份",
}
_SPECIAL_LABELS = {
    "count_rows": "记录数",
    "row_count": "记录数",
    "record_count": "记录数",
    "department_id": "所属部门",
    "dept_id": "所属部门",
    "customer_id": "客户",
    "employee_id": "员工",
    "project_id": "项目",
    "supplier_id": "供应商",
}
_BUSINESS_RULE_HEADINGS = (
    "口径",
    "粒度",
    "边界",
    "范围",
    "规则",
    "语义",
    "定义",
    "业务",
)
_BUSINESS_RULE_HINTS = (
    "口径",
    "一行",
    "空值",
    "为空",
    "默认",
    "边界",
    "不推断",
    "去重",
    "含税",
    "不含税",
    "row",
    "null",
    "default",
    "boundary",
    "scope",
    "tax",
    "exclude",
    "distinct",
)


def build_sql_explanation(sql: str, snapshot: Snapshot) -> dict[str, Any]:
    """Return a safe, deterministic explanation of one executed SELECT.

    The explanation is derived from the exact SQL handed to the executor and
    the immutable Scene Snapshot used for that query.  It never recalculates
    result values and it does not ask another model to interpret the query.
    """

    source = str(snapshot.runtime_config.get("view") or "").strip() or None
    source_label = _source_label(snapshot)
    rules = _business_rules(snapshot)
    fallback = _fallback_explanation(source, source_label, rules)
    try:
        dialect = _dialect(snapshot)
        statement = sqlglot.parse_one(sql, read=dialect)
        select = statement.find(exp.Select)
        if select is None:
            return fallback
    except Exception:
        return fallback

    capabilities = build_query_capabilities(snapshot)
    definitions: dict[str, dict[str, Any]] = {}
    for item in [*capabilities.metrics, *capabilities.dimensions]:
        if not isinstance(item, dict):
            continue
        for key in (item.get("key"), item.get("field")):
            if key:
                definitions[str(key).lower()] = item
    column_labels = _column_labels(snapshot, definitions)
    metrics: list[dict[str, Any]] = []
    for expression in select.expressions:
        alias = str(expression.alias_or_name or expression.sql(dialect=dialect)).strip()
        target = expression.this if isinstance(expression, exp.Alias) else expression
        aggregate = _first_aggregate(target)
        if aggregate is not None:
            field = _first_column_name(aggregate)
            definition = definitions.get(alias.lower()) or definitions.get(
                (field or "").lower(), {}
            )
            aggregation = _aggregation_name(aggregate)
            label = _friendly_label(
                str(definition.get("name") or "").strip() or alias,
                column_labels,
            )
            field_label = _friendly_label(field or "", column_labels)
            null_handling = _null_handling(target)
            metrics.append(
                {
                    "key": alias,
                    "label": label,
                    "unit": definition.get("unit"),
                    "definition": _metric_definition(
                        label,
                        field_label,
                        aggregation,
                        definition,
                        null_handling,
                        definition.get("unit"),
                    ),
                }
            )
            continue

    grouped_dimensions: list[str] = []
    group = select.args.get("group")
    if group is not None:
        for item in group.expressions:
            field = _first_column_name(item)
            grouped_dimensions.append(_friendly_label(field or "", column_labels))

    predicate_nodes = _predicate_nodes(select.args.get("where"))
    time_field = str(capabilities.time.get("field") or "").strip()
    time_nodes = [
        node
        for node in predicate_nodes
        if time_field and _contains_column(node, time_field)
    ]
    other_nodes = [node for node in predicate_nodes if node not in time_nodes]
    time_scope = [
        _describe_condition(node, column_labels, is_time=True) for node in time_nodes
    ]
    predicates = [_describe_condition(node, column_labels) for node in other_nodes]
    order = select.args.get("order")
    order_by = [
        str(item.sql(dialect=dialect)).strip()
        for item in (order.expressions if order else [])
    ]
    limit = _limit_value(select.args.get("limit"))
    distinct = bool(select.args.get("distinct"))
    has_aggregate = bool(metrics)
    if grouped_dimensions:
        grain = grouped_dimensions
        grain_description = (
            f"每一行代表一个{'、'.join(grouped_dimensions)}的统计结果"
        )
    elif has_aggregate:
        grain = ["汇总结果"]
        grain_description = "所有符合条件的数据合在一起统计，结果通常只有一行"
    else:
        grain = ["明细记录"]
        grain_description = "每一行代表一条符合条件的记录"
    summary = _summary(
        source_label=source_label,
        grain_description=grain_description,
        metrics=metrics,
        predicates=predicates,
        time_scope=time_scope,
        rules=rules,
        distinct=distinct,
        limit=limit,
    )
    return {
        "summary": summary,
        "source": source,
        "source_label": source_label,
        "grain": grain,
        "grain_description": grain_description,
        "metrics": metrics,
        "filters": predicates,
        "time_scope": time_scope,
        "order_by": order_by,
        "limit": limit,
        "distinct": distinct,
        "business_rules": rules,
    }


def _dialect(snapshot: Snapshot) -> str | None:
    schema = schema_config(snapshot)
    dialect = str(schema.get("dialect") or "").strip().lower()
    return {
        "mssql": "tsql",
        "postgresql": "postgres",
        "sqlserver": "tsql",
    }.get(dialect, dialect or None)


def _first_aggregate(expression: exp.Expression) -> exp.AggFunc | None:
    if isinstance(expression, exp.AggFunc):
        return expression
    return next(expression.find_all(exp.AggFunc), None)


def _first_column_name(expression: exp.Expression) -> str | None:
    column = next(expression.find_all(exp.Column), None)
    return column.name if column is not None else None


def _aggregation_name(aggregate: exp.AggFunc) -> str:
    if isinstance(aggregate, exp.Count):
        target = aggregate.args.get("this")
        if isinstance(target, exp.Distinct):
            return "count_distinct"
        return "count"
    return str(aggregate.sql_name() or aggregate.key).lower()


def _metric_definition(
    label: str,
    field_label: str,
    aggregation: str,
    definition: dict[str, Any],
    null_handling: str | None,
    unit: Any,
) -> str:
    explicit = str(
        definition.get("description") or definition.get("definition") or ""
    ).strip()
    if explicit:
        result = explicit.rstrip("。")
    elif aggregation == "sum":
        result = f"{label}：把符合条件记录中的{field_label or '相关数值'}加在一起计算"
    elif aggregation in {"avg", "mean"}:
        result = f"{label}：计算符合条件记录中{field_label or '相关数值'}的平均值"
    elif aggregation in {"min", "max"}:
        extreme = "最低" if aggregation == "min" else "最高"
        result = f"{label}：取符合条件记录中{field_label or '相关数值'}的{extreme}值"
    elif aggregation == "count_distinct":
        result = f"{label}：统计符合条件记录中不同{field_label or '内容'}的数量"
    elif aggregation == "count":
        if field_label:
            result = f"{label}：统计符合条件记录中填写了{field_label}的记录数"
        else:
            result = f"{label}：统计符合条件的记录数"
    else:
        result = f"{label}：根据符合条件记录中的{field_label or '相关数值'}计算"
    details = [value for value in (null_handling, _unit_text(unit)) if value]
    if details:
        result += f"（{'；'.join(details)}）"
    return result + "。"


def _unit_text(unit: Any) -> str | None:
    normalized = str(unit or "").strip()
    return f"单位为 {normalized}" if normalized else None


def _null_handling(expression: exp.Expression) -> str | None:
    if any(isinstance(node, exp.Coalesce) for node in expression.walk()):
        return "没有可用数值时按 0 处理"
    return None


def _predicate_nodes(where: exp.Expression | None) -> list[exp.Expression]:
    if where is None:
        return []
    return _split_and(where.this)


def _split_and(expression: exp.Expression) -> list[exp.Expression]:
    if isinstance(expression, exp.And):
        return [*_split_and(expression.this), *_split_and(expression.expression)]
    return [expression]


def _contains_column(expression: exp.Expression, name: str) -> bool:
    normalized = name.lower()
    return any(
        column.name.lower() == normalized for column in expression.find_all(exp.Column)
    )


def _describe_condition(
    expression: exp.Expression,
    column_labels: dict[str, str],
    *,
    is_time: bool = False,
) -> str:
    if isinstance(expression, exp.Or):
        left = _describe_condition(expression.this, column_labels, is_time=is_time)
        right = _describe_condition(
            expression.expression, column_labels, is_time=is_time
        )
        return f"满足以下任一条件：{left}，或{right}"

    column = next(expression.find_all(exp.Column), None)
    label = _friendly_label(column.name, column_labels) if column else "相关内容"
    value = _condition_value(expression, is_time=is_time)
    if isinstance(expression, exp.EQ):
        return f"{label}为“{value}”" if value is not None else f"符合{label}相关条件"
    if isinstance(expression, exp.NEQ):
        return f"{label}不为“{value}”" if value is not None else f"排除{label}相关条件"
    if isinstance(expression, exp.GT):
        if is_time:
            return (
                f"{label}晚于{value}"
                if value is not None
                else f"{label}符合较晚范围"
            )
        return f"{label}大于{value}" if value is not None else f"{label}符合较高范围"
    if isinstance(expression, exp.GTE):
        if is_time:
            return f"从{value}开始" if value is not None else "从较早时间开始"
        return f"{label}至少是{value}" if value is not None else f"{label}符合较高范围"
    if isinstance(expression, exp.LT):
        if is_time:
            return f"早于{value}" if value is not None else f"{label}符合较早范围"
        return f"{label}小于{value}" if value is not None else f"{label}符合较低范围"
    if isinstance(expression, exp.LTE):
        if is_time:
            return f"截至{value}" if value is not None else "截至较晚时间"
        return f"{label}不超过{value}" if value is not None else f"{label}符合较低范围"
    if isinstance(expression, exp.In):
        values = [_plain_value(item) for item in expression.expressions]
        values = [item for item in values if item is not None]
        if values:
            return f"{label}是{'、'.join(f'“{item}”' for item in values)}之一"
    if isinstance(expression, exp.Between):
        low = _format_value(_plain_value(expression.args.get("low")), is_time=is_time)
        high = _format_value(
            _plain_value(expression.args.get("high")), is_time=is_time
        )
        if low is not None and high is not None:
            return f"{label}在{low}到{high}之间"
    if isinstance(expression, (exp.Like, exp.ILike)) and value is not None:
        pattern = str(value).strip("%")
        if pattern:
            return f"{label}包含“{pattern}”"
    if isinstance(expression, exp.Is) and isinstance(expression.expression, exp.Null):
        return f"{label}尚未填写"
    if isinstance(expression, exp.Not) and isinstance(expression.this, exp.Is):
        inner = expression.this
        if isinstance(inner.expression, exp.Null):
            return f"{label}已填写"
    return f"按{label}相关条件筛选"


def _condition_value(
    expression: exp.Expression, *, is_time: bool = False
) -> str | None:
    value_node = expression.args.get("expression")
    return _format_value(_plain_value(value_node), is_time=is_time)


def _plain_value(expression: exp.Expression | None) -> str | None:
    if expression is None:
        return None
    if isinstance(expression, exp.Literal):
        return str(expression.this)
    literal = next(expression.find_all(exp.Literal), None)
    if literal is not None:
        return str(literal.this)
    return None


def _format_value(value: str | None, *, is_time: bool = False) -> str | None:
    if value is None:
        return None
    if is_time:
        match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
        if match:
            year, month, day = match.groups()
            return f"{year}年{int(month)}月{int(day)}日"
    return value


def _limit_value(limit: exp.Expression | None) -> int | None:
    if limit is None:
        return None
    expression = limit.args.get("expression")
    if isinstance(expression, exp.Literal) and not expression.is_string:
        try:
            return int(expression.this)
        except (TypeError, ValueError):
            return None
    return None


def _source_label(snapshot: Snapshot) -> str:
    projection = snapshot.routing_projection
    if isinstance(projection, dict):
        for key in ("name", "scene_name", "display_name"):
            value = str(projection.get(key) or "").strip()
            if value:
                return value
    return "当前业务数据"


def _column_labels(
    snapshot: Snapshot, definitions: dict[str, dict[str, Any]]
) -> dict[str, str]:
    labels = {
        key: str(item.get("name") or "").strip()
        for key, item in definitions.items()
        if item.get("name")
    }
    for item in schema_config(snapshot).get("columns", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        comment = str(item.get("comment") or item.get("label") or "").strip()
        if name and comment:
            labels[name.lower()] = comment
    labels.update(_dictionary_labels(snapshot))
    return labels


def _dictionary_labels(snapshot: Snapshot) -> dict[str, str]:
    documents = snapshot.runtime_config.get("documents", {})
    dictionary = (
        str(documents.get("data_dictionary_md") or "")
        if isinstance(documents, dict)
        else ""
    )
    labels: dict[str, str] = {}
    for line in dictionary.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        for index, cell in enumerate(cells):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", cell):
                continue
            candidates = [
                value
                for value in cells[:index] + cells[index + 1 :]
                if value and not re.fullmatch(r"[A-Za-z0-9_()., ]+", value)
            ]
            if candidates:
                labels[cell.lower()] = candidates[-1]
    return labels


def _friendly_label(value: str, labels: dict[str, str]) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    special = _SPECIAL_LABELS.get(normalized.lower())
    if special:
        return special
    label = labels.get(normalized.lower())
    if label:
        return label
    if re.search(r"[\u3400-\u9fff]", normalized):
        return normalized
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", normalized.lower()) if part]
    translated = [_FIELD_WORDS[part] for part in parts if part in _FIELD_WORDS]
    if translated and all(part in _FIELD_WORDS for part in parts):
        return "".join(translated) or "相关信息"
    return "相关信息"


def _business_rules(snapshot: Snapshot) -> list[str]:
    documents = snapshot.runtime_config.get("documents", {})
    if not isinstance(documents, dict):
        return []
    semantics = str(documents.get("business_semantics_md") or "").strip()
    if not semantics:
        return []

    rules: list[str] = []
    active = False
    for raw_line in semantics.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            heading = line.lstrip("#").strip().lower()
            active = any(keyword in heading for keyword in _BUSINESS_RULE_HEADINGS)
            continue
        if line.startswith("|") or set(line) <= {"|", "-", ":", " "}:
            continue
        normalized = line.lstrip("-* ").strip()
        if active or any(hint in normalized for hint in _BUSINESS_RULE_HINTS):
            if normalized and normalized not in rules:
                rules.append(normalized[:240])
        if len(rules) >= 8:
            break
    return rules


def _summary(
    *,
    source_label: str,
    grain_description: str,
    metrics: list[dict[str, Any]],
    predicates: list[str],
    time_scope: list[str],
    rules: list[str],
    distinct: bool,
    limit: int | None,
) -> str:
    parts: list[str] = []
    if source_label:
        parts.append(f"数据来自{source_label}")
    parts.append(grain_description)
    if metrics:
        metric_text = "；".join(
            str(item.get("definition") or item.get("key")) for item in metrics
        )
        parts.append(f"数字的计算方式是：{metric_text.rstrip('。')}")
    else:
        parts.append("本次没有对数值做合计或平均处理，结果直接展示符合条件的记录")
    if predicates:
        parts.append(f"只统计：{'；'.join(predicates)}")
    if time_scope:
        parts.append(f"时间范围是：{'；'.join(time_scope)}")
    if distinct:
        parts.append("相同内容只计算一次")
    if limit is not None:
        parts.append(f"最多展示 {limit} 条记录")
    if rules:
        parts.append(f"需要注意：{'；'.join(rule.rstrip('。') for rule in rules)}")
    return (
        "；".join(parts).rstrip("。") + "。"
        if parts
        else "未能从 SQL 中提取可解释的数据口径。"
    )


def _fallback_explanation(
    source: str | None, source_label: str, rules: list[str]
) -> dict[str, Any]:
    summary = (
        f"数据来自{source_label}；暂时无法判断每一条结果代表什么；"
        "暂时无法识别数字的计算方式。"
    )
    if rules:
        summary += f"需要注意：{'；'.join(rules)}。"
    return {
        "summary": summary,
        "source": source,
        "source_label": source_label,
        "grain": [],
        "grain_description": "暂时无法判断每一条结果代表什么",
        "metrics": [],
        "filters": [],
        "time_scope": [],
        "order_by": [],
        "limit": None,
        "distinct": False,
        "business_rules": rules,
    }


__all__ = ["build_sql_explanation"]
