#!/usr/bin/env bash
# No browser-supplied command or argument. Python validates the fixed demo target.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
exec python3 -u -B -m scripts.demo_console.trigger_oom
