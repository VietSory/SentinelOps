"""Frozen benchmark detectors on a shared, right-aligned scalar window.

The ADSketch approximation follows Chen et al. (ICSE 2022,
doi:10.1145/3510003.3510085). It uses reference-fitted min-max scaling,
nearest-subsequence Euclidean distances, discovery percentiles, similarity
components, and Affinity Propagation with mean patterns and radii. Direct
self/cross profiles over deterministic bounded samples replace STAMP. The
benchmark requires label-confirmed discovery candidates and learns its normal
patterns from the designated reference. Radius tests report novel windows
against the frozen library.
"""

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from hashlib import sha256
from itertools import groupby
import json
import math
import random
from statistics import median
from time import perf_counter
from types import MappingProxyType


MODEL_IDS = ("adsketch", "isolation_forest", "median_mad")
_SCHEMA_VERSION = 1
_METHOD = "adsketch-approximation-direct-profile-v1"
_Vector = tuple[float, ...]


@dataclass(frozen=True, slots=True)
class DetectorRow:
    run_id: str
    service: str
    split: str
    timestamp: int
    value: float | None
    label: int | None = None


@dataclass(frozen=True, slots=True)
class DetectorConfig:
    bucket_seconds: int
    window_length: int
    seed: int = 42
    normal_patterns: int = 8
    anomalous_patterns: int = 8
    max_fit_windows: int = 512
    forest_trees: int = 64
    forest_sample_size: int = 128
    discovery_quantile: float = 0.95
    scale_floor: float = 1e-6

    def __post_init__(self):
        bounds = (self.bucket_seconds, self.window_length, self.normal_patterns,
                  self.anomalous_patterns, self.max_fit_windows, self.forest_trees,
                  self.forest_sample_size)
        if any(type(value) is not int or value < 1 for value in bounds):
            raise ValueError("detector configuration requires positive integer sizes")
        if min(self.max_fit_windows, self.forest_sample_size) < 2 or type(self.seed) is not int:
            raise ValueError("isolation forest requires at least two samples and an integer seed")
        if not _finite(self.discovery_quantile) or not 0 <= self.discovery_quantile < 1:
            raise ValueError("discovery_quantile must be in [0, 1)")
        if not _finite(self.scale_floor) or self.scale_floor <= 0:
            raise ValueError("scale_floor must be positive and finite")


@dataclass(frozen=True, slots=True)
class DetectorPrediction:
    run_id: str
    service: str
    timestamp: int
    model_id: str
    score: float | None
    status: str
    reason: str | None = None
    pattern_kind: str | None = None
    pattern_id: str | None = None
    inference_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class _TrainingSegment:
    role: str
    run_id: str
    service: str
    split: str
    start_timestamp: int
    end_timestamp: int
    rows: int
    normal_windows: int
    anomalous_windows: int


@dataclass(frozen=True, slots=True)
class _Pattern:
    pattern_id: str
    kind: str
    center: _Vector
    radius: float
    count: int


@dataclass(frozen=True, slots=True)
class _Window:
    key: tuple[str, str, int]
    values: _Vector
    positive: bool = False


@dataclass(frozen=True, slots=True)
class _Tree:
    size: int
    feature: int | None = None
    threshold: float | None = None
    left: "_Tree | None" = None
    right: "_Tree | None" = None


