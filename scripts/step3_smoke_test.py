from __future__ import annotations

import time

import httpx

CHECKOUT = "http://127.0.0.1:8001"
OTEL_HEALTH = "http://127.0.0.1:13133"
OTEL_METRICS = "http://127.0.0.1:8889/metrics"
JAEGER = "http://127.0.0.1:16686"
EXPECTED_SERVICES = {"checkout-service", "payment-service", "inventory-service"}


def wait_for(client: httpx.Client, url: str, timeout: float = 20.0) -> httpx.Response:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            response = client.get(url)
            if response.status_code < 500:
                return response
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"timeout waiting for {url}: {last_error}")


def main() -> None:
    with httpx.Client(timeout=5.0, trust_env=False) as client:
        collector = wait_for(client, OTEL_HEALTH)
        if collector.status_code != 200:
            raise RuntimeError(f"collector health: {collector.status_code}")
        print("PASS collector health")

        jaeger = wait_for(client, JAEGER)
        if jaeger.status_code != 200:
            raise RuntimeError(f"jaeger UI: {jaeger.status_code}")
        print("PASS jaeger UI")

        checkout = client.post(
            f"{CHECKOUT}/v1/checkout",
            headers={"x-correlation-id": "step3-smoke-test"},
            json={"sku": "sku-001", "quantity": 1, "amount": 99.90, "currency": "USD"},
        )
        if checkout.status_code != 200:
            raise RuntimeError(f"checkout failed: {checkout.status_code}: {checkout.text}")
        print("PASS telemetry-generating checkout")

        deadline = time.time() + 15
        metrics_text = ""
        while time.time() < deadline:
            response = client.get(OTEL_METRICS)
            if response.status_code == 200:
                metrics_text = response.text
                if "sentinelops_http_requests" in metrics_text:
                    break
            time.sleep(1)
        else:
            raise RuntimeError("custom SentinelOps metrics were not exported")
        print("PASS custom metrics exported")

        services = client.get(f"{JAEGER}/api/v3/services")
        if services.status_code != 200:
            raise RuntimeError(f"Jaeger services API: {services.status_code}")
        found = set(services.json().get("services", []))
        if not EXPECTED_SERVICES.issubset(found):
            raise RuntimeError(f"Jaeger services incomplete: expected={EXPECTED_SERVICES}, found={found}")
        print("PASS Jaeger service registry", sorted(EXPECTED_SERVICES))

        # Jaeger indexes traces asynchronously. Poll until the checkout trace
        # produces the expected service dependencies.
        deadline = time.time() + 15

        while time.time() < deadline:
            now = time.time()
            start_ms = int((now - 300) * 1000)
            end_ms = int(now * 1000)

            response = client.get(
                f"{JAEGER}/api/v3/dependencies",
                params={
                    "startTime": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 300)
                    ),
                    "endTime": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)
                    ),
                },
            )

            if response.status_code == 200:
                payload = response.json()
                dependencies = payload.get("dependencies", [])

                edges = {
                    (
                        dep.get("parent"),
                        dep.get("child"),
                    )
                    for dep in dependencies
                }

                if {
                    ("checkout-service", "payment-service"),
                    ("checkout-service", "inventory-service"),
                }.issubset(edges):
                    print("PASS distributed trace dependency graph")
                    print("STEP 3 SMOKE TEST: PASS")
                    return

            time.sleep(1)

        raise RuntimeError(
            "Jaeger did not expose expected checkout dependencies within timeout"
        )

if __name__ == "__main__":
    main()
