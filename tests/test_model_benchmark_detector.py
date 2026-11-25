from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
import json
import math

import pytest

from sentinelops.ml.local_evaluation import persistent_states
from sentinelops.ml.model_benchmark_detector import (
    MODEL_IDS,
    DetectorConfig,
    DetectorRow,
    FrozenDetectors,
    compare_offline_sketch,
    fit_detectors,
    predict_detectors,
)


BASELINE = (10.0, 12.0, 15.0, 12.0, 10.0, 9.0)
LEARNED = (30.0, 58.0, 65.0, 32.0)
NOVEL = (300.0, 400.0, 500.0, 600.0, 700.0, 800.0)


def _rows(service, values, *, start=0, run_id="alibaba-train", split="train", positives=()):
    return [DetectorRow(run_id, service, split, start + 60 * index, value, int(index in positives))
            for index, value in enumerate(values)]


@pytest.fixture(scope="module")
def training():
    config = DetectorConfig(60, 5, seed=1234, forest_trees=4, forest_sample_size=16, discovery_quantile=.8)
    reference = (_rows("reference-a", BASELINE * 4)
                 + _rows("reference-a", (11.0, 12.0, 13.0), start=1560)
                 + _rows("reference-b", BASELINE * 2))
    discovery = _rows("discovery-a", BASELINE * 4 + LEARNED + BASELINE * 3, positives=range(24, 28))
    return config, reference, discovery


@pytest.fixture(scope="module")
def frozen(training):
    config, reference, discovery = training
    return fit_detectors(reference, config, discovery_rows=discovery)


def test_b01_matches_recorded_adsketch_author_fixture():
    """Compare with b01-author.json from OpsPAI/ADSketch at f7bd642c93ff25612514ed711f47cc095a6a66fd."""
    discovery = list(BASELINE * 9)
    discovery[17:21] = LEARNED
    discovery[41:45] = (30.0, 57.0, 66.0, 31.0)
    inputs = dict(normal=BASELINE * 10, discovery=discovery, window_length=5, percentile=80, seed=1234)
    recorded = dict(
        anomalous_window_starts=[15, 16, 17, 18, 19, 39, 40, 41, 42, 43],
        nearest_pattern_by_start=[0] * 14 + [1] * 3 + [2] * 3 + [0] * 18 + [1] * 3 + [2] * 3 + [0] * 6,
        cluster_sizes=[96, 4, 6],
        anomalous_cluster_count=2,
        radii=[8.170978126018918, 4.138446098611303, 6.513874081954269],
    )
    actual = compare_offline_sketch(**inputs)

    assert actual == compare_offline_sketch(**inputs)
    assert actual == dict(recorded, radii=pytest.approx(recorded["radii"], rel=0, abs=1e-12))


def test_b02_fits_only_train_segments_and_reference_scaler(training, frozen):
    config, reference, discovery = training
    assert frozen.scaler == (9.0, 6.0)
    assert [(segment.run_id, segment.split, segment.service, segment.start_timestamp, segment.end_timestamp,
             segment.rows, segment.normal_windows, segment.anomalous_windows)
            for segment in frozen.training_segments] == [
        ("alibaba-train", "train", "reference-a", 0, 1380, 24, 20, 0),
        ("alibaba-train", "train", "reference-a", 1560, 1680, 3, 0, 0),
        ("alibaba-train", "train", "reference-b", 0, 660, 12, 8, 0),
        ("alibaba-train", "train", "discovery-a", 0, 2700, 46, 0, 8),
    ]
    assert frozen.support["normal_segments"] == 2
    assert frozen.support["anomalous_segments"] == 1
    assert {pattern.kind for pattern in frozen.patterns if pattern.count > 0} == {"normal", "anomalous"}
    assert fit_detectors(reversed(reference), config, discovery_rows=reversed(discovery)).digest == frozen.digest
    assert FrozenDetectors.from_dict(json.loads(json.dumps(frozen.to_dict()))) == frozen

    for fit_reference, fit_discovery in (
        ([*reference[:-1], replace(reference[-1], split="test")], discovery),
        (reference, [*discovery[:-1], replace(discovery[-1], split="test")]),
    ):
        with pytest.raises(ValueError, match="requires Alibaba train rows"):
            fit_detectors(fit_reference, config, discovery_rows=fit_discovery)
    with pytest.raises(ValueError, match="disjoint services"):
        fit_detectors(reference, config, discovery_rows=[*discovery, reference[-1]])


