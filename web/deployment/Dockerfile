ARG OPERATOR_IMAGE
FROM ${OPERATOR_IMAGE}

ARG OPERATOR_USER
ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision=${SOURCE_REVISION}

COPY deployment/demo_routes.py /opt/venv/lib/python3.11/site-packages/sentinelops/ui/demo_routes.py
COPY dist/ /opt/venv/lib/python3.11/site-packages/sentinelops/ui/demo_site/

USER root
RUN --mount=type=bind,source=deployment/install.py,target=/tmp/install-demo.py \
    /opt/venv/bin/python -B /tmp/install-demo.py
USER ${OPERATOR_USER}
