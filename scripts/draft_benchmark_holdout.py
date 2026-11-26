"""Materialize the proposed shared holdout design without starting capture."""
from dataclasses import asdict, replace

from sentinelops.experiments.local_campaign import SLOT_OVERHEAD_SECONDS
from sentinelops.experiments.local_corpus import ALIBABA_PLAN, load_plan, recipe
from sentinelops.telemetry.core import stable_id


def draft_matrix():
    plan = load_plan(ALIBABA_PLAN)
    kinds = ('healthy', 'payment-abrupt', 'inventory-abrupt', 'payment-gradual-high',
             'inventory-gradual-high', 'payment-gradual-low', 'inventory-gradual-low')
    groups = []
    for ordinal in range(1, 6):
        group = f'bench-final-{ordinal:02d}'
        slots = []
        for index, kind in enumerate(kinds):
            base = kind.removesuffix('-high').removesuffix('-low')
            config = recipe(plan, 'development-validation', ordinal, base)
            config = replace(config, split='test', seed=20263000 + 10 * ordinal + index,
                             maximum_delay_ms=400 if kind.endswith('-low') else config.maximum_delay_ms)
            config.validate()
            seconds = config.warmup_seconds + config.active_seconds + config.recovery_seconds
            slots.append({'slot_id': f'{group}-{kind}', 'kind': kind, 'recipe': asdict(config),
                          'budget_seconds': seconds + SLOT_OVERHEAD_SECONDS})
        groups.append({'group_id': group, 'ordinal': ordinal, 'slots': slots,
                       'budget_seconds': sum(slot['budget_seconds'] for slot in slots)})
    matrix = {
        'schema_version': 'benchmark-holdout-design/draft-v1',
        'role': 'sealed-final', 'source_profile': plan['source_profile'],
        'oracle': {'measure': 'p95_latency_ms', 'threshold': 750, 'comparison': '>', 'entry_buckets': 2},
        'groups': groups, 'budget_seconds': sum(group['budget_seconds'] for group in groups),
        'slot_count': sum(len(group['slots']) for group in groups),
    }
    return {'status': 'blocked', 'matrix': matrix, 'matrix_sha256': stable_id(matrix),
            'capture_allowed': False,
            'reasons': ['approved-holdout-protocol-and-adapter-required',
                        'verified-candidate-freeze-and-low-ramp-development-receipts-required']}
