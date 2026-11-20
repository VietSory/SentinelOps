FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

ARG SENTINELOPS_SOURCE_REVISION=unrecorded
LABEL org.opencontainers.image.source="https://github.com/VietSory/SentinelOps" \
      org.opencontainers.image.revision="${SENTINELOPS_SOURCE_REVISION}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    DEMO_CONSOLE_ENABLE_FAULTS=false \
    DEMO_CONSOLE_DEPLOY_SCRIPT=""
WORKDIR /opt/sentinelops
COPY scripts/demo_console/deployment/requirements.txt /tmp/console-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/console-requirements.txt
COPY services/__init__.py ./services/__init__.py
COPY services/common/__init__.py services/common/faults.py ./services/common/
COPY scripts/demo_console/*.py ./scripts/demo_console/
COPY scripts/demo_console/static ./scripts/demo_console/static
USER 10001:10001
CMD ["python", "-B", "-m", "scripts.demo_console"]
