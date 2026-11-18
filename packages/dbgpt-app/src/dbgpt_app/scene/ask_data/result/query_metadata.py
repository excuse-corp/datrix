"""Structured lineage and caliber extracted from the SQL that actually ran."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import sqlglot
from sqlglot import exp

from ..query.capabilities import build_query_capabilities, schema_config
from ..query.query_spec import FieldLineage, TimeRange, build_field_ref
from ..schemas.snapshot import Snapshot


def sql_field_lineage(
    sql: str,
    snapshot: Snapshot,
    *,
    result_columns: list[dict[str, Any]] | None = None,
) -> list[FieldLineage]:
    """Parse physical field usage from validated SQL without guessing aliases."""
    try:
        statement = sqlglot.parse_one(sql, read=_dialect(snapshot))
    except Exception:
        return []
    if not isinstance(statement, (exp.Select, exp.With)):
        return []
    selects = list(statement.find_all(exp.Select))
    if not selects:
        return []
    root_select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)

    capabilities = build_query_capabilities(snapshot)
    known_fields = _known_fields(snapshot, capabilities)
    definitions_by_field: dict[str, list[dict[str, Any]]] = {}
    for item in [*capabilities.dimensions, *capabilities.metrics]:
        field_name = str(item.get("field") or "").strip()
        if field_name:
            definitions_by_field.setdefault(field_name.lower(), []).append(item)
    result_columns = result_columns or []
    semantic_by_field: dict[str, str] = {}
    for field_name, definitions in definitions_by_field.items():
        semantic_keys = {
            str(item.get("semantic_key") or item.get("key") or "").strip()
            for item in definitions
        }
        semantic_keys.discard("")
        if len(semantic_keys) == 1:
            semantic_by_field[field_name] = next(iter(semantic_keys))
    for column in result_columns:
        field_name = str(column.get("source_field") or "").strip()
        semantic_key = str(column.get("semantic_key") or "").strip()
        if field_name and semantic_key:
            semantic_by_field[field_name.lower()] = semantic_key

    cte_names = _cte_names(statement)
    merged: dict[str, dict[str, Any]] = {}

    def add_column(
        column: exp.Column,
        role: str,
        result_key: str | None = None,
        aggregation: str | None = None,
        table_aliases: set[str] | None = None,
        direct_base_table_count: int = 0,
    ) -> None:
        field_name = str(column.name or "").strip()
        if not field_name or field_name == "*":
            return
        qualifier = str(column.table or "").strip().lower()
        qualified_to_bound_source = not qualifier or (
            qualifier not in cte_names and qualifier in (table_aliases or set())
        )
        if qualifier and qualifier not in (table_aliases or set()):
            qualified_to_bound_source = qualifier in cte_names
        if not qualifier:
            qualified_to_bound_source = direct_base_table_count == 1 or bool(cte_names)
        resolved = (
            qualified_to_bound_source and field_name.lower() in known_fields
        )
        key = field_name.lower()
        item = merged.setdefault(
            key,
            {
                "field_ref": (
                    build_field_ref(snapshot.scene_id, snapshot.snapshot_id, field_name)
                    if resolved
                    else None
                ),
                "source_field": field_name,
                "roles": [],
                "result_keys": [],
                "semantic_key": semantic_by_field.get(key),
                "aggregation": aggregation,
                "resolved": resolved,
            },
        )
        item["resolved"] = bool(item["resolved"] and resolved)
        if not item["resolved"]:
            item["field_ref"] = None
        if role not in item["roles"]:
            item["roles"].append(role)
        if result_key and result_key not in item["result_keys"]:
            item["result_keys"].append(result_key)
        if aggregation and not item.get("aggregation"):
            item["aggregation"] = aggregation

    for select in selects:
        include_result_keys = select is root_select
        table_aliases, direct_base_table_count = _select_scope(
            select, snapshot, cte_names
        )
        for select_expression in select.expressions:
            target = select_expression.this if isinstance(select_expression, exp.Alias) else select_expression
            result_key = (
                str(select_expression.alias_or_name or "").strip() or None
                if include_result_keys
                else None
            )
            aggregate_by_column = {
                id(column): _aggregate_name(aggregate)
                for aggregate in target.find_all(exp.AggFunc)
                for column in aggregate.find_all(exp.Column)
            }
            for column in target.find_all(exp.Column):
                add_column(
                    column,
                    "aggregate" if id(column) in aggregate_by_column else "select",
                    result_key,
                    aggregation=aggregate_by_column.get(id(column)),
                    table_aliases=table_aliases,
                    direct_base_table_count=direct_base_table_count,
                )

        group = select.args.get("group")
        if group is not None:
            for expression in group.expressions:
                for column in expression.find_all(exp.Column):
                    add_column(
                        column,
                        "group_by",
                        table_aliases=table_aliases,
                        direct_base_table_count=direct_base_table_count,
                    )

        for clause, role in ((select.args.get("where"), "filter"), (select.args.get("having"), "having")):
            if clause is not None:
                for column in clause.find_all(exp.Column):
                    add_column(
                        column,
                        role,
                        table_aliases=table_aliases,
                        direct_base_table_count=direct_base_table_count,
                    )

        for join in select.args.get("joins") or []:
            on_clause = join.args.get("on")
            if on_clause is not None:
                for column in on_clause.find_all(exp.Column):
                    add_column(
                        column,
                        "join",
                        table_aliases=table_aliases,
                        direct_base_table_count=direct_base_table_count,
                    )
            for column in join.args.get("using") or []:
                if isinstance(column, exp.Column):
                    add_column(
                        column,
                        "join",
                        table_aliases=table_aliases,
                        direct_base_table_count=direct_base_table_count,
                    )

        order = select.args.get("order")
        if order is not None:
            for expression in order.expressions:
                for column in expression.find_all(exp.Column):
                    add_column(
                        column,
                        "order_by",
                        table_aliases=table_aliases,
                        direct_base_table_count=direct_base_table_count,
                    )

    time_field = str(capabilities.time.get("field") or "").strip().lower()
    if time_field:
        for key, item in merged.items():
            if key == time_field and "filter" in item["roles"]:
                item["roles"].append("time_filter")
    return [FieldLineage.model_validate(item) for item in merged.values()]


def sql_result_profile(
    sql: str, snapshot: Snapshot
) -> dict[str, Any]:
    """Return only SQL profile values that can be read exactly from its AST."""
    capabilities = build_query_capabilities(snapshot)
    time_config = capabilities.time
    dialect = _dialect(snapshot)
    try:
        statement = sqlglot.parse_one(sql, read=dialect)
        select = statement.find(exp.Select)
    except Exception:
        select = None
    grain: list[str] = []
    time_grain = None
    if select is not None:
        group = select.args.get("group")
        dimensions_by_field = {
            str(item.get("field") or "").lower(): str(
                item.get("semantic_key") or item.get("key") or ""
            )
            for item in capabilities.dimensions
            if item.get("field")
        }
        if group is not None:
            for expression in group.expressions:
                columns = list(expression.find_all(exp.Column))
                if len(columns) == 1 and isinstance(expression, exp.Column):
                    semantic_key = dimensions_by_field.get(
                        str(columns[0].name or "").lower()
                    )
                    if semantic_key and semantic_key not in grain:
                        grain.append(semantic_key)
                found_grain = _date_trunc_grain(expression, str(time_config.get("field") or ""))
                if found_grain:
                    time_grain = found_grain
    return {
        "grain": grain,
        "time_grain": time_grain,
        "time_range": _time_range_from_sql(select, time_config),
        "time_basis": str(time_config.get("name") or "").strip() or None,
    }


def _time_range_from_sql(
    select: exp.Select | None, time_config: dict[str, Any]
) -> TimeRange | None:
    if select is None:
        return None
    time_field = str(time_config.get("field") or "").strip().lower()
    if not time_field:
        return None
    where = select.args.get("where")
    if where is None:
        return None
    lower = upper = None
    conjuncts = _top_level_conjuncts(where.this)
    for node in conjuncts:
        if not isinstance(node, (exp.GTE, exp.GT, exp.LT, exp.LTE)):
            continue
        left, right = node.this, node.expression
        if isinstance(right, exp.Column) and not isinstance(left, exp.Column):
            left, right = right, left
            operator_reversed = True
        else:
            operator_reversed = False
        if not isinstance(left, exp.Column) or str(left.name or "").lower() != time_field:
            continue
        value = _literal_datetime(right)
        if value is None:
            continue
        operator = type(node)
        if operator is exp.GTE and not operator_reversed:
            lower = _merge_bound(lower, value, choose_max=True)
        elif operator is exp.GT and not operator_reversed:
            bound = value + timedelta(microseconds=1)
            lower = _merge_bound(lower, bound, choose_max=True)
        elif operator is exp.LT and not operator_reversed:
            upper = _merge_bound(upper, value, choose_max=False)
        elif operator is exp.LTE and not operator_reversed:
            bound = value + timedelta(microseconds=1)
            upper = _merge_bound(upper, bound, choose_max=False)
        elif operator is exp.LT and operator_reversed:
            bound = value + timedelta(microseconds=1)
            lower = _merge_bound(lower, bound, choose_max=True)
        elif operator is exp.LTE and operator_reversed:
            lower = _merge_bound(lower, value, choose_max=True)
        elif operator is exp.GT and operator_reversed:
            upper = _merge_bound(upper, value, choose_max=False)
        elif operator is exp.GTE and operator_reversed:
            bound = value + timedelta(microseconds=1)
            upper = _merge_bound(upper, bound, choose_max=False)
    if lower is None or upper is None:
        return None
    try:
        if lower >= upper:
            return None
        return TimeRange(
            start=lower,
            end_exclusive=upper,
            timezone=str(time_config.get("timezone") or "UTC"),
        )
    except Exception:
        return None


def _literal_datetime(expression: exp.Expression) -> datetime | None:
    if isinstance(expression, exp.Cast):
        expression = expression.this
    if not isinstance(expression, exp.Literal) or not expression.is_string:
        return None
    value = str(expression.this).strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed
    except ValueError:
        try:
            parsed_date = date.fromisoformat(value)
            return datetime.combine(parsed_date, time.min)
        except ValueError:
            return None


def _merge_bound(
    current: datetime | None, candidate: datetime, *, choose_max: bool
) -> datetime | None:
    if current is None:
        return candidate
    try:
        return max(current, candidate) if choose_max else min(current, candidate)
    except TypeError:
        return None


def _date_trunc_grain(expression: exp.Expression, time_field: str) -> str | None:
    if not time_field:
        return None
    columns = list(expression.find_all(exp.Column))
    if not any(str(column.name or "").lower() == time_field.lower() for column in columns):
        return None
    for node in expression.walk():
        function_name = str(getattr(node, "name", "") or node.sql_name()).lower()
        if function_name not in {"date_trunc", "datetrunc", "timestamp_trunc"}:
            continue
        for value in node.find_all(exp.Literal):
            grain = str(value.this or "").lower()
            if grain in {"year", "quarter", "month", "week", "day", "hour"}:
                return grain
    return None


def _aggregate_name(aggregate: exp.AggFunc) -> str:
    name = str(aggregate.sql_name() or "").lower()
    if name == "count":
        distinct = aggregate.args.get("distinct")
        return "count_distinct" if distinct else "count"
    return name or "aggregate"


def _known_fields(snapshot: Snapshot, capabilities: Any) -> set[str]:
    result = {
        str(item.get("name") or "").strip().lower()
        for item in schema_config(snapshot).get("columns", [])
        if isinstance(item, dict) and item.get("name")
    }
    result.update(
        str(item.get("field") or "").strip().lower()
        for item in [*capabilities.dimensions, *capabilities.metrics]
        if item.get("field")
    )
    if capabilities.time.get("field"):
        result.add(str(capabilities.time["field"]).strip().lower())
    return result


def _cte_names(statement: exp.Expression) -> set[str]:
    return {
        str(cte.alias or "").lower()
        for cte in statement.find_all(exp.CTE)
        if str(cte.alias or "").strip()
    }


def _select_scope(
    select: exp.Select, snapshot: Snapshot, cte_names: set[str]
) -> tuple[set[str], int]:
    view_name = str(snapshot.runtime_config.get("view") or "").lower()
    view_leaf = view_name.rsplit(".", 1)[-1]
    aliases: set[str] = set()
    base_table_count = 0
    direct_tables: list[exp.Table] = []
    from_clause = select.args.get("from_")
    if from_clause is not None and isinstance(from_clause.this, exp.Table):
        direct_tables.append(from_clause.this)
    for join in select.args.get("joins") or []:
        if isinstance(join.this, exp.Table):
            direct_tables.append(join.this)
    for table in direct_tables:
        name = str(table.name or "").lower()
        if name in cte_names:
            continue
        actual = ".".join(
            part for part in (str(table.catalog or ""), str(table.db or ""), name) if part
        ).lower()
        if actual == view_name or name == view_leaf:
            base_table_count += 1
            aliases.add(name)
            if table.alias:
                aliases.add(str(table.alias).lower())
    return aliases, base_table_count


def _top_level_conjuncts(expression: exp.Expression) -> list[exp.Expression]:
    if isinstance(expression, exp.And):
        return [
            *_top_level_conjuncts(expression.this),
            *_top_level_conjuncts(expression.expression),
        ]
    if isinstance(expression, exp.Or):
        return []
    return [expression]


def _dialect(snapshot: Snapshot) -> str | None:
    dialect = str(schema_config(snapshot).get("dialect") or "").lower()
    return {"mssql": "tsql", "postgresql": "postgres", "sqlserver": "tsql"}.get(
        dialect, dialect or None
    )


__all__ = ["sql_field_lineage", "sql_result_profile"]
