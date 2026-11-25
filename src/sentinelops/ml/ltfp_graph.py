"""Development-only, receipt-bounded LTFP graph inputs shared by replay adapters."""
from datetime import timedelta
import math

from sentinelops.contracts import utc_time
from sentinelops.ml.alibaba_live import relative_feature_values
from sentinelops.telemetry.core import stable_id, timestamp


def build_graph_input(node_ids, observations, *, anchor, as_of, config, topology,
                      source_identity, self_only=False):
    """Build N×5×F values and validity without treating padding as observed data.

    Observations map service IDs and UTC epoch minutes to rt (ms), rate (request
    count), raw_ref and available_at. Topology contains only feature-node edges;
    callers explicitly exclude components without telemetry, including RDS.
    """
    nodes = list(node_ids)
    if not nodes or any(not isinstance(node, str) or not node for node in nodes) or len(set(nodes)) != len(nodes):
        raise ValueError("graph requires unique explicit node IDs")
    if anchor.second or anchor.microsecond or anchor > as_of:
        raise ValueError("graph requires an available minute-aligned anchor")
    if not source_identity or not topology.get("identity"):
        raise ValueError("graph requires source and topology identity")
    observed = utc_time(topology["observed_at"])
    if observed > as_of:
        raise ValueError("topology was unavailable at cutoff")
    edges = sorted(set(tuple(edge) for edge in topology["directed_edges"]))
    if any(len(edge) != 2 or any(node not in nodes for node in edge) for edge in edges):
        raise ValueError("topology edge has an unknown endpoint")
    topology_record = {"identity": topology["identity"], "observed_at": timestamp(observed),
                       "node_ids": sorted(nodes), "directed_edges": [list(edge) for edge in edges]}
    steps = [int((anchor - timedelta(minutes=offset)).timestamp()) // 60 for offset in range(4, -1, -1)]
    values, validity, raw_refs = [], [], []
    for node in nodes:
        minutes = {}
        for minute, row in observations.get(node, {}).items():
            if minute <= steps[-1] and utc_time(row["available_at"]) <= as_of:
                if all(type(row[key]) in (int, float) and math.isfinite(row[key]) and row[key] > 0
                       for key in ("rt", "rate")):
                    minutes[minute] = row
        node_values, node_refs, node_valid = [], [], []
        for step in steps:
            first = step - config["window_minutes"] - config["baseline"]["max_minutes"] + 1
            selected = {minute: row for minute, row in minutes.items() if first <= minute <= step}
            vector = relative_feature_values({minute: (row["rt"], row["rate"])
                                              for minute, row in selected.items()}, step, config)
            node_valid.append(vector is not None)
            node_values.append(vector if vector is not None else [0.0] * len(config["features"]))
            node_refs.append([selected[minute]["raw_ref"] for minute in sorted(selected)])
        values.append(node_values)
        validity.append(node_valid)
        raw_refs.append(node_refs)
    available = [all(valid) for valid in validity]
    pairs = set(edges) | {(down, up) for up, down in edges}
    mask = [[int(available[i] and available[j] and (i == j or
             (not self_only and (up, down) in pairs))) for j, down in enumerate(nodes)]
            for i, up in enumerate(nodes)]
    return {"projection_version": "ltfp-graph-input/development-v1", "node_ids": nodes,
            "step_minutes": steps, "as_of": timestamp(as_of), "source_identity": source_identity,
            "feature_names": list(config["features"]), "config_sha256": stable_id(config),
            "units": {"rt": "milliseconds", "rate": "request_count"},
            "topology": topology_record, "graph_sha256": stable_id(topology_record),
            "self_only": self_only, "attention_mask": mask,
            "mask_sha256": stable_id({"node_ids": nodes, "mask": mask}),
            "values": values, "validity": validity, "target_available": available,
            "missing_reasons": [None if ready else "incomplete-feature-sequence" for ready in available],
            "raw_refs": raw_refs}
