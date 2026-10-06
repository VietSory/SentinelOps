from __future__ import annotations

import time

import httpx

CHECKOUT = "http://127.0.0.1:8001"
PAYMENT = "http://127.0.0.1:8002"
INVENTORY = "http://127.0.0.1:8003"


def expect(response: httpx.Response, status: int, label: str) -> None:
    if response.status_code != status:
        raise RuntimeError(f"{label}: expected {status}, got {response.status_code}: {response.text}")


def set_payment_fault(client: httpx.Client, mode: str, delay_ms: int = 0) -> None:
    response = client.post(f"{PAYMENT}/internal/faults", json={"mode": mode, "delay_ms": delay_ms})
    expect(response, 200, f"set payment fault {mode}")


def main() -> None:
    with httpx.Client(timeout=10.0) as client:
        expect(client.get(f"{CHECKOUT}/ready"), 200, "checkout readiness")
        expect(client.get(f"{PAYMENT}/ready"), 200, "payment readiness")
        expect(client.get(f"{INVENTORY}/ready"), 200, "inventory readiness")

        inventory = client.get(f"{INVENTORY}/v1/inventory/sku-001")
        expect(inventory, 200, "inventory read")

        payload = {"sku": "sku-001", "quantity": 1, "amount": 99.90, "currency": "USD"}
        healthy = client.post(f"{CHECKOUT}/v1/checkout", json=payload)
        expect(healthy, 200, "healthy checkout")
        correlation_id = healthy.headers.get("x-correlation-id")
        if not correlation_id or healthy.json()["correlation_id"] != correlation_id:
            raise RuntimeError("correlation ID was not returned consistently")
        print("PASS healthy checkout", healthy.json())

        set_payment_fault(client, "latency", 1200)
        started = time.perf_counter()
        delayed = client.post(f"{CHECKOUT}/v1/checkout", json=payload)
        elapsed = time.perf_counter() - started
        expect(delayed, 200, "latency checkout")
        if elapsed < 1.0:
            raise RuntimeError(f"latency injection did not propagate; elapsed={elapsed:.2f}s")
        print(f"PASS latency propagation elapsed={elapsed:.2f}s")

        set_payment_fault(client, "error")
        failed = client.post(f"{CHECKOUT}/v1/checkout", json=payload)
        expect(failed, 502, "payment cascading error")
        print("PASS cascading failure", failed.json())

        set_payment_fault(client, "healthy")
        recovered = client.post(f"{CHECKOUT}/v1/checkout", json=payload)
        expect(recovered, 200, "recovered checkout")
        print("PASS recovery after fault reset")

    print("STEP 2 SMOKE TEST: PASS")


if __name__ == "__main__":
    main()