def test_b03_predictions_are_prefix_causal_and_ignore_test_labels(frozen):
    prefix = _rows("holdout", NOVEL, run_id="holdout", split="test")
    cutoff = prefix[-1].timestamp
    suffix = _rows("holdout", BASELINE, start=cutoff + 60, run_id="holdout", split="test")
    changed = [replace(row, label=1 - row.label) for row in prefix]
    changed += [replace(row, label=1 - row.label, value=-row.value) for row in suffix]
    artifact = frozen.to_dict()
    original = predict_detectors(frozen, prefix + suffix)
    updated = predict_detectors(frozen, reversed(changed))
    prefix_predictions = predict_detectors(frozen, prefix)

    assert tuple(row for row in original if row.timestamp <= cutoff) == prefix_predictions
    assert tuple(row for row in updated if row.timestamp <= cutoff) == prefix_predictions
    assert original != updated
    assert any(row.timestamp == cutoff and row.pattern_kind == "novel" for row in updated)
    assert frozen.to_dict() == artifact


def test_b05_unavailable_reasons_and_alerts_share_model_timeline(frozen):
    rows = _rows("holdout", (*BASELINE[:5], None, *BASELINE[:5]), run_id="holdout", split="test")
    rows += _rows("holdout", BASELINE[:5], start=720, run_id="holdout", split="test")
    rows += _rows("holdout", (math.inf,), start=1020, run_id="holdout", split="test")
    expected = ["insufficient_history"] * 4 + [None] + ["missing_bucket"] * 5 + [None]
    expected += ["missing_bucket"] * 4 + [None, "invalid_value"]
    damaged = dict(frozen.to_dict(), scaler={"minimum": 0.0, "span": 0.0})

    for model, reasons in ((frozen, expected), (damaged, ["invalid_model"] * len(rows))):
        predictions = predict_detectors(model, rows)
        assert len(predictions) == len(rows) * len(MODEL_IDS)
        for model_id in MODEL_IDS:
            timeline = [row for row in predictions if row.model_id == model_id]
            assert [row.timestamp for row in timeline] == [row.timestamp for row in rows]
            assert [row.reason for row in timeline] == reasons
            flags = []
            for row, reason in zip(timeline, reasons):
                if reason is None:
                    assert row.status == "available" and math.isfinite(row.score)
                else:
                    assert row.status == "unavailable"
                    assert row.score is row.pattern_kind is row.pattern_id is None
                anchor = datetime.fromtimestamp(row.timestamp, UTC).isoformat()
                flags.append(dict(window_end=anchor, decision_at=anchor,
                                  flag=None if row.score is None else row.score >= 0))
            states = persistent_states(flags, "flag", {"step_seconds": 60, "entry_windows": 1, "exit_windows": 1})
            assert [state["available"] for state in states] == [reason is None for reason in reasons]
            assert all(not state["entered"] for state, reason in zip(states, reasons) if reason is not None)
            assert any(state["entered"] for state in states) == (model is frozen)


def test_b06_separates_known_and_novel_runs_against_one_frozen_library(frozen):
    artifact = frozen.to_dict()
    known = _rows("holdout", (10.0, *LEARNED, 10.0), run_id="known", split="test")
    novel = _rows("holdout", NOVEL, run_id="novel", split="test")
    predictions = predict_detectors(artifact, novel + known)
    available = [row for row in predictions if row.status == "available" and row.model_id == "adsketch"]

    counts = Counter((row.run_id, row.pattern_kind) for row in available)
    assert counts == {("known", "known"): 2, ("novel", "novel"): 2}
    learned_ids = {pattern.pattern_id for pattern in frozen.patterns if pattern.kind == "anomalous"}
    assert all(row.pattern_id in learned_ids for row in available if row.run_id == "known")
    assert all(row.pattern_id is None for row in available if row.run_id == "novel")
    assert artifact == frozen.to_dict()
