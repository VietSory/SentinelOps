"""Internal rollback types. No client-selected image, target or RCA approval."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import re

from sentinelops.contracts import utc_time
from sentinelops.telemetry.core import stable_id, timestamp

RUNBOOK = 'payment-rollback-known-good/v1'
DIGEST = re.compile(r'[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}')
NAME = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')


class Blocked(ValueError):
    """A fixed reason code, never raw provider output or credentials."""


class ActionConflict(RuntimeError):
    pass


class KubernetesUnavailable(RuntimeError):
    pass


def image_digest(image: str) -> str:
    if not isinstance(image, str) or len(image) > 512 or not DIGEST.fullmatch(image):
        raise Blocked('immutable-image-digest-required')
    return image


@dataclass(frozen=True)
class Target:
    cluster_uid: str
    namespace: str
    namespace_uid: str
    deployment: str
    deployment_uid: str
    container: str

    def __post_init__(self):
        if (self.namespace != 'sentinelops-b-actions' or self.deployment != 'payment-service'
            or self.container != 'payment' or any(not isinstance(v, str) or not 1 <= len(v) <= 128
                for v in (self.cluster_uid, self.namespace_uid, self.deployment_uid))):
            raise Blocked('target-outside-local-payment-sandbox')

    @property
    def key(self):
        return stable_id(asdict(self))


@dataclass(frozen=True)
class Request:
    incident_id: str
    incident_revision: int
    idempotency_key: str

    def __post_init__(self):
        if (not isinstance(self.incident_id, str) or not 1 <= len(self.incident_id) <= 256
            or type(self.incident_revision) is not int or self.incident_revision < 1
            or not isinstance(self.idempotency_key, str)
            or not re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', self.idempotency_key)):
            raise ValueError('invalid rollback request identity/revision/idempotency key')

    def fingerprint(self, target):
        return stable_id({'runbook': RUNBOOK, 'target_key': target.key, **asdict(self)})


@dataclass(frozen=True)
class Policy:
    target: Target
    enabled: bool = False
    max_pods: int = 1
    max_surge: int = 0
    cooldown_seconds: int = 600
    verification_seconds: int = 600
    freshness_seconds: int = 180
    change_window_seconds: int = 600

    def __post_init__(self):
        if (type(self.enabled) is not bool or self.max_pods != 1 or type(self.max_pods) is not int
            or self.max_surge != 0 or type(self.max_surge) is not int
            or any(type(getattr(self, k)) is not int or getattr(self, k) != v for k, v in (
                ('cooldown_seconds', 600), ('verification_seconds', 600),
                ('freshness_seconds', 180), ('change_window_seconds', 600)))):
            raise ValueError('unsupported payment-rollback/local-v1 bounds')

    @property
    def identity(self):
        return stable_id({'policy_version': 'payment-rollback/local-v1', **asdict(self)})


@dataclass(frozen=True)
class Pod:
    uid: str
    image: str
    ready: bool
    image_id: str


@dataclass(frozen=True)
class Deployment:
    target: Target
    resource_version: str
    generation: int
    observed_generation: int
    image: str
    container_index: int
    replicas: int
    updated_replicas: int
    ready_replicas: int
    available_replicas: int
    max_surge: int
    max_unavailable: int
    strategy: str
    paused: bool
    hpa: bool
    pods: tuple[Pod, ...]
    observed_at: str

    def validate(self, policy, *, before_dispatch=True):
        utc_time(self.observed_at)
        image_digest(self.image)
        if self.target != policy.target:
            raise Blocked('target-identity-mismatch')
        integers = (self.generation, self.observed_generation, self.container_index, self.replicas,
            self.updated_replicas, self.ready_replicas, self.available_replicas, self.max_surge, self.max_unavailable)
        if (any(type(v) is not int or v < 0 for v in integers) or not self.resource_version
            or type(self.paused) is not bool or type(self.hpa) is not bool
            or any(not p.uid or type(p.ready) is not bool for p in self.pods)
            or len({p.uid for p in self.pods}) != len(self.pods)):
            raise Blocked('invalid-deployment-observation')
        inventory_ok = len(self.pods) == self.replicas if before_dispatch else len(self.pods) <= 2
        if (self.replicas != 1 or not inventory_ok or self.strategy != 'RollingUpdate'
            or self.max_surge > policy.max_surge or self.max_unavailable > policy.max_pods
            or self.paused or self.hpa):
            raise Blocked('blast-radius-or-rollout-policy')

    def ready_for(self, image, *, prior_generation, old_pods):
        return (self.image == image and self.generation > prior_generation
            and self.observed_generation >= self.generation
            and self.replicas == self.updated_replicas == self.ready_replicas == self.available_replicas == 1
            and len(self.pods) == 1 and not set(old_pods) & {p.uid for p in self.pods}
            and all(p.ready and p.image == image and re.search(r'sha256:[0-9a-f]{64}$', p.image_id) for p in self.pods))

    def projection(self):
        return asdict(self)


def rollback_patch(before: Deployment, image: str) -> list[dict]:
    """Only image replacement; server-side tests reject drift and UID reuse."""
    image_digest(image)
    image_digest(before.image)
    if image == before.image or image.split('@')[0] != before.image.split('@')[0]:
        raise Blocked('known-good-image-is-not-a-distinct-same-repository-digest')
    pointer = '/spec/template/spec/containers/' + str(before.container_index)
    return [
        {'op': 'test', 'path': '/metadata/uid', 'value': before.target.deployment_uid},
        {'op': 'test', 'path': '/metadata/resourceVersion', 'value': before.resource_version},
        {'op': 'test', 'path': '/metadata/generation', 'value': before.generation},
        {'op': 'test', 'path': '/spec/replicas', 'value': before.replicas},
        {'op': 'test', 'path': pointer + '/name', 'value': before.target.container},
        {'op': 'test', 'path': pointer + '/image', 'value': before.image},
        {'op': 'replace', 'path': pointer + '/image', 'value': image},
    ]


def fresh(value: str, now: datetime, seconds: int):
    elapsed = now - utc_time(value)
    if not timedelta(0) <= elapsed <= timedelta(seconds=seconds):
        raise Blocked('evidence-stale-or-future')
    timestamp(now)
