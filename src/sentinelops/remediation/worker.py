"""Resume committed actions without creating an intent or sending a mutation.

Prepared intents are canceled, not automatically dispatched after a restart.
Only dispatching/verifying actions enter the existing observation reconciler.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import logging

import asyncpg

from sentinelops.remediation.domain import ActionConflict, KubernetesUnavailable

logger = logging.getLogger('sentinelops-remediation-worker')
RETRYABLE = (asyncpg.PostgresConnectionError, asyncpg.CannotConnectNowError,
             asyncpg.InterfaceError, ConnectionError, OSError, TimeoutError, KubernetesUnavailable)


class ReconciliationWorker:
    def __init__(self, journal, executor, *, timeout_seconds=30.):
        if executor.journal is not journal or executor.policy.enabled is not False:
            raise ValueError('reconciliation worker requires its own disabled dispatch policy')
        if not 1 <= timeout_seconds <= 60:
            raise ValueError('reconciliation timeout outside supported bounds')
        self.journal, self.executor, self.timeout_seconds = journal, executor, timeout_seconds

    async def tick(self):
        async with asyncio.timeout(self.timeout_seconds):
            await self.journal.check_ready()
            state = await self.journal.active(self.executor.policy.target)
            if state is None:
                return {'status': 'IDLE'}
            if state['intent']['policy']['target'] != asdict(self.executor.policy.target):
                raise RuntimeError('active action is bound to a different target')
            if state['phase'] == 'prepared':
                try:
                    state = await self.journal.transition(state['action_id'], state['revision'], 'blocked',
                        {'reason': 'prepared-intent-requires-explicit-revalidation'}, release=True)
                except ActionConflict:
                    return {'status': 'CONTENDED'}
            elif state['phase'] in ('dispatching', 'verifying'):
                state = await self.executor.reconcile(state['action_id'])
            elif state['phase'] != 'escalated':
                # A blocked/recovered action cannot retain a target claim.
                raise RuntimeError('terminal action unexpectedly holds the target')
            return {'status': 'QUARANTINED' if state['phase'] == 'escalated' else 'OBSERVED',
                    'action_id': state['action_id'], 'revision': state['revision'], 'phase': state['phase']}

    async def run(self, stop, *, poll_seconds=5., max_ticks=None, max_failures=12):
        if (not .25 <= poll_seconds <= 10 or type(max_failures) is not int or not 1 <= max_failures <= 100
            or max_ticks is not None and (type(max_ticks) is not int or not 1 <= max_ticks <= 100000)):
            raise ValueError('worker poll/tick/retry bounds are invalid')
        failures = ticks = 0
        while not stop.is_set() and (max_ticks is None or ticks < max_ticks):
            try:
                result = await self.tick()
            except RETRYABLE as exc:
                failures += 1
                logger.warning(json.dumps({'event': 'action_worker_dependency_unavailable',
                    'error_type': type(exc).__name__, 'consecutive_failures': failures}))
                if failures >= max_failures:
                    raise RuntimeError('action reconciliation retry budget exhausted') from None
            else:
                failures = 0
                logger.info(json.dumps({'event': 'action_worker_tick', **result}, sort_keys=True))
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            delay = min(30., poll_seconds * 2 ** min(failures, 7))
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
        return {'status': 'STOPPED', 'ticks': ticks}
