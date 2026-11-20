"""Render supplemental OOM demo RBAC for an administrator; no cluster calls."""
import argparse
from pathlib import Path

import yaml

from scripts.aws_demo.oom_controller import render_rbac


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-name', action='append', default=[],
                        help='Allow GET only these observed Kubernetes node names')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.output.open('x') as stream:
        yaml.safe_dump_all(render_rbac(node_names=args.node_name), stream, sort_keys=False)
    print('Rendered exact OOM Deployment permission and optional pinned node GET; no apply performed')


if __name__ == '__main__':
    main()
