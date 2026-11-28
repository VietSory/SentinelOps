from collections import Counter
import json
import math
from pathlib import Path

import pytest

from scripts.generate_adsketch_parity_fixture import expand_input
from sentinelops.ml.model_benchmark_detector import DetectorConfig, DetectorRow, fit_detectors, predict_detectors


FIXTURES = Path(__file__).parent / "fixtures/adsketch_parity"


def _rows(service, split, values, labels, config):
    return [
        DetectorRow("adsketch-parity", service, split, index * config.bucket_seconds, value, label)
        for index, (value, label) in enumerate(zip(values, labels, strict=True))
    ]


def test_benchmark_fit_quantifies_adsketch_reference_differences():
    """Pin the bounded benchmark variant's divergence from the author implementation."""
    inputs = json.loads((FIXTURES / "input.json").read_text())
    author = json.loads((FIXTURES / "author.json").read_text())
    config = DetectorConfig(bucket_seconds=60, window_length=5)
    for field in ("bucket_seconds", "window_length", "seed", "discovery_quantile", "max_fit_windows",
                  "normal_patterns", "anomalous_patterns"):
        assert getattr(config, field) == inputs[field]
    normal, discovery, labels = expand_input(inputs)
    assert min(len(normal), len(discovery)) - config.window_length + 1 > config.max_fit_windows

    model = fit_detectors(
        _rows("reference", "train", normal, [0] * len(normal), config), config,
        discovery_rows=_rows("discovery", "train", discovery, labels, config),
    )
    assert model.scaler == (9.0, 6.0)
    assert Counter(pattern.kind for pattern in model.patterns) == {"normal": 1, "anomalous": 2}
    author_normal, = author["patterns"]
    assert author_normal["kind"] == "normal"
    benchmark_normal, = (pattern for pattern in model.patterns if pattern.kind == "normal")
    assert benchmark_normal.count == config.max_fit_windows
    assert author_normal["count"] == 1132
    center_error = math.dist(benchmark_normal.center, author_normal["center"])
    radius_error = abs(benchmark_normal.radius - author_normal["radius"])
    assert center_error == pytest.approx(0.9118908396106898, abs=1e-8)
    assert radius_error == pytest.approx(11.204805502245165, abs=1e-8)

    minimum, span = model.scaler
    scaled = [(value - minimum) / span for value in discovery]
    windows = [scaled[start:start + config.window_length]
               for start in range(len(scaled) - config.window_length + 1)]
    nearest = [min(model.patterns, key=lambda pattern: math.dist(window, pattern.center)) for window in windows]
    author_nearest = [author["patterns"][index] for index, count in author["nearest_pattern_runs"]
                      for _ in range(count)]
    assert len(nearest) == len(author_nearest) == 536
    assert sum(pattern.kind == reference["kind"] for pattern, reference in zip(nearest, author_nearest, strict=True)) == 456

    scoring = _rows("discovery", "scoring", discovery, [None] * len(discovery), config)
    predictions = [prediction for prediction in predict_detectors(model, scoring) if prediction.model_id == "adsketch"]
    warmup = config.window_length - 1
    assert [prediction.reason for prediction in predictions[:warmup]] == ["insufficient_history"] * warmup
    assert Counter(prediction.pattern_kind for prediction in predictions[warmup:]) == {
        "normal": 376, "known": 20, "novel": 140,
    }
    for prediction, window, pattern in zip(predictions[warmup:], windows, nearest, strict=True):
        assert prediction.status == "available"
        within_radius = math.dist(window, pattern.center) <= max(config.scale_floor, pattern.radius)
        assert prediction.pattern_id == (pattern.pattern_id if within_radius else None)
