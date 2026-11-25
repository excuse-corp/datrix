"""Build a dashboard from structured data without sending the template to the model."""

import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from check_report import check
from verify_calcs import verify_row

_DATA_BLOCK = re.compile(r"^const DATA = \{.*?^\};", re.MULTILINE | re.DOTALL)
_CHART_TYPES = {"bar", "groupedBar", "line", "multiLine", "stackedBar", "hbar", "donut"}


def resolve_metrics(value, metrics):
    if isinstance(value, list):
        return [resolve_metrics(item, metrics) for item in value]
    if not isinstance(value, dict):
        return value
    if "metric" in value:
        metric_id = value["metric"]
        if metric_id not in metrics:
            raise ValueError(f"未定义的指标 {metric_id}")
        field = "computed_raw" if value.get("raw") is True else "computed"
        return metrics[metric_id][field]
    return {key: resolve_metrics(item, metrics) for key, item in value.items()}


def _validate_chart(chart: dict) -> None:
    kind = chart.get("type")
    if kind not in _CHART_TYPES:
        raise ValueError(f"不支持的图表类型 {kind}")
    if kind in {"donut", "hbar"}:
        items = chart.get("items" if kind == "donut" else "rows", [])
        series = [[item["value"] for item in items]]
    else:
        series = [item["data"] for item in chart.get(
            "segments" if kind == "stackedBar" else "series", []
        )]
    if not series or any(
        not values or any(
            type(value) not in (int, float) or not math.isfinite(value)
            for value in values
        )
        for values in series
    ):
        raise ValueError(f"图表 {chart.get('title', kind)} 需要非空的数值序列")
    if kind in {"bar", "groupedBar", "stackedBar"} and any(
        len(values) != len(chart.get("cats", [])) for values in series
    ):
        raise ValueError("柱状图的数值个数须与类别个数一致")
    if kind == "donut":
        if min(series[0]) < 0 or sum(series[0]) <= 0:
            raise ValueError("环形图仅支持合计大于零的非负数值")
        for item in items:
            item.setdefault("share", item["value"])


def build_report(data: dict, calcs: dict, output_dir: Path) -> Path:
    metrics = {}
    for row in calcs.get("rows", []):
        metric_id = row["id"]
        if not metric_id or metric_id in metrics:
            raise ValueError(f"指标编号缺失或重复 {metric_id}")
        result = verify_row(row, calcs.get("vars"), use_computed=True)
        if result["status"] == "fail":
            detail = result.get("error") or f"报告值 {result['reported']}，复算值 {result['computed']}"
            raise ValueError(f"指标 {metric_id} 未通过复算（{detail}）")
        metrics[metric_id] = result

    data = resolve_metrics(data, metrics)
    if not data.get("meta", {}).get("title"):
        raise ValueError("请填写 data.meta.title")
    if not data["meta"].get("note"):
        raise ValueError("请在 data.meta.note 写明数据来源和时间口径")
    data["meta"].setdefault(
        "generatedAt", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    )
    if data.get("hero"):
        _validate_chart({"type": "line", **data["hero"]})
    for grid in ("gridA", "gridB", "gridC"):
        for chart in data.get(grid, []):
            _validate_chart(chart)
    table = data.get("table")
    if table and any(len(row) != len(table["columns"]) for row in table["rows"]):
        raise ValueError("明细表各行的单元格数需与列标题一致")
    audit_fields = ("id", "name", "formula", "computed", "reported", "diff", "status")
    data["audit"] = {
        "source": "verify_calcs.py",
        "note": calcs.get("note", "") if metrics else "本报告无自行计算指标",
        "rows": [{key: row[key] for key in audit_fields} for row in metrics.values()],
    }
    template_path = Path(__file__).resolve().parents[1] / "templates/dashboard_template.html"
    template = template_path.read_text(encoding="utf-8")
    # Keep data inside the script element even if a field contains a closing tag.
    payload = json.dumps(data, ensure_ascii=True, allow_nan=False).replace("<", r"\u003c")
    html, count = _DATA_BLOCK.subn(lambda _: f"const DATA = {payload};", template, count=1)
    if count != 1:
        raise ValueError("模板缺少 DATA 区块")

    with tempfile.TemporaryDirectory(dir=output_dir) as folder:
        draft = Path(folder) / "Report.html"
        draft.write_text(html, encoding="utf-8")
        problems, _ = check(str(draft))
        if problems:
            raise ValueError("；".join(problems))
        path = output_dir / f"Report-{Path(folder).name}.html"
        draft.replace(path)
    return path


def main() -> int:
    try:
        args = json.loads(sys.argv[1])
        path = build_report(args["data"], args.get("calcs", {}), Path(os.environ["OUTPUT_DIR"]))
    except (KeyError, TypeError, AttributeError, ValueError, OSError) as exc:
        print(f"报告生成失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps({"file_path": str(path)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
