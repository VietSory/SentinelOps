"""Consume one immutable health replay scope; never join separate runs."""
from pathlib import Path
from datetime import timedelta
import re
import subprocess

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.incidents.correlation import correlate_health
from sentinelops.incidents.health import validate_policy
from sentinelops.telemetry.core import BUSINESS_ROUTES, stable_id
from sentinelops.telemetry.store import ArtifactStore


def replay_correlation(source: ArtifactStore, health_report_ref: str, topology: dict, output: Path,
                       *, project: Path | None = None) -> dict:
    commit = None
    if project is not None:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=project, text=True).strip():
            raise ValueError("commit correlation implementation before source execution")
    match = re.fullmatch(r"artifact://manifests/([0-9a-f]{64})\.json", health_report_ref)
    if not match:
        raise ValueError("correlation needs a content-addressed health report scope")
    health = source.read(health_report_ref)
    if (health.get("report_version") != "slo-health-replay/v1" or health.get("mode") != "availability-aware-replay"
        or health.get("raw_normalization_parity") is not True
        or health.get("model_outputs_available") is not False or health.get("actions_dispatched") is not False
        or health.get("historical_emission_times_verified") is not False):
        raise ValueError("unsupported/unverified upstream health replay")
    validate_policy(health["policy"])
    if stable_id(health["policy"]) != health["policy_sha256"]:
        raise ValueError("upstream health policy identity differs")
    refs = health["signal_refs"]
    if (type(health["bucket_count"]) is not int or not 1 <= health["bucket_count"] <= 10000 or len(set(refs)) != len(refs)
        or len(refs) != health["signal_count"] or len(refs) != len(BUSINESS_ROUTES) * health["bucket_count"]):
        raise ValueError("upstream health signal scope is incomplete/duplicate")
    signals, last = [], {}
    for reference in refs:
        signal = source.read(reference)
        validate_payload("health-signal.schema.json", signal)
        if signal["policy_sha256"] != health["policy_sha256"]:
            raise ValueError("signal policy differs from upstream health report")
        service = signal["service"]
        if service not in last or utc_time(signal["bucket_end"]) > utc_time(last[service]["bucket_end"]):
            last[service] = signal
        signals.append(signal)
    if last != health["final_states"]:
        raise ValueError("upstream final states differ from immutable signals")
    ends = set()
    for service in BUSINESS_ROUTES:
        sequence = sorted((s for s in signals if s["service"] == service), key=lambda s: utc_time(s["bucket_end"]))
        if len(sequence) != health["bucket_count"]:
            raise ValueError("upstream service sequence is incomplete")
        previous = None
        for signal in sequence:
            end = utc_time(signal["bucket_end"])
            if (signal["previous_signal_id"] != (previous["signal_id"] if previous else None)
                or (previous and end - utc_time(previous["bucket_end"]) != timedelta(seconds=60))
                or utc_time(signal["evaluated_at"]) - end != timedelta(seconds=health["policy"]["maximum_lag_seconds"])):
                raise ValueError("upstream health replay chain/cutoff is inconsistent")
            previous = signal
            ends.add(end)
    if len(ends) != health["bucket_count"]:
        raise ValueError("upstream services have different replay bounds")
    result = correlate_health(signals, topology, as_of=max(utc_time(s["evaluated_at"]) for s in signals),
                              source_scope_sha256=match[1])
    output.mkdir(parents=True, exist_ok=False)
    report = {"report_version": "slo-correlation-replay/v1", "source_commit": commit,
        "source_health_report_ref": health_report_ref, "input_signal_refs": refs, "correlation": result,
        "historical_emissions_verified": False, "rca_evaluated": False, "actions_allowed": False}
    report["report_ref"] = ArtifactStore(output).write("manifests", report)
    return report
