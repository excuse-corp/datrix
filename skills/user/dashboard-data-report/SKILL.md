---
name: dashboard-data-report
description: Generate a self-contained HTML dashboard from data with KPIs, charts, detail rows, and formula-based calculation records.
---

# Dashboard Data Report

优先使用当前会话已经取得的数据。在存在影响口径的歧义或用户要求确认时再询问。保留用户要求的明细和数据覆盖范围，缺失值用 null 表示。报告须注明来源和时间口径。

## 常规报告

数据已经齐备时，直接调用一次 `html_interpreter`。准备配置、计算、检查和展示属于同一个交付步骤，无需创建待办计划。工具会计算公式、填充模板、检查 HTML 并展示报告。

```json
{
  "dashboard_report": {
    "brief_delivery": true,
    "data": {
      "meta": {"title": "各部门项目概览", "note": "来源为项目查询结果，统计截至 11 月 24 日"},
      "kpi": {"label": "项目数", "value": {"metric": "total"}},
      "gridA": [
        {"type": "bar", "title": "各部门项目数", "cats": ["甲", "乙"],
         "series": [{"name": "项目数", "color": "#D67040", "data": [3, 6]}]}
      ],
      "table": {"columns": ["部门", "项目数"], "rows": [["甲", 3], ["乙", 6]]},
      "globalNote": "每行代表一个部门。"
    },
    "calcs": {
      "vars": {"counts": [3, 6]},
      "rows": [{"id": "total", "name": "项目总数", "formula": "sum(counts)", "decimals": 0}]
    }
  }
}
```

- 用真实来源数据替换示例。根据需要填写 `kpi`、`gridA`、`table`、`globalNote`。缺少时间序列时，类别比较用 `bar` 或 `groupedBar`。其他图表和可选区块见 [图表配置](references/chart-config.md)。
- 派生指标写成公式，用 `{"metric":"编号"}` 引用格式化数值。图表数据或其他数值字段用 `{"metric":"编号","raw":true}`。金额和百分比分别用 `unit:"currency"` 和 `unit:"percent"`。公式 `pct`、`delta`、`cagr` 返回百分数。没有派生指标时省略 `calcs`。
- 用户只要求报告时设 `brief_delivery: true`。看到工具确认报告已展示后，调用 `terminate`，用一两句交付。用户同时要求在聊天中分析时，省略 `brief_delivery` 并给出所需分析。
- 工具报错时修正指出的配置或公式并重试。基于来源披露单位、统计范围和舍入方式。

## 定制报告

用户需要自定义布局时，按 [图表配置](references/chart-config.md) 填充 `templates/dashboard_template.html` 的 `DATA`，或按要求设计单文件 HTML。先运行 `verify_calcs.py` 以校验派生数值和生成核对表。

```json
{"skill_name":"dashboard-data-report","script_file_name":"verify_calcs.py","args":{"calcs_file":"绝对路径","strict":true,"emit_audit":true}}
```

计算文件结构见 [复算示例](templates/calcs.example.json)。修复失败项，将输出填入 `DATA.audit`，再用 `scripts/check_report.py` 检查结构。源数据报告也保留核对表，注明「本报告无自行计算指标」。使用 `html_interpreter` 展示。
