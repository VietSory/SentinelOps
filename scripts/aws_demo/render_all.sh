#!/usr/bin/env bash
# Offline only. Run inside the prepared Python 3.11 container; see README.render.md.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$root/src:$root${PYTHONPATH:+:$PYTHONPATH}"
cd "$root"
python -c 'import sys; assert sys.version_info[:2] == (3, 11), "Python 3.11 is required"'
command -v kubeconform >/dev/null || { echo 'kubeconform is required (tested with v0.7.0)' >&2; exit 1; }
python -m scripts.aws_demo.render_all "$@"
