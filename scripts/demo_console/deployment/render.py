"""Render only. No kubectl, network access or mutation; image digest required."""

from __future__ import annotations

import argparse
import json
import re


def render(image: str, *, enable_faults: bool = False) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[a-f0-9]{64}", image):
        raise ValueError("--image must be a registry image pinned by sha256 digest")
    namespace = "sentinelops-b-actions"
    name = "fault-console"
    labels = {"app.kubernetes.io/name": name}
    # Exec probes reach the actual loopback listener. HTTP probes to the Pod IP
    # would fail, and /api/status would probe the business services needlessly.
    probe = {
        "exec": {"command": ["python", "-c", (
            "import urllib.request; "
            "urllib.request.build_opener(urllib.request.ProxyHandler({})).open("
            "'http://127.0.0.1:8765/',timeout=2).close()"
        )]},
        "timeoutSeconds": 3,
        "periodSeconds": 15,
        "failureThreshold": 3,
    }
    return {
        "apiVersion": "v1", "kind": "List", "items": [
            {
                "apiVersion": "v1", "kind": "ServiceAccount",
                "metadata": {"name": name, "namespace": namespace, "labels": labels},
                "automountServiceAccountToken": False,
            },
            {
                "apiVersion": "apps/v1", "kind": "Deployment",
                "metadata": {"name": name, "namespace": namespace, "labels": labels},
                "spec": {
                    "replicas": 1,
                    "revisionHistoryLimit": 2,
                    "strategy": {"type": "Recreate"},
                    "selector": {"matchLabels": labels},
                    "template": {
                        "metadata": {"labels": labels},
                        "spec": {
                            "serviceAccountName": name,
                            "automountServiceAccountToken": False,
                            "enableServiceLinks": False,
                            "securityContext": {
                                "runAsNonRoot": True, "runAsUser": 10001,
                                "runAsGroup": 10001,
                                "seccompProfile": {"type": "RuntimeDefault"},
                            },
                            "containers": [{
                                "name": name, "image": image,
                                "imagePullPolicy": "IfNotPresent",
                                "env": [
                                    {"name": "DEMO_CONSOLE_ENABLE_FAULTS", "value": "true" if enable_faults else "false"},
                                    {"name": "DEMO_CONSOLE_DEPLOY_SCRIPT", "value": ""},
                                    {"name": "DEMO_CONSOLE_OOM_SCRIPT", "value": ""},
                                    {"name": "DEMO_CONSOLE_ENABLE_OOM", "value": "false"},
                                    {"name": "DEMO_CONSOLE_PORT", "value": "8765"},
                                    {"name": "DEMO_CONSOLE_PAYMENT_URL", "value": "http://payment-service:8000"},
                                    {"name": "DEMO_CONSOLE_INVENTORY_URL", "value": "http://inventory-service:8000"},
                                ],
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "resources": {
                                    "requests": {"cpu": "25m", "memory": "64Mi"},
                                    "limits": {"cpu": "250m", "memory": "128Mi"},
                                },
                                "readinessProbe": probe,
                                "livenessProbe": {**probe, "initialDelaySeconds": 15},
                            }],
                        },
                    },
                },
            },
            {
                "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                "metadata": {"name": name, "namespace": namespace, "labels": labels},
                "spec": {"podSelector": {"matchLabels": labels}, "policyTypes": ["Ingress"], "ingress": []},
            },
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--enable-faults", action="store_true",
                        help="Enable only HTTP fault controls for an authorized dev session; script runners stay unconfigured")
    args = parser.parse_args()
    try:
        manifest = render(args.image, enable_faults=args.enable_faults)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(manifest, indent=2))
