"""Read-only CloudWatch HTTP RED capture and exact log-distribution rollup.

Development source adapter only: deployment mapping, source parity, live lag/cost
and compatible model approval need independent H3/G7 evidence.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import re

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.experiments.labels import percentile
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id, timestamp
from sentinelops.telemetry.prometheus import BucketNotReady

SOURCE_PROFILE = "cloudwatch-log-rollup-60s/v1"
MAX_PAGES, MAX_BYTES, MAX_EVENTS = 20, 20 * 1024 * 1024, 200000
WATERMARK_SECONDS = 120


def _ms(point):
    return (point - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(milliseconds=1)


def _catalog(value):
    keys = {"catalog_version", "service", "environment", "log_group_arn", "namespace",
            "request_metric", "error_metric", "dimensions", "counter_semantics"}
    if (not isinstance(value, dict) or set(value) != keys
        or value["catalog_version"] != "cloudwatch-http-red/v1"
        or value["service"] not in BUSINESS_ROUTES
        or value["counter_semantics"] != "per-request-count/v1"
        or not isinstance(value["environment"], str) or not value["environment"]):
        raise ValueError("explicit CloudWatch business catalog required")
    if not isinstance(value["log_group_arn"], str):
        raise ValueError("exact CloudWatch log group ARN required")
    arn = re.fullmatch(r"arn:(aws|aws-cn|aws-us-gov):logs:([a-z0-9-]+):([0-9]{12}):log-group:([A-Za-z0-9_./#-]+)(?::\*)?",
                       value["log_group_arn"])
    if not arn:
        raise ValueError("exact CloudWatch log group ARN required")
    names = [value[key] for key in ("namespace", "request_metric", "error_metric")]
    dimensions = value["dimensions"]
    if (not isinstance(dimensions, dict) or set(dimensions) != {"service", "environment", "method", "route"}
        or any(not isinstance(name, str) for name in dimensions.values())
        or len(set(dimensions.values())) != 4
        or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_./-]{0,254}", name)
               for name in names + list(dimensions.values()))
        or value["namespace"].startswith("AWS/") or value["request_metric"] == value["error_metric"]):
        raise ValueError("catalog requires distinct custom per-request metrics and exact dimension names")
    return arn[3]


def queries(catalog, end):
    account = _catalog(catalog)
    timestamp(end)
    if end.timestamp() % 60:
        raise ValueError("CloudWatch bucket must be UTC minute aligned")
    begin = end - timedelta(seconds=60)
    method, route = BUSINESS_ROUTES[catalog["service"]]
    fields = {"service": catalog["service"], "environment": catalog["environment"], "method": method, "route": route}
    dimensions = [{"Name": catalog["dimensions"][key], "Value": value} for key, value in sorted(fields.items())]
    metrics = [{"Id": key, "AccountId": account, "ReturnData": True, "MetricStat": {
        "Metric": {"Namespace": catalog["namespace"], "MetricName": catalog[name], "Dimensions": dimensions},
        "Period": 60, "Stat": "Sum", "Unit": "Count"}}
        for key, name in (("requests", "request_metric"), ("errors", "error_metric"))]
    pattern = "{ " + " && ".join("$.%s = %s" % (key, json.dumps(value)) for key, value in (
        ("event", "request_completed"), ("service", catalog["service"]), ("method", method), ("route", route))) + " }"
    return {
        "logs": {"logGroupIdentifier": catalog["log_group_arn"], "startTime": _ms(begin), "endTime": _ms(end)-1,
                 "filterPattern": pattern, "limit": 10000, "unmask": False},
        "metrics": {"StartTime": timestamp(begin), "EndTime": timestamp(end),
                    "MetricDataQueries": metrics, "ScanBy": "TimestampAscending", "MaxDatapoints": 2}}


def _jsonable(value):
    if isinstance(value, datetime):
        return timestamp(value.astimezone(timezone.utc)) if value.tzinfo is not None else timestamp(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _response(body, kind):
    if not isinstance(body, dict) or "Error" in body:
        raise ValueError("CloudWatch source returned an invalid response")
    if kind == "logs":
        if not isinstance(body.get("events"), list):
            raise ValueError("CloudWatch source lacks its event inventory")
        selected = {"events": body["events"]}
    else:
        if not isinstance(body.get("MetricDataResults"), list):
            raise ValueError("CloudWatch source lacks its metric inventory")
        selected = {"MetricDataResults": [
            {key: row[key] for key in ("Id", "Timestamps", "Values", "StatusCode", "Messages") if key in row}
            for row in body["MetricDataResults"]]}
        if "Messages" in body:
            selected["Messages"] = body["Messages"]
    if "nextToken" in body:
        selected["nextToken"] = body["nextToken"]
    if "NextToken" in body:
        selected["NextToken"] = body["NextToken"]
    return deepcopy(_jsonable(selected))


class CloudWatchSource:
    """Injected SDK clients need operator-configured region, timeouts and retries.

    Instantiation never imports an SDK, discovers credentials or contacts AWS.
    Clients must already target the ARN's account/region and approved endpoints.
    """
    def __init__(self, logs, metrics, catalog, *, clock=None):
        _catalog(catalog)
        region = catalog["log_group_arn"].split(":")[3]
        if any(getattr(getattr(client, "meta", None), "region_name", None) != region
               for client in (logs, metrics)):
            raise ValueError("CloudWatch client regions differ from the pinned log group ARN")
        self.logs, self.metrics, self.catalog = logs, metrics, deepcopy(catalog)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def capture(self, end):
        started = self.clock()
        request = queries(self.catalog, end)
        if started < end + timedelta(seconds=WATERMARK_SECONDS):
            raise BucketNotReady("CloudWatch development watermark has not elapsed")
        capture = {"capture_version": "cloudwatch-http-red/v1", "source_profile": SOURCE_PROFILE,
            "catalog": deepcopy(self.catalog), "catalog_sha256": stable_id(self.catalog),
            "bucket_end": timestamp(end), "started_at": timestamp(started), "logs": [], "metrics": None}
        size, tokens, token = 0, set(), None
        try:
            for _ in range(MAX_PAGES):
                query = {**request["logs"], **({"nextToken": token} if token is not None else {})}
                response = _response(self.logs.filter_log_events(**query), "logs")
                size += len(json.dumps(response, allow_nan=False).encode())
                if size > MAX_BYTES:
                    raise ValueError("CloudWatch capture byte budget exceeded")
                capture["logs"].append({"request": query, "response": response, "received_at": timestamp(self.clock())})
                token = response.get("nextToken")
                if token is None:
                    break
                if not isinstance(token, str) or not token or token in tokens:
                    raise ValueError("CloudWatch source pagination did not advance")
                tokens.add(token)
            else:
                raise ValueError("CloudWatch capture page budget exceeded")
            sdk_query = deepcopy(request["metrics"])
            for key in ("StartTime", "EndTime"):
                sdk_query[key] = utc_time(sdk_query[key])
            response = _response(self.metrics.get_metric_data(**sdk_query), "metrics")
            size += len(json.dumps(response, allow_nan=False).encode())
            if size > MAX_BYTES:
                raise ValueError("CloudWatch capture byte budget exceeded")
            capture["metrics"] = {"request": request["metrics"], "response": response,
                                  "received_at": timestamp(self.clock())}
        except Exception as exc:
            # SDK messages may contain account details, endpoint URLs or credentials.
            capture["source_error"] = {"error_type": type(exc).__name__[:64]}
        capture["collected_at"] = timestamp(self.clock())
        return capture


def _integer(value):
    return type(value) is int and value >= 0


def _counts(body, begin):
    if (not isinstance(body, dict) or not set(body) <= {"MetricDataResults", "NextToken", "Messages"}
        or "NextToken" in body or body.get("Messages")):
        raise ValueError("CloudWatch metric response is partial or has messages")
    if "Messages" in body and not isinstance(body["Messages"], list):
        raise ValueError("CloudWatch metric message inventory differs")
    rows = body["MetricDataResults"]
    if (not isinstance(rows, list) or len(rows) != 2 or not all(isinstance(row, dict) for row in rows)
        or {row.get("Id") for row in rows} != {"requests", "errors"}):
        raise ValueError("CloudWatch metric result scope differs")
    values = {}
    for row in rows:
        if (row.get("StatusCode") != "Complete" or row.get("Messages")
            or "Messages" in row and not isinstance(row["Messages"], list)):
            raise ValueError("CloudWatch metric result is incomplete")
        times, counts = row.get("Timestamps", []), row.get("Values", [])
        if times == counts == []:
            values[row["Id"]] = None  # No data is never an observed zero.
            continue
        if (not isinstance(times, list) or not isinstance(counts, list)
            or len(times) != 1 or len(counts) != 1 or utc_time(times[0]) != begin
            or type(counts[0]) not in (int, float) or not math.isfinite(counts[0])
            or not 0 <= counts[0] <= MAX_EVENTS or counts[0] != int(counts[0])):
            raise ValueError("CloudWatch Count/Sum bucket or value differs")
        values[row["Id"]] = int(counts[0])
    return values["requests"], values["errors"]


def _strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate log JSON key")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("nonfinite log JSON")
    result = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(result, dict):
        raise ValueError("completion JSON must be an object")
    return result


def decode_capture(capture, *, environment):
    if (not isinstance(capture, dict) or capture.get("capture_version") != "cloudwatch-http-red/v1"
        or capture.get("source_profile") != SOURCE_PROFILE or "source_error" in capture):
        raise ValueError("CloudWatch source capture unavailable or unsupported")
    catalog = capture["catalog"]
    _catalog(catalog)
    if catalog["environment"] != environment or capture.get("catalog_sha256") != stable_id(catalog):
        raise ValueError("CloudWatch catalog/environment identity differs")
    end, started, available = (utc_time(capture[key]) for key in ("bucket_end", "started_at", "collected_at"))
    expected = queries(catalog, end)
    if not end + timedelta(seconds=WATERMARK_SECONDS) <= started <= available:
        raise ValueError("CloudWatch source receipt/watermark differs")
    pages = capture["logs"]
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise ValueError("CloudWatch pagination inventory missing or unbounded")
    request_ids, event_ids, token, tokens, size, event_count = {}, {}, None, set(), 0, 0
    previous_receipt = started
    begin = end - timedelta(seconds=60)
    for index, page in enumerate(pages):
        query = {**expected["logs"], **({"nextToken": token} if token is not None else {})}
        received = utc_time(page["received_at"])
        if stable_id(page["request"]) != stable_id(query) or not previous_receipt <= received <= available:
            raise ValueError("CloudWatch log query/receipt chain differs")
        previous_receipt = received
        body = page["response"]
        if not isinstance(body, dict) or not set(body) <= {"events", "nextToken"}:
            raise ValueError("CloudWatch log response scope differs")
        size += len(json.dumps(body, allow_nan=False).encode())
        events = body["events"]
        if not isinstance(events, list) or len(events) > 10000:
            raise ValueError("CloudWatch log page inventory differs")
        event_count += len(events)
        if event_count > MAX_EVENTS or size > MAX_BYTES:
            raise ValueError("CloudWatch capture exceeds bounded replay inventory")
        for event in events:
            if (set(event) != {"eventId", "logStreamName", "timestamp", "ingestionTime", "message"}
                or not all(isinstance(event[key], str) and 0 < len(event[key]) <= bound
                           for key, bound in (("eventId", 512), ("logStreamName", 512), ("message", 65536)))
                or not _integer(event["timestamp"]) or not _integer(event["ingestionTime"])
                or not _ms(begin) <= event["timestamp"] < _ms(end)
                or not event["timestamp"] <= event["ingestionTime"] <= _ms(received)):
                raise ValueError("CloudWatch log identity or event/ingestion/receipt clock differs")
            key, identity = (event["logStreamName"], event["eventId"]), stable_id(event)
            if key in event_ids:
                if event_ids[key] != identity:
                    raise ValueError("conflicting duplicate CloudWatch event")
                continue
            event_ids[key] = identity
            row = _strict_json(event["message"])
            duration = row.get("duration_ms")
            if (row.get("service") != catalog["service"] or row.get("event") != "request_completed"
                or (row.get("method"), row.get("route")) != BUSINESS_ROUTES[catalog["service"]]
                or not isinstance(row.get("request_instance_id"), str)
                or not re.fullmatch("[0-9a-f]{32}", row["request_instance_id"])
                or type(row.get("timestamp_unix_ms")) is not int or row["timestamp_unix_ms"] != event["timestamp"]
                or type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0
                or type(row.get("status_code")) is not int or not 100 <= row["status_code"] <= 599):
                raise ValueError("CloudWatch completion lacks exact business identity/units")
            fact = {key: row.get(key) for key in ("timestamp_unix_ms", "duration_ms", "status_code",
                    "service", "method", "route", "trace_id", "span_id", "correlation_id")}
            identity = row["request_instance_id"]
            if identity in request_ids and stable_id(request_ids[identity]) != stable_id(fact):
                raise ValueError("conflicting duplicate application request instance")
            request_ids[identity] = fact
        token = body.get("nextToken")
        if token is not None:
            if not isinstance(token, str) or not token or token in tokens or index == len(pages)-1:
                raise ValueError("CloudWatch log pagination is incomplete or cyclic")
            tokens.add(token)
        elif index != len(pages)-1:
            raise ValueError("CloudWatch pages continue after a terminal response")
    metric = capture["metrics"]
    if (stable_id(metric["request"]) != stable_id(expected["metrics"])
        or not previous_receipt <= utc_time(metric["received_at"]) <= available):
        raise ValueError("CloudWatch metric query or receipt differs")
    size += len(json.dumps(metric["response"], allow_nan=False).encode())
    if size > MAX_BYTES:
        raise ValueError("CloudWatch capture byte budget exceeded")
    requests, errors = _counts(metric["response"], begin)
    logged_errors = sum(row["status_code"] >= 500 for row in request_ids.values())
    reason, status, coverage = [], "valid", 1.
    if requests is None or errors is None:
        status, coverage, reason = "incomplete", 0., ["missing_independent_counts"]
    elif requests != len(request_ids) or errors != logged_errors or errors > requests:
        status, coverage, reason = "invalid", 0., ["log_metric_count_mismatch"]
    elif requests == 0:
        status, reason = "no_traffic", ["no_business_requests"]
    elif requests < 30:
        status, reason = "insufficient_traffic", ["insufficient_business_requests"]
    payload = None
    if status == "valid":
        payload = {"schema_version": "1.0", "timestamp": timestamp(end), "service": catalog["service"],
            "p95_latency_ms": percentile([row["duration_ms"] for row in request_ids.values()], .95),
            "error_rate": errors / requests}
    result = {"envelope_version": "1.0", "environment": environment, "source_profile": SOURCE_PROFILE,
        "service": catalog["service"], "bucket_start": timestamp(begin), "bucket_end": timestamp(end),
        "available_at": timestamp(available), "ingested_at": timestamp(available), "payload": payload,
        "measurements": {"request_count": requests, "error_count": errors},
        "quality": {"status": status, "coverage_ratio": coverage, "eligible_for_inference": status == "valid", "reasons": reason},
        "provenance": {"source_refs": ["artifact://raw/" + stable_id(capture) + ".json"]}}
    result["record_id"] = stable_id({k: v for k, v in result.items() if k not in ("available_at", "ingested_at")})
    validate_payload("observation-envelope.schema.json", result)
    return result


def process_captures(captures, store, *, environment):
    from sentinelops.telemetry.pipeline import _process_captures
    captures = list(captures)
    catalogs = {}
    for capture in captures:
        try:
            catalog = capture["catalog"]
            _catalog(catalog)
        except (KeyError, ValueError, TypeError):
            continue  # The decoder retains malformed captures in quarantine.
        service, digest = catalog["service"], stable_id(catalog)
        if service in catalogs and catalogs[service] != digest:
            raise ValueError("one manifest cannot mix CloudWatch catalogs for a service")
        catalogs[service] = digest
    return _process_captures(captures, store, environment=environment,
                             decoder=decode_capture, default_profile=SOURCE_PROFILE)
