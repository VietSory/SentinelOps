"""ADR-019 synthetic fault-shape gate: it must reject models that cannot respond to the injected faults."""
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("pandas")

from sentinelops.ml.alibaba_training import load_config
from sentinelops.ml.fault_shapes import fault_shape_gate, sequences
from sentinelops.ml.local_evaluation import load_protocol


class Fixed:
    """Stand-in detector/risk with a chosen response to latency level (feature 0)."""
    def __init__(self, respond):
        self.respond = respond
        self.thresholds = {'isolation_forest': .5, 'median_mad': .5}

    def predict(self, values):
        level = np.asarray(values)[:, 0]
        flags = self.respond(level)
        score = np.where(flags, 1., 0.)
        return {name: {'alert': flags, 'raw_score': score, 'score': score} for name in ('isolation_forest', 'median_mad')}

    def predict_proba(self, values):
        array = np.asarray(values)
        p = 1 / (1 + np.exp(-4 * (array[:, 0] + array[:, 5] + 20 * np.maximum(array[:, 1], 0) * np.exp(array[:, 5]))))
        return np.column_stack([1 - p, p])


def test_sequences_are_deterministic_and_breach_only_in_faults():
    spec = load_config()['fault_shape_gate']
    a, b = sequences(spec, 750.), sequences(spec, 750.)
    assert len(a) == 3 * len(spec['base_latency_ms']) * len(spec['noise_sigma']) * spec['seeds_per_cell']
    assert all(np.array_equal(x['rt'], y['rt']) for x, y in zip(a, b))
    assert all((s['onset'] is None) == (s['kind'] == 'healthy') for s in a)


def test_saturated_detector_fails_and_responsive_one_passes_detector_checks():
    config, protocol = load_config(), load_protocol()
    silent = fault_shape_gate(Fixed(lambda level: level > 50.), Fixed(None), config, protocol, .9)
    assert silent['checks']['detector_event_recall'] is False and silent['passed'] is False
    noisy = fault_shape_gate(Fixed(lambda level: level > -1.), Fixed(None), config, protocol, .9)
    assert noisy['checks']['detector_false_alerts'] is False
    good = fault_shape_gate(Fixed(lambda level: level > 2.), Fixed(None), config, protocol, .9)
    assert good['checks']['detector_event_recall'] and good['checks']['detector_false_alerts']
    assert good['detector_events']['isolation_forest']['tp'] == good['sequences'] // 3