@dataclass(frozen=True, slots=True)
class FrozenDetectors:
    config: DetectorConfig
    training_segments: tuple[_TrainingSegment, ...]
    scaler: tuple[float, float]
    discovery_threshold: float
    patterns: tuple[_Pattern, ...]
    forest_sample_count: int
    forest: tuple[_Tree, ...]
    median_center: _Vector
    mad_scale: _Vector
    digest: str

    @property
    def support(self) -> Mapping[str, int]:
        return MappingProxyType({
            "reference_segments": sum(segment.role == "reference" for segment in self.training_segments),
            "discovery_segments": sum(segment.role == "discovery" for segment in self.training_segments),
            "normal_segments": sum(segment.normal_windows > 0 for segment in self.training_segments),
            "anomalous_segments": sum(segment.anomalous_windows > 0 for segment in self.training_segments),
            "normal_windows": sum(segment.normal_windows for segment in self.training_segments),
            "anomalous_windows": sum(segment.anomalous_windows for segment in self.training_segments),
            "normal_pattern_windows": sum(pattern.count for pattern in self.patterns if pattern.kind == "normal"),
            "anomalous_pattern_windows": sum(pattern.count for pattern in self.patterns if pattern.kind == "anomalous"),
        })

    def _body(self) -> dict:
        return {
            "schema_version": _SCHEMA_VERSION,
            "method": _METHOD,
            "config": asdict(self.config),
            "support": dict(self.support),
            "training_segments": [asdict(segment) for segment in self.training_segments],
            "scaler": dict(minimum=self.scaler[0], span=self.scaler[1]),
            "discovery_threshold": self.discovery_threshold,
            "patterns": [asdict(pattern) for pattern in self.patterns],
            "forest_sample_count": self.forest_sample_count,
            "forest": [asdict(tree) for tree in self.forest],
            "median_center": self.median_center,
            "mad_scale": self.mad_scale,
        }

    def to_dict(self) -> dict:
        """Export detached JSON data, including the SHA-256 of canonical content."""
        return json.loads(_canonical(dict(self._body(), digest=self.digest)))

    @classmethod
    def from_dict(cls, payload: Mapping) -> "FrozenDetectors":
        """Check the complete artifact before loading any inference state."""
        try:
            body = dict(payload)
            digest = body.pop("digest")
            if digest != _digest(body):
                raise ValueError("detector artifact digest mismatch")
            config = DetectorConfig(**body["config"])
            sample_count = body["forest_sample_count"]
            if type(sample_count) is not int or not 2 <= sample_count <= config.forest_sample_size:
                raise ValueError("invalid isolation forest sample count")
            depth = math.ceil(math.log2(sample_count))
            model = cls(
                config=config,
                training_segments=tuple(_TrainingSegment(**item) for item in body["training_segments"]),
                scaler=(body["scaler"]["minimum"], body["scaler"]["span"]),
                discovery_threshold=body["discovery_threshold"],
                patterns=tuple(_Pattern(**dict(item, center=tuple(item["center"]))) for item in body["patterns"]),
                forest_sample_count=sample_count,
                forest=tuple(_load_tree(item, config.window_length, depth) for item in body["forest"]),
                median_center=tuple(body["median_center"]),
                mad_scale=tuple(body["mad_scale"]),
                digest=digest,
            )
            model._validate()
            if _digest(model._body()) != digest:
                raise ValueError("invalid detector artifact schema or support counts")
        except (TypeError, KeyError, OverflowError, RecursionError) as error:
            raise ValueError("invalid detector artifact") from error
        return model

    def _validate(self):
        width = self.config.window_length
        if (not all(map(_finite, self.scaler)) or self.scaler[1] < self.config.scale_floor
                or not _nonnegative(self.discovery_threshold)):
            raise ValueError("invalid frozen scaler or discovery threshold")
        if (not _valid_vector(self.median_center, width) or not _valid_vector(self.mad_scale, width)
                or min(self.mad_scale) < self.config.scale_floor):
            raise ValueError("invalid frozen median/MAD")
        if (len(self.forest) != self.config.forest_trees
                or any(tree.size != self.forest_sample_count for tree in self.forest)):
            raise ValueError("invalid frozen isolation forest")
        for kind, maximum in (("normal", self.config.normal_patterns), ("anomalous", self.config.anomalous_patterns)):
            count = sum(pattern.kind == kind for pattern in self.patterns)
            if not 1 <= count <= maximum:
                raise ValueError(f"invalid frozen {kind} patterns")
        pattern_ids = set()
        for pattern in self.patterns:
            if (pattern.kind not in {"normal", "anomalous"} or not _valid_vector(pattern.center, width)
                    or not _nonnegative(pattern.radius) or type(pattern.count) is not int or pattern.count < 1
                    or pattern.pattern_id != _pattern_id(pattern.kind, pattern.center)
                    or pattern.pattern_id in pattern_ids):
                raise ValueError("invalid frozen pattern")
            pattern_ids.add(pattern.pattern_id)
        services = {"reference": set(), "discovery": set()}
        last_end = {}
        for segment in self.training_segments:
            if (segment.role not in services or segment.split != "train"
                    or not _identity(segment.run_id) or not _identity(segment.service)
                    or any(type(value) is not int for value in (segment.start_timestamp, segment.end_timestamp,
                                                               segment.rows, segment.normal_windows,
                                                               segment.anomalous_windows))
                    or segment.rows < 1 or segment.normal_windows < 0 or segment.anomalous_windows < 0
                    or segment.normal_windows + segment.anomalous_windows > max(0, segment.rows - width + 1)
                    or segment.end_timestamp - segment.start_timestamp != (segment.rows - 1) * self.config.bucket_seconds
                    or (segment.role == "reference" and segment.anomalous_windows)
                    or (segment.role == "discovery" and segment.normal_windows)):
                raise ValueError("invalid training segment")
            key = (segment.run_id, segment.service)
            if key in last_end and segment.start_timestamp - last_end[key] <= self.config.bucket_seconds:
                raise ValueError("training segments overlap or lack a gap")
            last_end[key] = segment.end_timestamp
            services[segment.role].add(segment.service)
        support = self.support
        if (services["reference"] & services["discovery"]
                or support["normal_pattern_windows"] != min(support["normal_windows"], self.config.max_fit_windows)
                or support["anomalous_pattern_windows"] > min(support["anomalous_windows"], self.config.max_fit_windows)
                or self.forest_sample_count != min(support["normal_pattern_windows"], self.config.forest_sample_size)):
            raise ValueError("invalid frozen training support")


