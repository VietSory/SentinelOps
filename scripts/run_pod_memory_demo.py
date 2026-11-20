"""Observe a dedicated OOM demo and execute only its on-call-approved RAM step."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg
import httpx

from sentinelops.incidents.repository import IncidentRepository
from sentinelops.remediation.domain import Blocked
from sentinelops.remediation.pod_memory import KubernetesMemory, MemoryController, MemoryTarget, ObservationPending
from sentinelops.telemetry.store import ArtifactStore


def metric_call(metrics, name, *args):
    """Monitoring failures never enter a control or incident-writing path."""
    if metrics is not None:
        try:
            getattr(metrics, name)(*args)
        except Exception:
            # No upstream exception/DSN/credential reflection into controller logs.
            pass


async def sample_metrics(kube, metrics, stop, *, interval=5, timeout=3,
                         now=lambda: datetime.now(timezone.utc)):
    """Independent GET-only sampling also stays current while approval is pending."""
    while not stop.is_set():
        try:
            value = await asyncio.wait_for(kube.observe(now()), timeout=timeout)
            try:
                metrics.observe(value)
            except Exception:
                metric_call(metrics, 'unavailable')
        except asyncio.CancelledError:
            raise
        except Exception:
            metric_call(metrics, 'unavailable')
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


def database_url(environ=None):
    """Resolve runtime credentials in RAM, before opening any database socket."""
    environ = os.environ if environ is None else environ
    dsn = environ.get('SENTINELOPS_DATABASE_URL', '')
    if not dsn:
        base = environ.pop('CONTROL_DATABASE_URL', '')
        password = environ.pop('UI_DATABASE_PASSWORD', '')
        parsed = urlsplit(base)
        if (not password or parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname
            or parsed.path != '/sentinelops_control' or parsed.query != 'sslmode=verify-full'
            or parsed.fragment):
            raise ValueError('verified control database Secret configuration is required')
        dsn = urlunsplit(parsed._replace(netloc='control_writer:' + quote(password, safe='')
                                         + '@' + parsed.netloc.rsplit('@', 1)[-1]))
    parsed = urlsplit(dsn)
    if (parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname
        or parsed.path != '/sentinelops_control' or parsed.fragment
        or parsed.hostname != '127.0.0.1' and parsed.query != 'sslmode=verify-full'):
        raise ValueError('explicit control runtime database with remote verified TLS is required')
    return dsn


async def run(args):
    dsn = database_url()
    target = MemoryTarget(**json.loads(args.target.read_text()))
    pool = await asyncpg.create_pool(dsn, min_size=0, max_size=2, command_timeout=5, timeout=5)
    kube = KubernetesMemory(target, server=args.server, token_file=args.token_file, ca_file=args.ca_file)
    metrics, sampler, stop = None, None, None
    try:
        incidents = IncidentRepository(pool)
        await incidents.check_ready()
        controller = MemoryController(target=target, kube=kube, store=ArtifactStore(args.store),
            incidents=incidents, kill_switch=args.kill_switch, enabled=args.policy_enabled)
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        print(json.dumps({'status': 'READY', 'runbook': 'pod-memory-limit-demo/v1',
                          'policy_enabled': args.policy_enabled}), flush=True)
        if getattr(args, 'metrics_otlp_endpoint', None):
            try:
                from sentinelops.remediation.pod_memory_metrics import PodMemoryMetrics
                metrics = PodMemoryMetrics.otlp(args.metrics_otlp_endpoint, target=target)
                sampler = asyncio.create_task(sample_metrics(kube, metrics, stop))
            except Exception as exc:
                print(json.dumps({'status': 'METRICS_UNAVAILABLE', 'error_type': type(exc).__name__}), flush=True)
        while not stop.is_set():
            try:
                result = await controller.tick()
                metric_call(metrics, 'phase', result.get('phase', 'unavailable'))
                print(json.dumps(result), flush=True)
            except (Blocked, ObservationPending) as exc:
                metric_call(metrics, 'phase', 'blocked' if isinstance(exc, Blocked) else 'observing')
                print(json.dumps({'phase': 'observing', 'reason': str(exc)}), flush=True)
            except (httpx.HTTPError, asyncpg.PostgresError, OSError, ValueError, KeyError, TypeError) as exc:
                metric_call(metrics, 'phase', 'unavailable')
                print(json.dumps({'phase': 'unavailable', 'error_type': type(exc).__name__}), flush=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                pass
    finally:
        if stop is not None:
            stop.set()
        if sampler is not None:
            sampler.cancel()
            await asyncio.gather(sampler, return_exceptions=True)
        if metrics is not None:
            try:
                await asyncio.to_thread(metrics.close)
            except Exception:
                pass
        await kube.close()
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('target', 'store', 'token-file', 'ca-file', 'kill-switch'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--server', required=True)
    parser.add_argument('--policy-enabled', action='store_true')
    parser.add_argument('--metrics-otlp-endpoint', help='Optional allowlisted observation-only OTLP endpoint')
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except Exception as exc:
        raise SystemExit('pod memory demo stopped: ' + type(exc).__name__) from None


if __name__ == '__main__':
    main()
