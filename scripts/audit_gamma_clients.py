"""Verify or acquire only the frozen quarantined client captures, then audit format."""
import argparse
import json
from pathlib import Path

import httpx

from sentinelops.public_data.gamma import canonical_bytes
from sentinelops.public_data.gamma_clients import audit_clients, clean_commit, download_clients, load_context


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('data/raw/public/gamma-client-capability-v1'))
    parser.add_argument('--scope', type=Path, default=Path('configs/gamma_client_capture_scope.json'))
    parser.add_argument('--catalog', type=Path, default=Path('data/raw/public/inventories/69934c78fb790eeaa12931f25669770b50e44c70b647fd98c8a6d63698b4caf8.json'))
    parser.add_argument('--groups', type=Path, default=Path('configs/gamma_source_groups.json'))
    parser.add_argument('--splits', type=Path, default=Path('configs/gamma_resource_splits.json'))
    parser.add_argument('--download', action='store_true', help='Bounded pinned archive acquisition when files are absent.')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists; choose an exclusive report path')
    source_commit = clean_commit(Path.cwd())
    context = load_context(args.scope, args.catalog, args.groups, args.splits)
    try:
        acquisition = None
        if args.download:
            with httpx.Client(timeout=45, follow_redirects=True, trust_env=False) as client:
                acquisition = download_clients(client, args.root, *context)
        report = audit_clients(args.root, *context, source_commit=source_commit)
    except httpx.HTTPError as exc:
        raise SystemExit('GAMMA client archive unavailable: ' + type(exc).__name__) from None
    if clean_commit(Path.cwd()) != source_commit or context != load_context(args.scope, args.catalog, args.groups, args.splits):
        raise SystemExit('source/context changed during client capability audit')
    if acquisition is not None:
        report['acquisition'] = acquisition
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as stream:
        stream.write(canonical_bytes(report))
    print(json.dumps({k: v for k, v in report.items() if k != 'rows'}, indent=2))
