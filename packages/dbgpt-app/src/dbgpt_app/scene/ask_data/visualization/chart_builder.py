"""Whitelist-only chart selection from structured results."""

from __future__ import annotations

from ..schemas.chart import ChartOption, ChartSpec
from ..schemas.combine import CombinedResult


class ChartSpecBuilder:
    max_categories = 20

    def build(
        self, result: CombinedResult, *, title: str = "查询结果"
    ) -> list[ChartSpec]:
        if not result.rows or not result.columns:
            return [
                ChartSpec(
                    type="table", title=title, option=ChartOption(), data=result.rows
                )
            ]
        dimensions = [column for column in result.columns if column.type == "dimension"]
        values = [
            column
            for column in result.columns
            if column.type in {"metric", "derived_metric"}
        ]
        if not values:
            return [
                ChartSpec(
                    type="table", title=title, option=ChartOption(), data=result.rows
                )
            ]
        if not dimensions and len(result.rows) == 1:
            return [
                ChartSpec(
                    type="metric",
                    title=title,
                    option=ChartOption(
                        value_keys=[column.key for column in values],
                        unit=values[0].unit,
                    ),
                    data=result.rows,
                )
            ]
        if len(result.rows) > self.max_categories:
            return [
                ChartSpec(
                    type="table", title=title, option=ChartOption(), data=result.rows
                )
            ]
        category = dimensions[0] if dimensions else None
        category_key = category.key if category else ""
        is_time_series = bool(
            result.time_grain
            or any(
                token in category_key.lower()
                for token in ("date", "time", "month", "year")
            )
        )
        is_share = len(values) == 1 and (
            values[0].unit == "%"
            or any(
                token in values[0].key.lower()
                for token in ("rate", "ratio", "share")
            )
        )
        chart_type = (
            "line"
            if is_time_series
            else "donut"
            if is_share
            else "bar"
            if len(values) == 1
            else "grouped_bar"
        )
        return [
            ChartSpec(
                type=chart_type,
                title=title,
                option=ChartOption(
                    category_key=category.key if category else None,
                    value_keys=[column.key for column in values],
                    unit=values[0].unit,
                ),
                data=result.rows,
            )
        ]


__all__ = ["ChartSpecBuilder"]
