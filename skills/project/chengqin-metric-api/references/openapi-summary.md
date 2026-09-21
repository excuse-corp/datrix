# Chengqin Indicator Hub OpenAPI Summary

The source document is ref/诚勤指标中枢OpenAPI开发指南.pdf. This reference keeps only the operational details needed by the Skill; consult the source PDF when the platform behavior differs.

## Authentication

POST /open-api/system/oauth2-openapi/token

Required headers:

- appkey
- checksum = SHA1(username + secret + nonce + curtime)
- curtime as Unix seconds, not milliseconds
- nonce as a 10-digit random number
- username as the signed-in employee number

The response contains data.access_token, with an advertised lifetime of about 1800 seconds. There is no usable refresh-token endpoint; reacquire the token after expiry or a 401 response.

## Endpoint map

| Action | Method and path | Purpose |
|---|---|---|
| list_scenes | POST /open-api/metric/scene/versions/with-permission | List scene versions visible to the signed-in user |
| list_metrics | POST /open-api/metric/scene/version/metric-instances | List metric instances and definitions for one scene version |
| list_metrics_with_versions | POST /open-api/metric/scene/version/metric-instances-withVersions | List metric instances across multiple scene version UIDs |
| lineage | POST /open-api/metric/scene/version/metric-instance/lineage | Return dependent metrics, models, sources, and tables |
| measure | POST /open-api/metric/instance/codes/measure | Query results by metric code |
| measure_by_id | POST /open-api/metric/instance/ids/measure | Query results by metric ID |
| detail | POST /open-api/metric/instance/detail | Query paginated detail rows |
| distinct | POST /open-api/metric/instance/detail-distinct-field | Return distinct values for one detail column |
| list_tags | POST /open-api/metric/metricmgt/tags/list | List available metric tags |

## Filter DSL

Filters use an object with op, exprs, and optional nested querys. Supported expression operators are EQ, NE, IN, NOTIN, LIKE, NOTLIKE, ISNULL, ISNOTNULL, LT, GT, LTE, and GTE. ISNULL and ISNOTNULL do not need a meaningful value.

Example:

~~~json
{
  "op": "AND",
  "exprs": [
    {"field": "学院", "op": "EQ", "value": "计算机学院"}
  ],
  "querys": [
    {
      "op": "OR",
      "exprs": [
        {"field": "学制", "op": "EQ", "value": "4"},
        {"field": "学制", "op": "EQ", "value": "5"}
      ]
    }
  ]
}
~~~

## Known documentation inconsistencies

- Parameter tables sometimes say pageNo, while request examples use pageNum. The client defaults to pageNum and supports page_field or CHENGQIN_METRIC_PAGE_FIELD for a target environment that requires pageNo.
- The example for the ID-based measure endpoint contains the code-based URL; the client uses the documented ID endpoint /ids/measure.
- Responses vary between uid/uuid and versionUid/versionUuid; callers should tolerate both names when interpreting metadata.
- The distinct-field example in the PDF appears to omit a JSON comma after columnAlias; follow valid JSON in the client payload.

