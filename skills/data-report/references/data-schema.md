# Input JSON Schema

```json
{
  "title": "报告标题",
  "subtitle": "副标题或时间范围",
  "footer": "页脚说明文字",
  "kpis": [
    {"label": "指标名称", "value": "显示值", "trend": "+12%", "direction": "up"}
  ],
  "charts": [
    {
      "type": "bar | line | pie | doughnut",
      "title": "图表标题",
      "labels": ["一月", "二月", "三月"],
      "datasets": [
        {"label": "系列名", "data": [10, 20, 30]}
      ]
    }
  ],
  "tables": [
    {
      "title": "表格标题",
      "columns": ["项目", "金额", "状态"],
      "rows": [["项目A", "100万", "进行中"]],
      "badge_columns": {
        "状态": {"进行中": "blue", "已完成": "green", "逾期": "red"}
      }
    }
  ]
}
```

## Field Notes
- `direction`: one of `up` (green arrow), `down` (red arrow), `flat` (neutral).
- `type`: bar/line for trends, pie/doughnut for composition.
- `badge_columns`: map a column name to a value->color dictionary for pill badges.
- All text is auto HTML-escaped by the renderer.
