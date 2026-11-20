"""Render a guarded demo-only RAM reset patch; never call Kubernetes or approve."""
import argparse
import json
from pathlib import Path

from sentinelops.remediation.pod_memory import AFTER, BEFORE, MemoryTarget, NAME, NAMESPACE


def render(deployment, target, *, fault=False):
    target = MemoryTarget(**target)
    meta, spec = deployment['metadata'], deployment['spec']
    containers = spec['template']['spec']['containers']
    if (meta.get('uid') != target.deployment_uid or meta.get('name') != NAME
        or meta.get('namespace') != NAMESPACE or not meta.get('resourceVersion')
        or spec.get('replicas') != 1 or len(containers) != 1
        or spec['template']['metadata']['labels'].get('sentinelops.io/demo') != 'oom'):
        raise ValueError('reset is limited to the captured single-replica OOM demo')
    container = containers[0]
    if (container.get('name') != target.container or container.get('image') != target.image
        or container['resources']['requests']['memory'] != '32Mi'
        or container['resources']['limits']['memory'] not in (BEFORE, AFTER)):
        raise ValueError('OOM target image or bounded memory settings changed')
    return [
        {'op': 'test', 'path': '/metadata/uid', 'value': target.deployment_uid},
        {'op': 'test', 'path': '/metadata/resourceVersion', 'value': meta['resourceVersion']},
        {'op': 'test', 'path': '/spec/template/spec/containers/0', 'value': container},
        {'op': 'replace', 'path': '/spec/template/spec/containers/0/resources/limits/memory',
         'value': BEFORE if fault else AFTER},
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--fault', action='store_true', help='Explicitly lower demo limit to 64Mi for a new rehearsal')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    patch = render(json.loads(args.deployment.read_text()), json.loads(args.target.read_text()), fault=args.fault)
    with args.output.open('x') as stream:
        json.dump(patch, stream, indent=2)
        stream.write('\n')
    print('Rendered guarded demo RAM patch; no apply or approval performed')


if __name__ == '__main__':
    main()
