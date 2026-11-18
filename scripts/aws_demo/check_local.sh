#!/bin/sh
# Local-only entrypoint, invoked with wsl.exe -d Ubuntu -- sh <this-file>.
set -eu
repo=/mnt/d/year4/AI4life/SentinelOps
copy=$(mktemp -d /tmp/sentinelops-control-check.XXXXXX)
# A Git archive avoids node_modules, state, credentials and large untracked artifacts.
git -C "$repo" archive HEAD | tar -x -C "$copy"
mkdir -p "$copy/scripts/aws_demo"
for filename in control.py control_job.py control-job.yaml upload_model.py check_control.py check_local.sh CONTROL_RUNBOOK.md; do
  cp "$repo/scripts/aws_demo/$filename" "$copy/scripts/aws_demo/$filename"
done
chown -R "$(id -u):$(id -g)" "$copy"
printf 'Copied repo (chowned): %s\n' "$copy"
docker run --rm --pull never --network none \
  --env PYTHONDONTWRITEBYTECODE=1 \
  --mount "type=bind,source=$copy,target=/work,readonly" \
  --mount type=bind,source=/usr/bin/docker,target=/usr/bin/docker,readonly \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  --workdir /work python:3.11-slim-bookworm \
  python scripts/aws_demo/check_control.py --postgres-image postgres:17-alpine
