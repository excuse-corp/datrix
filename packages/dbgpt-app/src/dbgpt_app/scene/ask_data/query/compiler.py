"""Deterministic parameterized compilation from QuerySpec to SQL."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import MetaData, Table, asc, bindparam, column, desc, func, select

from ..schemas.snapshot import Snapshot
from .capabilities import build_query_capabilities
from .query_spec import QuerySpec
from .validator import QuerySpecValidator


class CompiledQuery:
    def __init__(
        self,
        sql: str,
        parameters: dict[str, Any],
        selected_keys: list[str],
        compiler_version: str,
        query_spec_hash: str,
    ):
        self.sql = sql
        self.parameters = parameters
        self.selected_keys = selected_keys
        self.compiler_version = compiler_version
        self.query_spec_hash = query_spec_hash
        self.sql_hash = f"sha256:{hashlib.sha256(sql.encode('utf-8')).hexdigest()}"


class QuerySpecCompiler:
    compiler_version = "1.0"

    def __init__(self, validator: QuerySpecValidator | None = None):
        self.validator = validator or QuerySpecValidator()

    def compile(self, spec: QuerySpec, snapshot: Snapshot) -> CompiledQuery:
        self.validator.validate(spec, snapshot)
        runtime = snapshot.runtime_config
        capabilities = build_query_capabilities(snapshot)
        view_name = runtime["view"]
        schema, _, table_name = view_name.rpartition(".")
        table = Table(table_name or schema, MetaData(), schema=schema or None)
        dimensions = {item["key"]: item for item in capabilities.dimensions}
        metrics = {item["key"]: item for item in capabilities.metrics}
        selected_keys: list[str] = []
        selections = []
        for key in spec.dimensions:
            item = dimensions[key]
            expression = column(item["field"], _selectable=table).label(key)
            selections.append(expression)
            selected_keys.append(key)
            if item.get("label_field"):
                label_key = f"{key}_label"
                selections.append(
                    column(item["label_field"], _selectable=table).label(label_key)
                )
                selected_keys.append(label_key)
        for key in spec.metrics:
            item = metrics[key]
            field = column(item.get("field") or "*", _selectable=table)
            aggregation = item["aggregation"].lower()
            if aggregation == "count":
                expression = func.count()
            elif aggregation in {"count_distinct", "distinct_count"}:
                expression = func.count(field.distinct())
            else:
                expression = getattr(func, aggregation)(field)
            selections.append(expression.label(key))
            selected_keys.append(key)
        statement = select(*selections).select_from(table)
        parameters: dict[str, Any] = {}
        conditions = []
        time_config = capabilities.time
        if spec.time_range and time_config.get("field"):
            time_field = column(time_config["field"], _selectable=table)
            start_name, end_name = "p_time_start", "p_time_end"
            conditions.extend(
                [time_field >= bindparam(start_name), time_field < bindparam(end_name)]
            )
            parameters.update(
                {
                    start_name: spec.time_range.start,
                    end_name: spec.time_range.end_exclusive,
                }
            )
        for index, item in enumerate(spec.filters):
            dimension = dimensions[item.dimension]
            field = column(dimension["field"], _selectable=table)
            values = item.values
            names = [
                f"p_filter_{index}_{value_index}" for value_index in range(len(values))
            ]
            if item.operator == "eq":
                conditions.append(field == bindparam(names[0]))
            elif item.operator == "neq":
                conditions.append(field != bindparam(names[0]))
            elif item.operator == "in":
                conditions.append(field.in_([bindparam(name) for name in names]))
            elif item.operator in {"like", "gt", "gte", "lt", "lte"}:
                operator = {
                    "like": field.like,
                    "gt": field.__gt__,
                    "gte": field.__ge__,
                    "lt": field.__lt__,
                    "lte": field.__le__,
                }[item.operator]
                conditions.append(operator(bindparam(names[0])))
            for name, value in zip(names, values):
                parameters[name] = value
        if conditions:
            statement = statement.where(*conditions)
        if spec.dimensions:
            statement = statement.group_by(
                *[
                    column(dimensions[key]["field"], _selectable=table)
                    for key in spec.dimensions
                ]
            )
        for item in spec.order_by:
            expression = column(item.key)
            statement = statement.order_by(
                desc(expression)
                if item.direction.lower() == "desc"
                else asc(expression)
            )
        max_rows = capabilities.query_limits.get("max_rows", spec.limit)
        fetch_limit = min(spec.limit + 1, max_rows + 1)
        statement = statement.limit(bindparam("p_limit", value=fetch_limit))
        parameters["p_limit"] = fetch_limit
        compiled = statement.compile(compile_kwargs={"render_postcompile": True})
        canonical_spec = json.dumps(
            spec.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        query_hash = (
            f"sha256:{hashlib.sha256(canonical_spec.encode('utf-8')).hexdigest()}"
        )
        return CompiledQuery(
            str(compiled), parameters, selected_keys, self.compiler_version, query_hash
        )


__all__ = ["CompiledQuery", "QuerySpecCompiler"]
