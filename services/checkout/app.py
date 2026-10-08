from __future__ import annotations

import os
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.common.observability import configure, inject_trace_headers, new_client_span
from services.common.runtime import correlation_id_var, install_request_context, log_event

SERVICE_NAME = os.getenv("SERVICE_NAME", "checkout-service")
PAYMENT_URL = os.getenv("PAYMENT_URL", "http://127.0.0.1:8002")
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://127.0.0.1:8003")
DOWNSTREAM_TIMEOUT_SECONDS = float(os.getenv("DOWNSTREAM_TIMEOUT_SECONDS", "4.0"))
tracer, _meter = configure(SERVICE_NAME)


class CheckoutRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    quantity: int = Field(default=1, ge=1, le=100)
    amount: float = Field(gt=0, le=1_000_000)
    currency: str = Field(default="USD", min_length=3, max_length=3)


class CheckoutResponse(BaseModel):
    checkout_id: str
    payment_id: str
    sku: str
    quantity: int
    status: str
    correlation_id: str


app = FastAPI(title="SentinelOps Checkout Testbed", version="0.2.1")
install_request_context(app, SERVICE_NAME)


def downstream_headers() -> dict[str, str]:
    headers = {"x-correlation-id": correlation_id_var.get()}
    inject_trace_headers(headers)
    return headers


async def raise_for_downstream(response: httpx.Response, dependency: str) -> None:
    if response.status_code < 400:
        return
    if response.status_code == 404 and dependency == "inventory-service":
        raise HTTPException(status_code=404, detail="sku not found")
    if 400 <= response.status_code < 500:
        raise HTTPException(status_code=409, detail=f"{dependency} rejected request")
    raise HTTPException(status_code=502, detail=f"{dependency} unavailable")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/ready")
async def ready() -> dict[str, object]:
    statuses: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
        for service, url in (
            ("payment-service", f"{PAYMENT_URL}/health"),
            ("inventory-service", f"{INVENTORY_URL}/health"),
        ):
            try:
                response = await client.get(url, headers=downstream_headers())
                statuses[service] = "ok" if response.status_code == 200 else "unavailable"
            except httpx.HTTPError:
                statuses[service] = "unavailable"
    if any(value != "ok" for value in statuses.values()):
        raise HTTPException(status_code=503, detail=statuses)
    return {"status": "ready", "service": SERVICE_NAME, "dependencies": statuses}


@app.post("/v1/checkout", response_model=CheckoutResponse)
async def checkout(payload: CheckoutRequest) -> CheckoutResponse:
    checkout_id = f"CHK-{uuid4().hex[:12].upper()}"
    timeout = httpx.Timeout(DOWNSTREAM_TIMEOUT_SECONDS)

    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            headers = downstream_headers()
            with new_client_span(tracer, "inventory-service", "GET"):
                inventory_response = await client.get(
                    f"{INVENTORY_URL}/v1/inventory/{payload.sku}", headers=headers
                )
            await raise_for_downstream(inventory_response, "inventory-service")
            inventory = inventory_response.json()
            if inventory["quantity"] < payload.quantity:
                raise HTTPException(status_code=409, detail="insufficient inventory")

            headers = downstream_headers()
            with new_client_span(tracer, "payment-service", "POST"):
                payment_response = await client.post(
                    f"{PAYMENT_URL}/v1/payments/charge",
                    headers=headers,
                    json={
                        "order_id": checkout_id,
                        "amount": payload.amount,
                        "currency": payload.currency,
                    },
                )
            await raise_for_downstream(payment_response, "payment-service")
            payment = payment_response.json()
    except httpx.TimeoutException as exc:
        log_event(SERVICE_NAME, "downstream_timeout", checkout_id=checkout_id)
        raise HTTPException(status_code=504, detail="downstream timeout") from exc
    except httpx.HTTPError as exc:
        log_event(SERVICE_NAME, "downstream_transport_error", checkout_id=checkout_id, error=str(exc))
        raise HTTPException(status_code=502, detail="downstream transport error") from exc

    log_event(
        SERVICE_NAME,
        "checkout_completed",
        checkout_id=checkout_id,
        payment_id=payment["payment_id"],
        sku=payload.sku,
    )
    return CheckoutResponse(
        checkout_id=checkout_id,
        payment_id=payment["payment_id"],
        sku=payload.sku,
        quantity=payload.quantity,
        status="completed",
        correlation_id=correlation_id_var.get(),
    )
