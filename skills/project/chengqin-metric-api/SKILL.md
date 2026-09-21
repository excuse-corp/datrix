---
name: chengqin-metric-api
description: Query Chengqin Education Indicator Hub OpenAPI through natural-language requests in Datrix. Use when the user asks for indicator values, metric definitions, scene versions, dimensions, detail rows, distinct filter values, metric lineage, or tags from the Chengqin indicator platform.
---

# Chengqin Metric API

Use the bundled API client to answer natural-language questions against the Chengqin Education Indicator Hub. The API is metadata-driven: discover the permitted scene version and metric definition before executing a metric query when the user has not supplied an unambiguous identifier.

## Required configuration

Read these values from the process environment. Never place them in this file, references, prompts, logs, or tool output:

- CHENGQIN_METRIC_BASE_URL — indicator platform base URL.
- CHENGQIN_METRIC_APPKEY — third-party application key.
- CHENGQIN_METRIC_SECRET — third-party application secret.
- CHENGQIN_METRIC_USERNAME — optional fixed service-account employee number; prefer the current Datrix user's mapped employee number when available.

The local deployment convention is .env.dataman.local, which must remain untracked. The script reads it indirectly through the service environment; it does not load dotenv files itself.

## Natural-language workflow

1. Identify whether the user wants metadata, an aggregate result, detail rows, distinct field values, lineage, or tags.
2. If the scene version is unknown, call list_scenes with the user's name or keyword. Prefer the permission-aware endpoint so results respect the signed-in user.
3. If the metric identifier is unknown, call list_metrics for the chosen scene_version_uid; match by metric name, code, tags, dimensions, and definition text. Do not invent a metric code or dimension.
4. For a numeric or categorical result, call measure with scene_version_uid, the selected metric code(s), requested dimensions, and a validated filter. Use measure_by_id only when the user or metadata provides metric IDs.
5. For row-level data, call detail; use distinct to populate or validate a field-value filter.
6. Explain the result using the returned metric definition, dimensions, measure type, assessment data, and any limitations. Preserve the API's units and time semantics.

When the user request is ambiguous, ask only for the missing scene, metric, time range, or dimension. Do not guess from similarly named metrics.

## Tool execution

Use execute_skill_script_file with scripts/query_metric.py. Pass one JSON object in args, for example:

~~~json
{
  "action": "measure",
  "scene_version_uid": "<uid>",
  "instance_codes": ["<code>"],
  "instances": [
    {
      "instance_code": "<code>",
      "dims": ["学院", "年度"],
      "filter": {
        "op": "AND",
        "exprs": [
          {"field": "学院", "op": "EQ", "value": "计算机学院"},
          {"field": "年度", "op": "EQ", "value": "2025"}
        ]
      }
    }
  ]
}
~~~

Supported actions are list_scenes, list_metrics, list_metrics_with_versions, lineage, measure, measure_by_id, detail, distinct, and list_tags. See references/openapi-summary.md for endpoint details and documented inconsistencies.

## Query safety

- Use only the endpoint whitelist implemented by the script. Do not construct arbitrary URLs.
- Validate filter fields against the selected metric's dimensions or measure metadata.
- Use page_num and page_size for all paginated calls; keep page_size at or below 200 unless the platform owner approves another limit.
- Treat API envelope code != 0 as an error. A 401 response means the token must be reacquired.
- Never expose secret, checksum, access token, or raw authorization headers in the response.
- measure values may be strings; interpret them using measureType and do not silently change units.
- Use recalculate only when the user asks for real-time recalculation or the metric definition explicitly requires it.

## Output contract

The script returns JSON with:

~~~json
{"ok": true, "action": "measure", "data": {}, "message": ""}
~~~

On failure it returns:

~~~json
{"ok": false, "action": "measure", "error_type": "...", "message": "..."}
~~~

Use the data object as the source of truth. Do not fabricate missing values.