def _finite(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _nonnegative(value) -> bool:
    return _finite(value) and value >= 0


def _valid_vector(values, width) -> bool:
    return len(values) == width and all(map(_finite, values))


def _identity(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _canonical(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(payload) -> str:
    return sha256(_canonical(payload)).hexdigest()


def _pattern_id(kind, center) -> str:
    return f"{kind}-{_digest(center)}"


def _ordered_rows(rows: Iterable[DetectorRow]) -> list[DetectorRow]:
    rows = list(rows)
    for row in rows:
        if (not isinstance(row, DetectorRow) or not all(map(_identity, (row.run_id, row.service, row.split)))
                or type(row.timestamp) is not int):
            raise ValueError("detector rows require run, service, split and integer timestamp")
    return sorted(rows, key=lambda row: (row.run_id, row.service, row.timestamp))


def _timeline(rows, config):
    """Yield observed rows, causal history and any current window failure."""
    for _, series in groupby(rows, key=lambda row: (row.run_id, row.service)):
        history = deque(maxlen=config.window_length)
        previous = None
        interrupted = None
        for row in series:
            if previous is not None:
                step = row.timestamp - previous.timestamp
                if step == 0:
                    raise ValueError("duplicate detector bucket for run and service")
                if row.split != previous.split:
                    history.clear()
                    interrupted = None
                elif step != config.bucket_seconds:
                    history.clear()
                    interrupted = "missing_bucket"
            previous = row
            if row.value is None or not _finite(row.value):
                history.clear()
                interrupted = "missing_bucket" if row.value is None else "invalid_value"
                yield row, None, interrupted
                continue
            history.append(row)
            if len(history) == config.window_length:
                interrupted = None
                yield row, tuple(history), None
            else:
                yield row, None, interrupted or "insufficient_history"


def _training_windows(rows, config, role):
    segments, normal, discovery, anomalous = [], [], [], []
    previous = None
    segment_start = None
    segment_rows = normal_count = anomalous_count = 0
    for row, history, _ in _timeline(rows, config):
        if segment_start is not None and (row.value is None or not _finite(row.value)
                or (row.run_id, row.service) != (previous.run_id, previous.service)
                or row.timestamp - previous.timestamp != config.bucket_seconds):
            segments.append(_TrainingSegment(role, segment_start.run_id, segment_start.service, "train",
                                             segment_start.timestamp, previous.timestamp, segment_rows,
                                             normal_count, anomalous_count))
            segment_start = None
            segment_rows = normal_count = anomalous_count = 0
        if row.value is None or not _finite(row.value):
            continue
        previous = row
        segment_start = segment_start or row
        segment_rows += 1
        if history is None or any(item.label is None for item in history):
            continue
        window = _Window((row.run_id, row.service, row.timestamp), tuple(float(item.value) for item in history),
                         any(item.label == 1 for item in history))
        if role == "reference" and not window.positive:
            normal.append(window)
            normal_count += 1
        elif role == "discovery":
            discovery.append(window)
            if window.positive:
                anomalous.append(window)
                anomalous_count += 1
    if segment_start is not None:
        segments.append(_TrainingSegment(role, segment_start.run_id, segment_start.service, "train",
                                         segment_start.timestamp, previous.timestamp, segment_rows,
                                         normal_count, anomalous_count))
    return tuple(segments), normal, discovery, anomalous


def _sample(windows, limit):
    if len(windows) <= limit:
        return windows
    last = len(windows) - 1
    return [windows[index * last // (limit - 1)] for index in range(limit)]


def _scale(values, scaler):
    minimum, span = scaler
    return tuple((value - minimum) / span for value in values)


def _fit_scaler(minimum, maximum, floor):
    scaler = minimum, max(floor, maximum - minimum)
    if not all(map(_finite, scaler)):
        raise ValueError("nonfinite reference range")
    return scaler


def _mean(windows):
    return tuple(math.fsum(value / len(column) for value in column) for column in zip(*windows))


def _quantile(values, quantile):
    ordered = sorted(values)
    index = quantile * (len(ordered) - 1)
    lower, upper = ordered[math.floor(index)], ordered[math.ceil(index)]
    return lower + (upper - lower) * (index % 1)


def _nearest(windows, target, exclusion=None):
    candidates = ((math.dist(window.values, target.values), index) for index, window in enumerate(windows)
                  if exclusion is None or window.key[:2] != target.key[:2]
                  or abs(window.key[2] - target.key[2]) > exclusion)
    return min(candidates, default=(math.inf, None))


def _components(reference, discovery, profiles, threshold, exclusion, *, benchmark):
    parents = list(range(len(reference) + len(discovery)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, window in enumerate(reference):
        _, closest = _nearest(reference, window, exclusion)
        if closest is not None:
            parents[root(index)] = root(closest)
    active = list(range(len(reference)))
    candidates = set()
    for index, (window, (distance, closest)) in enumerate(zip(discovery, profiles), start=len(reference)):
        if distance > threshold and (not benchmark or window.positive):
            candidates.add(index)
            active.append(index)
        elif not benchmark:
            parents[index] = root(closest)
            active.append(index)
    components = {}
    for index in active:
        components.setdefault(root(index), []).append(index)
    return tuple(components.values()), candidates


def _affinity_groups(vectors, components, seed):
    from sklearn.cluster import AffinityPropagation

    centers = [_mean([vectors[index] for index in group]) for group in components]
    if len(centers) == 1 or len(set(centers)) == 1:
        return [sorted(index for component in components for index in component)]
    clustering = AffinityPropagation(random_state=seed % (2 ** 32)).fit(centers)
    if any(label < 0 for label in clustering.labels_):
        raise ValueError("ADSketch affinity propagation found no cluster centers")
    groups = {}
    for label, component in zip(clustering.labels_, components):
        groups.setdefault(int(label), []).extend(component)
    return sorted(sorted(group) for group in groups.values())


def _merge_groups(vectors, groups, limit):
    while len(groups) > limit:
        centers = [_mean([vectors[index] for index in group]) for group in groups]
        _, left, right = min((math.dist(centers[left], centers[right]), left, right)
                             for right in range(len(groups)) for left in range(right))
        groups[left].extend(groups.pop(right))
    return groups


def _sketch(reference, discovery, profiles, threshold, config, *, benchmark):
    exclusion = math.ceil(config.window_length / 4) * config.bucket_seconds
    components, candidates = _components(reference, discovery, profiles, threshold, exclusion, benchmark=benchmark)
    vectors = [window.values for window in (*reference, *discovery)]
    groups = _affinity_groups(vectors, components, config.seed)
    by_kind = {"normal": [], "anomalous": []}
    for group in groups:
        kind = "anomalous" if all(index in candidates for index in group) else "normal"
        if benchmark and kind == "normal":
            group = [index for index in group if index < len(reference)]
        if group:
            by_kind[kind].append(group)
    patterns, classifications = {}, {}
    for kind, limit in (("normal", config.normal_patterns), ("anomalous", config.anomalous_patterns)):
        groups = by_kind[kind]
        if benchmark:
            groups = _merge_groups(vectors, groups, limit)
        for group in groups:
            members = [vectors[index] for index in group]
            center = _mean(members)
            radius = max(math.dist(vector, center) for vector in members)
            pattern_id = _pattern_id(kind, center)
            previous = patterns.get(pattern_id)
            if previous is not None:
                radius = max(previous.radius, radius)
            patterns[pattern_id] = _Pattern(pattern_id, kind, center, radius,
                                            len(group) + (previous.count if previous else 0))
            classifications.update((index - len(reference), pattern_id) for index in group if index >= len(reference))
    return tuple(patterns.values()), classifications


def compare_offline_sketch(normal, discovery, *, window_length, percentile, seed):
    """Run the paper-style offline comparison on two raw contiguous series.

    Fit min-max scaling on the normal series. Report discovered anomalous
    start indices separately from nearest-pattern assignments. Number the
    clusters by first nearest assignment, then append any unassigned clusters.
    """
    config = DetectorConfig(1, window_length, seed=seed, discovery_quantile=percentile / 100)

    normal, discovery = tuple(normal), tuple(discovery)
    for series in (normal, discovery):
        if len(series) < window_length or not all(map(_finite, series)):
            raise ValueError("comparison series require finite complete windows")
        if len(series) - window_length + 1 > config.max_fit_windows:
            raise ValueError("comparison fixture exceeds direct profile budget")
    scaler = _fit_scaler(min(normal), max(normal), config.scale_floor)

    def windows(role, values):
        values = _scale(values, scaler)
        if not all(map(_finite, values)):
            raise ValueError("nonfinite scaled comparison series")
        return [_Window((role, role, index), values[index:index + window_length])
                for index in range(len(values) - window_length + 1)]

    reference, targets = windows("reference", normal), windows("discovery", discovery)
    profiles = [_nearest(reference, target) for target in targets]
    threshold = _quantile([distance for distance, _ in profiles], config.discovery_quantile)
    patterns, classifications = _sketch(reference, targets, profiles, threshold, config, benchmark=False)
    anomalous_ids = {pattern.pattern_id for pattern in patterns if pattern.kind == "anomalous"}
    nearest = [min(range(len(patterns)), key=lambda index: math.dist(target.values, patterns[index].center))
               for target in targets]
    order = tuple(dict.fromkeys((*nearest, *range(len(patterns)))))
    canonical_ids = {index: rank for rank, index in enumerate(order)}
    ordered_patterns = [patterns[index] for index in order]
    return dict(
        anomalous_window_starts=sorted(index for index, pattern_id in classifications.items()
                                       if pattern_id in anomalous_ids),
        nearest_pattern_by_start=[canonical_ids[index] for index in nearest],
        cluster_sizes=[pattern.count for pattern in ordered_patterns],
        anomalous_cluster_count=len(anomalous_ids),
        radii=[pattern.radius for pattern in ordered_patterns],
    )


def _grow_tree(windows, depth, rng):
    bounds = [(min(column), max(column)) for column in zip(*windows)]
    eligible = [index for index, (lower, upper) in enumerate(bounds) if lower < upper]
    if depth == 0 or not eligible:
        return _Tree(len(windows))
    feature = rng.choice(eligible)
    lower, upper = bounds[feature]
    fraction = rng.random()
    threshold = min(math.nextafter(upper, lower), lower * (1 - fraction) + upper * fraction)
    left = [window for window in windows if window[feature] <= threshold]
    right = [window for window in windows if window[feature] > threshold]
    return _Tree(len(windows), feature, threshold, _grow_tree(left, depth - 1, rng), _grow_tree(right, depth - 1, rng))


def _load_tree(node, width, depth):
    size, feature, threshold, left, right = (node[key] for key in ("size", "feature", "threshold", "left", "right"))
    if type(size) is not int or size < 1:
        raise ValueError("invalid isolation tree size")
    if feature is None and threshold is None and left is None and right is None:
        return _Tree(size)
    if depth == 0 or type(feature) is not int or not 0 <= feature < width or not _finite(threshold):
        raise ValueError("invalid isolation tree split")
    left, right = _load_tree(left, width, depth - 1), _load_tree(right, width, depth - 1)
    if left.size + right.size != size:
        raise ValueError("invalid isolation tree children")
    return _Tree(size, feature, threshold, left, right)


@lru_cache(maxsize=1024)
def _path_adjustment(size):
    if size == 1:
        return 0.0
    return 2 * (math.fsum(1 / rank for rank in range(1, size)) - (size - 1) / size)


def _tree_path(tree, window):
    depth = 0
    while tree.feature is not None:
        tree = tree.left if window[tree.feature] <= tree.threshold else tree.right
        depth += 1
    return depth + _path_adjustment(tree.size)


def fit_detectors(rows: Iterable[DetectorRow], config: DetectorConfig, *,
                  discovery_rows: Iterable[DetectorRow]) -> FrozenDetectors:
    """Fit with caller-verified Alibaba train rows, using disjoint service sets."""
    reference_rows, discovery_rows = _ordered_rows(rows), _ordered_rows(discovery_rows)
    for group in (reference_rows, discovery_rows):
        for row in group:
            if row.split != "train":
                raise ValueError("detector fitting requires Alibaba train rows")
            if row.label is not None and (type(row.label) is not int or row.label not in (0, 1)):
                raise ValueError("training labels must be 0, 1 or None")
    if {row.service for row in reference_rows} & {row.service for row in discovery_rows}:
        raise ValueError("reference and discovery require disjoint services")
    segments, reference, _, _ = _training_windows(reference_rows, config, "reference")
    discovery_segments, _, discovery, anomalous = _training_windows(discovery_rows, config, "discovery")
    if len(reference) < 2 or not anomalous:
        raise ValueError("insufficient normal reference or anomalous discovery support")
    reference = _sample(reference, config.max_fit_windows)
    percentile_sample = _sample(discovery, config.max_fit_windows)
    candidates = {window.key: window for window in (*percentile_sample, *_sample(anomalous, config.max_fit_windows))}
    minimum = min(min(window.values) for window in reference)
    maximum = max(max(window.values) for window in reference)
    scaler = _fit_scaler(minimum, maximum, config.scale_floor)
    reference = [replace(window, values=_scale(window.values, scaler)) for window in reference]
    discovery = [replace(window, values=_scale(window.values, scaler)) for _, window in sorted(candidates.items())]
    profiles = [_nearest(reference, target) for target in discovery]
    profiles_by_key = dict(zip((target.key for target in discovery), profiles))
    distances = [profiles_by_key[window.key][0] for window in percentile_sample]
    if not all(_finite(distance) for distance, _ in profiles):
        raise ValueError("nonfinite discovery profile")
    threshold = _quantile(distances, config.discovery_quantile)
    patterns, _ = _sketch(reference, discovery, profiles, threshold, config, benchmark=True)
    vectors = [window.values for window in reference]
    sample_count = min(len(vectors), config.forest_sample_size)
    depth = math.ceil(math.log2(sample_count))
    rng = random.Random(config.seed)
    forest = tuple(_grow_tree(rng.sample(vectors, sample_count), depth, rng) for _ in range(config.forest_trees))
    center = tuple(median(column) for column in zip(*vectors))
    mad = tuple(max(config.scale_floor, 1.4826 * median(abs(value - center[index]) for value in column))
                for index, column in enumerate(zip(*vectors)))
    model = FrozenDetectors(config, segments + discovery_segments, scaler, threshold, patterns, sample_count,
                            forest, center, mad, "")
    model._validate()
    return replace(model, digest=_digest(model._body()))


def _score_sketch(model, window):
    distances = [(math.dist(window, pattern.center), pattern) for pattern in model.patterns]
    normal_distance = min(distance for distance, pattern in distances if pattern.kind == "normal")
    distance, pattern = min(distances, key=lambda item: item[0])
    tolerance = max(model.config.scale_floor, pattern.radius)
    if distance > tolerance:
        return normal_distance, "novel", None
    kind = "normal" if pattern.kind == "normal" else "known"
    return normal_distance, kind, pattern.pattern_id


def _score_forest(model, window):
    path = math.fsum(_tree_path(tree, window) for tree in model.forest) / len(model.forest)
    return 2 ** (-path / _path_adjustment(model.forest_sample_count))


def _score_mad(model, window):
    return max(abs(value - center) / scale for value, center, scale in zip(window, model.median_center, model.mad_scale))


def predict_detectors(model: FrozenDetectors | Mapping | None, rows: Iterable[DetectorRow], *,
                      measure_inference: bool = False) -> tuple[DetectorPrediction, ...]:
    """Score each observed bucket through its timestamp. Labels are ignored.

    The caller supplies only data visible as of each evaluation. Missing or
    broken history yields unavailable records across the three model families.
    """
    ordered = _ordered_rows(rows)
    if isinstance(model, Mapping):
        try:
            model = FrozenDetectors.from_dict(model)
        except ValueError:
            model = None
    if not isinstance(model, FrozenDetectors):
        return tuple(DetectorPrediction(row.run_id, row.service, row.timestamp, model_id, None, "unavailable",
                                        "invalid_model") for row in ordered for model_id in MODEL_IDS)
    predictions = []
    for row, history, reason in _timeline(ordered, model.config):
        identity = row.run_id, row.service, row.timestamp
        if history is not None:
            window = _scale(tuple(item.value for item in history), model.scaler)
            if not all(map(_finite, window)):
                reason = "nonfinite_score"
        for model_id, scorer in zip(MODEL_IDS, (_score_sketch, _score_forest, _score_mad)):
            if reason:
                predictions.append(DetectorPrediction(*identity, model_id, None, "unavailable", reason))
                continue
            started = perf_counter() if measure_inference else None
            result = scorer(model, window)
            elapsed = perf_counter() - started if started is not None else None
            score, kind, pattern_id = result if model_id == "adsketch" else (result, None, None)
            if _finite(score):
                predictions.append(DetectorPrediction(*identity, model_id, score, "available", None, kind, pattern_id, elapsed))
            else:
                predictions.append(DetectorPrediction(*identity, model_id, None, "unavailable", "nonfinite_score"))
    return tuple(predictions)
