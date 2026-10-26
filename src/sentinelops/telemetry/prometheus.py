"""Pinned metric catalog and arrival-aware source captures for local history."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from typing import Any

import httpx
import yaml

from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import BUSINESS_ROUTES, BucketMetrics, normalize_bucket, stable_id, timestamp

SOURCE_PROFILE = "prometheus-increase-60s-5s/v1"
SOURCE_INTERVALS = {"prometheus-increase-60s/v1": 15, SOURCE_PROFILE: 5}
REQUESTS = "sentinelops_http_requests_total"
DURATION = "sentinelops_http_request_duration_ms_milliseconds_bucket"


def queries(service: str) -> dict[str, str]:
    if service not in BUSINESS_ROUTES:
        raise ValueError("unregistered business service")
    method, route = BUSINESS_ROUTES[service]
    selector = ",".join(f"{key}={json.dumps(value)}" for key, value in (
        ("service", service), ("method", method), ("route", route),
    ))
    return {
        "requests": f"sum(increase({REQUESTS}{{{selector}}}[60s]))",
        "errors": f'sum(increase({REQUESTS}{{{selector},status_code=~"5.."}}[60s])) or vector(0)',
        "buckets": f"sum by (le) (increase({DURATION}{{{selector}}}[60s]))",
        "scrapes": 'min(count_over_time(up{job="otel-collector"}[60s]))',
        "up": 'min(min_over_time(up{job="otel-collector"}[60s]))',
        "series_scrapes": f"min(count_over_time({REQUESTS}{{{selector}}}[60s]))",
    }


def _vector(response: dict[str, Any], end: datetime) -> list[tuple[dict[str, str], float]]:
    if response.get("status") != "success" or response.get("warnings"):
        raise ValueError("Prometheus query failed or returned warnings")
    data = response.get("data", {})
    if data.get("resultType") != "vector":
        raise ValueError("expected a Prometheus instant vector")
    result = []
    for series in data.get("result", []):
        sample_time, encoded = series["value"]
        number = float(encoded)
        if not math.isfinite(number) or number < 0:
            raise ValueError("non-finite or negative Prometheus sample")
        if float(sample_time) > end.timestamp():
            raise ValueError("Prometheus returned a future sample")
        result.append((series.get("metric", {}), number))
    return result


def decode_capture(capture: dict[str, Any], *, environment: str = "local") -> dict[str, Any]:
    profile = capture.get("source_profile")
    if profile not in SOURCE_INTERVALS:
        raise ValueError("unsupported source profile")
    end = utc_time(capture["bucket_end"])
    expected_queries = queries(capture["service"])
    responses = capture["queries"]
    if set(responses) != set(expected_queries):
        raise ValueError("incomplete source capture")
    decoded = {}
    for key, expression in expected_queries.items():
        if responses[key]["expression"] != expression:
            raise ValueError("capture query differs from pinned metric catalog")
        decoded[key] = _vector(responses[key]["response"], end)

    def scalar(key):
        values = decoded[key]
        if len(values) > 1:
            raise ValueError(f"expected one aggregate sample: {key}")
        return values[0][1] if values else None

    scrapes, series_scrapes, up = scalar("scrapes"), scalar("series_scrapes"), scalar("up")
    expected = 60 / SOURCE_INTERVALS[profile]
    coverage = min(1., (scrapes or 0.) / expected, (series_scrapes or 0.) / expected) if up == 1 else 0.
    buckets = tuple(sorted((float(labels["le"]), value) for labels, value in decoded["buckets"]))
    source_ref = "artifact://raw/" + stable_id(capture) + ".json"
    raw = BucketMetrics(
        capture["service"], end, scalar("requests"), scalar("errors"), buckets or None,
        coverage, utc_time(capture["collected_at"]), (source_ref,), profile,
    )
    return normalize_bucket(raw, environment=environment)


class PrometheusSource:
    def __init__(self, endpoint: str = "http://127.0.0.1:9090", *, transport=None):
        self.endpoint = endpoint.rstrip("/")
        self.client = httpx.Client(timeout=10., trust_env=False, transport=transport)

    def close(self) -> None:
        self.client.close()

    def verify_configuration(self) -> dict[str, str]:
        response = self.client.get(self.endpoint+"/api/v1/status/config")
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "success":
            raise ValueError("Prometheus configuration is unavailable")
        config = yaml.safe_load(body["data"]["yaml"])
        jobs = [job for job in config.get("scrape_configs",[]) if job.get("job_name") == "otel-collector"]
        interval = jobs[0].get("scrape_interval",config.get("global",{}).get("scrape_interval")) if len(jobs)==1 else None
        if interval != "5s":
            raise ValueError("Prometheus scrape configuration does not match the 5s source profile")
        return {"scrape_interval":interval,"configuration_hash":stable_id(config)}

    def capture(self, service: str, end: datetime, *, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        timestamp(end)
        timestamp(current)
        if end.timestamp() % 60 or current < end + timedelta(seconds=30):
            raise ValueError("bucket is unaligned or not past the source watermark")
        responses = {}
        for key, expression in queries(service).items():
            try:
                response = self.client.get(self.endpoint + "/api/v1/query", params={"query": expression, "time": end.timestamp()})
                response.raise_for_status()
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                # Preserve a failed/partial capture for quarantine; never replace
                # an outage with a zero-valued successful observation.
                responses[key] = {"expression": expression, "response": {
                    "status": "error", "errorType": type(exc).__name__,
                    "error": "source query unavailable or invalid JSON",
                }}
                break
            responses[key] = {"expression": expression, "response": body}
        return {
            "capture_version": "1.0", "source_profile": SOURCE_PROFILE,
            "service": service, "bucket_end": timestamp(end),
            "collected_at": timestamp(current if now is not None else datetime.now(timezone.utc)),
            "queries": responses,
        }
