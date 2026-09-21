#!/usr/bin/env python3
"""Small, allow-listed client for Chengqin Indicator Hub OpenAPI."""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import time
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TOKEN_CACHE: Dict[str, tuple[str, float]] = {}
ALLOWED_FILTER_OPS = {
    "EQ", "NE", "IN", "NOTIN", "LIKE", "NOTLIKE",
    "ISNULL", "ISNOTNULL", "LT", "GT", "LTE", "GTE",
}
ALLOWED_LOGICAL_OPS = {"AND", "OR"}


class MetricApiError(RuntimeError):
    """Expected API or configuration failure."""


def _arg(args: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in args and args[name] is not None:
            return args[name]
    return default


def _require(value: Any, name: str) -> Any:
    if value is None or value == "" or value == []:
        raise MetricApiError(f"Missing required parameter: {name}")
    return value


def _config() -> tuple[str, str, str, str, float]:
    base_url = os.environ.get("CHENGQIN_METRIC_BASE_URL", "").strip().rstrip("/")
    appkey = os.environ.get("CHENGQIN_METRIC_APPKEY", "").strip()
    secret = os.environ.get("CHENGQIN_METRIC_SECRET", "")
    username = os.environ.get("CHENGQIN_METRIC_USERNAME", "").strip()
    timeout_raw = os.environ.get("CHENGQIN_METRIC_TIMEOUT", "20")

    if not base_url:
        raise MetricApiError("Missing environment variable: CHENGQIN_METRIC_BASE_URL")
    if not appkey:
        raise MetricApiError("Missing environment variable: CHENGQIN_METRIC_APPKEY")
    if not secret:
        raise MetricApiError("Missing environment variable: CHENGQIN_METRIC_SECRET")
    try:
        timeout = max(1.0, float(timeout_raw))
    except ValueError as exc:
        raise MetricApiError("CHENGQIN_METRIC_TIMEOUT must be numeric") from exc
    return base_url, appkey, secret, username, timeout


def _json_request(
    url: str,
    *,
    headers: Mapping[str, str],
    payload: Optional[Mapping[str, Any]],
    timeout: float,
) -> Dict[str, Any]:
    body = None
    request_headers = dict(headers)
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    request = Request(url, data=body, headers=request_headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise MetricApiError(f"HTTP {exc.code}: {_safe_error_detail(raw)}") from exc
    except URLError as exc:
        raise MetricApiError(f"Network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise MetricApiError("Request timed out") from exc

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MetricApiError("API returned non-JSON content") from exc
    if not isinstance(result, dict):
        raise MetricApiError("API returned an unexpected JSON shape")
    return result


def _safe_error_detail(raw: str) -> str:
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return str(value.get("msg") or value.get("message") or "request failed")
    except json.JSONDecodeError:
        pass
    return raw[:300] or "request failed"


def _check_envelope(result: Mapping[str, Any]) -> Any:
    code = result.get("code")
    if code not in (0, "0", None):
        raise MetricApiError(str(result.get("msg") or f"API returned code {code}"))
    return result.get("data")


def _username(args: Mapping[str, Any], configured: str) -> str:
    username = str(_arg(args, "username", default=configured) or "").strip()
    if not username:
        raise MetricApiError(
            "Missing username: pass args.username or set CHENGQIN_METRIC_USERNAME"
        )
    return username


def _get_token(
    *, base_url: str, appkey: str, secret: str, username: str, timeout: float
) -> str:
    cache_key = f"{base_url}|{appkey}|{username}"
    cached = TOKEN_CACHE.get(cache_key)
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    nonce = random.randint(1_000_000_000, 9_999_999_999)
    curtime = int(time.time())
    checksum_input = f"{username}{secret}{nonce}{curtime}"
    checksum = hashlib.sha1(checksum_input.encode("utf-8")).hexdigest()
    result = _json_request(
        f"{base_url}/open-api/system/oauth2-openapi/token",
        headers={
            "appkey": appkey,
            "checksum": checksum,
            "curtime": str(curtime),
            "nonce": str(nonce),
            "username": username,
        },
        payload=None,
        timeout=timeout,
    )
    data = _check_envelope(result)
    if not isinstance(data, dict) or not data.get("access_token"):
        raise MetricApiError("Token response did not contain access_token")

    token = str(data["access_token"])
    try:
        expires_in = max(60, int(data.get("expires_in", 1800)))
    except (TypeError, ValueError):
        expires_in = 1800
    TOKEN_CACHE[cache_key] = (token, time.time() + expires_in)
    return token


def _page(args: Mapping[str, Any]) -> Dict[str, Any]:
    field = str(
        _arg(
            args,
            "page_field",
            default=os.environ.get("CHENGQIN_METRIC_PAGE_FIELD", "pageNum"),
        )
    )
    if field not in {"pageNum", "pageNo"}:
        raise MetricApiError("page_field must be pageNum or pageNo")
    page_num = int(_arg(args, "page_num", "pageNum", "pageNo", default=1))
    page_size = int(_arg(args, "page_size", "pageSize", default=50))
    if page_num < 1:
        raise MetricApiError("page_num must be >= 1")
    if page_size < 1 or page_size > 200:
        raise MetricApiError("page_size must be between 1 and 200")
    return {field: page_num, "pageSize": page_size}


def _filter(value: Any) -> Optional[Dict[str, Any]]:
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise MetricApiError("filter must be an object")
    _validate_filter(value)
    return value


def _validate_filter(value: Mapping[str, Any]) -> None:
    op = str(value.get("op", "")).upper()
    if op not in ALLOWED_LOGICAL_OPS:
        raise MetricApiError("filter.op must be AND or OR")
    exprs = value.get("exprs")
    if not isinstance(exprs, list):
        raise MetricApiError("filter.exprs must be an array")
    for expr in exprs:
        if not isinstance(expr, dict) or not expr.get("field"):
            raise MetricApiError("each filter expression needs field")
        expr_op = str(expr.get("op", "")).upper()
        if expr_op not in ALLOWED_FILTER_OPS:
            raise MetricApiError(f"unsupported filter operator: {expr_op}")
        if expr_op not in {"ISNULL", "ISNOTNULL"} and "value" not in expr:
            raise MetricApiError(f"filter value is required for {expr_op}")
    querys = value.get("querys", [])
    if querys is not None:
        if not isinstance(querys, list):
            raise MetricApiError("filter.querys must be an array")
        for nested in querys:
            if not isinstance(nested, dict):
                raise MetricApiError("nested filters must be objects")
            _validate_filter(nested)


def _post(args: Mapping[str, Any], path: str, payload: Mapping[str, Any]) -> Any:
    base_url, appkey, secret, configured_username, timeout = _config()
    username = _username(args, configured_username)
    cache_key = f"{base_url}|{appkey}|{username}"

    for attempt in range(2):
        token = _get_token(
            base_url=base_url,
            appkey=appkey,
            secret=secret,
            username=username,
            timeout=timeout,
        )
        try:
            result = _json_request(
                f"{base_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
                payload=payload,
                timeout=timeout,
            )
            return _check_envelope(result)
        except MetricApiError as exc:
            error_text = str(exc)
            if attempt == 0 and (
                error_text.startswith("HTTP 401")
                or error_text.startswith("API returned code 401")
            ):
                TOKEN_CACHE.pop(cache_key, None)
                continue
            raise
    raise MetricApiError("Authentication retry failed")


def _scene_payload(args: Mapping[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    name = _arg(args, "name")
    status = _arg(args, "scene_version_status", "sceneVersionStatus")
    if name not in (None, ""):
        payload["name"] = name
    if status is not None:
        payload["sceneVersionStatus"] = int(status)
    payload.update(_page(args))
    return payload


def _metric_payload(args: Mapping[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "sceneVersionUid": _require(
            _arg(args, "scene_version_uid", "sceneVersionUid"),
            "scene_version_uid",
        )
    }
    mapping = {
        "instance_codes": "instanceCodes",
        "metric_instance_name": "metricInstanceName",
        "tags": "tags",
        "state": "state",
    }
    for source, target in mapping.items():
        value = _arg(args, source, target)
        if value not in (None, "", []):
            payload[target] = value
    payload.update(_page(args))
    return payload


def _measure_payload(args: Mapping[str, Any], *, by_id: bool) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "sceneVersionUid": _require(
            _arg(args, "scene_version_uid", "sceneVersionUid"),
            "scene_version_uid",
        )
    }
    key = "instanceIds" if by_id else "instanceCodes"
    arg_name = "instance_ids" if by_id else "instance_codes"
    values = _arg(args, arg_name, key)
    if values not in (None, "", []):
        payload[key] = values
    for source, target in [
        ("recalculate", "recalculate"),
        ("global_filter", "globalFilter"),
        ("instances", "instances"),
    ]:
        value = _arg(args, source, target)
        if value is not None:
            payload[target] = value
    if "globalFilter" in payload:
        payload["globalFilter"] = _filter(payload["globalFilter"])
    if "instances" in payload:
        payload["instances"] = _normalize_instances(payload["instances"])
    return payload


def _normalize_instances(value: Any) -> list[Dict[str, Any]]:
    if not isinstance(value, list):
        raise MetricApiError("instances must be an array")
    field_map = {
        "instance_id": "instanceId",
        "instance_code": "instanceCode",
        "dims": "dims",
        "filter": "filter",
        "id": "id",
        "recalculate": "recalculate",
        "recalculate_code": "recalculateCode",
        "date_filter": "dateFilter",
    }
    normalized: list[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise MetricApiError("each instances item must be an object")
        output: Dict[str, Any] = {}
        for key, item_value in item.items():
            target = field_map.get(key, key)
            output[target] = item_value
        if "filter" in output and output["filter"] is not None:
            _filter(output["filter"])
        normalized.append(output)
    return normalized


def _detail_payload(
    args: Mapping[str, Any], *, distinct: bool = False
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "sceneVersionUid": _require(
            _arg(args, "scene_version_uid", "sceneVersionUid"),
            "scene_version_uid",
        )
    }
    instance_id = _arg(args, "instance_id", "instanceId")
    instance_code = _arg(args, "instance_code", "instanceCode")
    if instance_id is None and instance_code in (None, ""):
        raise MetricApiError("Provide instance_id or instance_code")
    if instance_id is not None:
        payload["instanceId"] = int(instance_id)
    elif instance_code not in (None, ""):
        payload["instanceCode"] = instance_code
    filter_value = _filter(_arg(args, "filter"))
    if filter_value is not None:
        payload["filter"] = filter_value
    if distinct:
        payload["columnAlias"] = _require(
            _arg(args, "column_alias", "columnAlias"),
            "column_alias",
        )
        search_value = _arg(args, "search_value", "searchValue")
        if search_value not in (None, ""):
            payload["searchValue"] = search_value
    else:
        order_field = _arg(args, "order_field", "orderField")
        order_type = _arg(args, "order_type", "orderType")
        if order_field not in (None, ""):
            payload["orderField"] = order_field
        if order_type not in (None, ""):
            if order_type not in {"asc", "desc"}:
                raise MetricApiError("order_type must be asc or desc")
            payload["orderType"] = order_type
    payload.update(_page(args))
    return payload


def query(args: Mapping[str, Any]) -> Any:
    action = str(args.get("action", "")).strip().lower()
    if action == "list_scenes":
        return _post(
            args,
            "/open-api/metric/scene/versions/with-permission",
            _scene_payload(args),
        )
    if action == "list_metrics":
        return _post(
            args,
            "/open-api/metric/scene/version/metric-instances",
            _metric_payload(args),
        )
    if action == "list_metrics_with_versions":
        payload: Dict[str, Any] = {
            "sceneVersionUids": _require(
                _arg(args, "scene_version_uids", "sceneVersionUids"),
                "scene_version_uids",
            )
        }
        tags = _arg(args, "tags")
        if tags not in (None, "", []):
            payload["tags"] = tags
        return _post(
            args,
            "/open-api/metric/scene/version/metric-instances-withVersions",
            payload,
        )
    if action == "lineage":
        payload = {
            "sceneVersionUid": _require(
                _arg(args, "scene_version_uid", "sceneVersionUid"),
                "scene_version_uid",
            )
        }
        codes = _arg(args, "instance_codes", "instanceCodes")
        if codes not in (None, "", []):
            payload["instanceCodes"] = codes
        return _post(
            args,
            "/open-api/metric/scene/version/metric-instance/lineage",
            payload,
        )
    if action == "measure":
        return _post(
            args,
            "/open-api/metric/instance/codes/measure",
            _measure_payload(args, by_id=False),
        )
    if action == "measure_by_id":
        return _post(
            args,
            "/open-api/metric/instance/ids/measure",
            _measure_payload(args, by_id=True),
        )
    if action == "detail":
        return _post(
            args,
            "/open-api/metric/instance/detail",
            _detail_payload(args),
        )
    if action == "distinct":
        return _post(
            args,
            "/open-api/metric/instance/detail-distinct-field",
            _detail_payload(args, distinct=True),
        )
    if action == "list_tags":
        return _post(args, "/open-api/metric/metricmgt/tags/list", {})
    raise MetricApiError(
        "Unsupported action. Use list_scenes, list_metrics, "
        "list_metrics_with_versions, lineage, measure, measure_by_id, "
        "detail, distinct, or list_tags"
    )


def main(argv: Optional[Iterable[str]] = None) -> int:
    values = list(argv if argv is not None else sys.argv[1:])
    if not values or values[0] in {"-h", "--help"}:
        print(
            json.dumps(
                {
                    "usage": "query_metric.py '<json args>'",
                    "required_env": [
                        "CHENGQIN_METRIC_BASE_URL",
                        "CHENGQIN_METRIC_APPKEY",
                        "CHENGQIN_METRIC_SECRET",
                    ],
                    "actions": [
                        "list_scenes",
                        "list_metrics",
                        "list_metrics_with_versions",
                        "lineage",
                        "measure",
                        "measure_by_id",
                        "detail",
                        "distinct",
                        "list_tags",
                    ],
                },
                ensure_ascii=False,
            )
        )
        return 0
    try:
        args = json.loads(values[0])
        if not isinstance(args, dict):
            raise MetricApiError("arguments must be a JSON object")
        result = query(args)
        print(
            json.dumps(
                {
                    "ok": True,
                    "action": args.get("action"),
                    "data": result,
                    "message": "",
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (MetricApiError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "action": None,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
