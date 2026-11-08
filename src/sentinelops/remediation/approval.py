"""Operator-only known-good ledger from collected image evidence.

`review_payment_image` produces unapproved `payment-image-evidence/v1`. This
module turns exactly one such record into `approved-payment-images/v1` after an
operator decision taken outside software. The ledger approves an image only; it
never authorizes dispatch. Callers must verify it with `KnownGoodRegistry`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sentinelops.contracts import utc_time, validate_payload
from sentinelops.telemetry.core import timestamp


def build_known_good_ledger(store, evidence_ref, *, approved_by, environment, now=None):
    if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 128:
        raise ValueError('approved_by must be a nonempty explicit operator identity')
    evidence = store.read(evidence_ref)
    validate_payload('payment-image-evidence.schema.json', evidence)
    if (evidence['environment'] != environment or evidence['known_good_approved'] is not False
            or evidence['action_dispatch_authorized'] is not False):
        raise ValueError('image evidence environment or approval state differs')
    approved = now or datetime.now(timezone.utc)
    after = store.read(evidence['after_ref'])
    if utc_time(after['observation']['observed_at']) > approved or utc_time(evidence['completed_at']) > approved:
        raise ValueError('approval cannot precede the evidence it approves')
    record = {'ledger_version': 'approved-payment-images/v1', 'approved_by': approved_by.strip(),
              'approved_at': timestamp(approved), 'approval_scope': 'known-good-image-only',
              'action_dispatch_authorized': False,
              'images': [{'target': evidence['target'], 'image': evidence['image'], 'environment': environment,
                          'before_ref': evidence['before_ref'], 'after_ref': evidence['after_ref'],
                          'health_ref': evidence['health_ref']}]}
    validate_payload('payment-known-good.schema.json', record)
    return store.write('manifests', record), record
