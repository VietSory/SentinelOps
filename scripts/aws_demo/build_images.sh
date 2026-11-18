#!/usr/bin/env bash
# Run with Bash in WSL. Builds local images only; never logs in or pushes.
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash scripts/aws_demo/build_images.sh stage1
  bash scripts/aws_demo/build_images.sh stage2 <application-ECR-digest>
  BASE=<application-ECR-digest> bash scripts/aws_demo/build_images.sh stage2

stage1 (default): application + control-plane, tagged sentinelops/<name>:git-<sha>.
stage2: payment-regression, only after application is pushed separately to ECR.
        BASE must be <12-digit-account>.dkr.ecr.ap-southeast-1.amazonaws.com/
        sentinelops/application@sha256:<64 lowercase hex> (on one line).

Optional environment: PLATFORM (default linux/amd64), BUILD_OUTPUT_DIR.
Stage 2 uses python:3.11-slim-bookworm for offline canonical BASE/Dockerfile checks.
Docker must already have permission to pull the private BASE for stage 2.
EOF
}

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

stage="${1:-stage1}"
case "$stage" in
  -h|--help) usage; exit 0 ;;
  stage1) [[ $# -le 1 ]] || fail 'stage1 takes no BASE argument' ;;
  stage2) [[ $# -le 2 ]] || fail 'stage2 takes one BASE argument' ;;
  *) usage >&2; exit 1 ;;
esac

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd -- "$root"
command -v git >/dev/null || fail 'git is required in WSL'
command -v docker >/dev/null || fail 'docker is required in WSL'
revision="$(git rev-parse HEAD)"
short_sha="$(git rev-parse --short HEAD)"
image_tag="git-${short_sha}"
platform="${PLATFORM:-linux/amd64}"
output="${BUILD_OUTPUT_DIR:-$root/artifacts/aws-demo}"

if [[ "$stage" == stage2 ]]; then
  base="${2:-${BASE:-}}"
  [[ "$base" =~ ^[0-9]{12}\.dkr\.ecr\.ap-southeast-1\.amazonaws\.com/sentinelops/application@sha256:[0-9a-f]{64}$ ]] ||
    fail 'stage2 requires a digest-pinned ap-southeast-1 ECR sentinelops/application BASE'
  # Run the repository validator and canonical Dockerfile check without network
  # access or the host Python. Copy the relevant repo trees and fix ownership.
  docker run --rm --network none --user 0:0 \
    --env "BASE=$base" --env PYTHONDONTWRITEBYTECODE=1 \
    --mount "type=bind,source=$root,target=/source,readonly" \
    python:3.11-slim-bookworm sh -ec '
      mkdir -p /tmp/repo
      cp -a /source/scripts /source/docker /tmp/repo/
      chown -R 0:0 /tmp/repo
      cd /tmp/repo
      python -c '\''import os; from scripts.check_payment_regression_image import check; check(os.environ["BASE"]); print("BASE and canonical payment-regression Dockerfile: PASS")'\''
    '
fi

docker info >/dev/null
mkdir -p -- "$output"
printf '%s\n' "$revision" > "$output/$stage-source-revision.txt"
git status --short > "$output/$stage-source-status.txt"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  printf 'WARNING: tracked working-tree changes exist; git tag records HEAD, not a clean-source attestation.\n' >&2
fi

build_image() {
  local name="$1" dockerfile="$2"
  shift 2
  local tag="sentinelops/$name:$image_tag"
  printf 'Building %s (%s) from %s\n' "$tag" "$platform" "$dockerfile"
  docker build --platform "$platform" --progress plain \
    --label "org.opencontainers.image.revision=$revision" \
    --file "$dockerfile" --tag "$tag" "$@" . 2>&1 |
    tee "$output/$name-build.log"
  docker image inspect --format \
    '{{index .RepoTags 0}} id={{.Id}} size_bytes={{.Size}} platform={{.Os}}/{{.Architecture}}' \
    "$tag"
}

case "$stage" in
  stage1)
    build_image application docker/Dockerfile.service
    build_image control-plane docker/Dockerfile.control-plane \
      --build-arg "SENTINELOPS_SOURCE_REVISION=$revision"
    docker image inspect "sentinelops/application:$image_tag" \
      "sentinelops/control-plane:$image_tag" > "$output/stage1-images.json"
    ;;
  stage2)
    printf '%s\n' "$base" > "$output/payment-regression-base.txt"
    build_image payment-regression docker/Dockerfile.payment-regression \
      --build-arg "BASE=$base"
    docker image inspect "sentinelops/payment-regression:$image_tag" > "$output/stage2-images.json"
    ;;
esac
printf 'Completed %s; local metadata/logs: %s\n' "$stage" "$output"
