---
name: data-report
description: "Generate styled HTML business data reports (KPIs, charts, tables) from structured data. Use when the user asks to create a data report, business dashboard, analysis report webpage, or wants data visualized in a professional blue-themed HTML report. Trigger keywords: 数据报告, 业务报告, 分析报告, 报表, data report, dashboard, report generation."
---

# Data Report Generator

Generate professional, blue-themed HTML business reports containing KPI cards,
interactive Chart.js charts, and styled data tables.

## Visual Style
The template follows a consistent enterprise-blue design system:
- Primary color `#1a56db`, dark header gradient `#0f2b5b -> #1a56db`
- White cards with soft shadows, rounded corners, blue left-border accents
- KPI cards (label / big value / trend arrow), Chart.js charts, badge-styled tables

## Core Workflow
1. **Prepare data**: Build a JSON object matching the schema in
   `references/data-schema.md`. Gather KPIs, chart series, and table rows from
   the user data (CSV/Excel/AskData result).
2. **Generate HTML**: Run the script to render the template:
   ```
   python scripts/generate_report.py --data <data.json> --output report.html
   ```
3. **Render**: Call `html_interpreter` with `{"file_path": "report.html"}` to
   display the report on the right panel. This step is mandatory for display.

## When to Skip Steps
- If the user already provides a fully structured JSON, skip data preparation
  and go directly to step 2.

## Key Files
- `scripts/generate_report.py` — renderer (CLI: --data, --output, optional --template)
- `assets/report-template.html` — the HTML/CSS template with `{{PLACEHOLDER}}` tokens
- `references/data-schema.md` — full input JSON schema and field explanations
- `assets/sample-data.json` — a working example input

## Notes
- Charts use Chart.js v4 via CDN; pie/doughnut auto-color datasets.
- Use `badge_columns` in a table to color status values (blue/green/orange/red).
- Always HTML-escape user content (the script does this automatically).
