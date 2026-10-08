from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from services.common.faults import FaultRequest, FaultState
from services.common.runtime import install_request_context, log_event

SERVICE_NAME = os.getenv("SERVICE_NAME", "payment-service")
DATABASE_URL = os.environ["DATABASE_URL"]
fault = FaultState()


class ChargeRequest(BaseModel):
    order_id: str = Field(min_length=1, max_length=100)
    amount: float = Field(gt=0, le=1_000_000)
    currency: str = Field(default="USD", min_length=3, max_length=3)


class ChargeResponse(BaseModel):
    payment_id: str
    order_id: str
    status: str
    amount: float
    currency: str
    created_at: datetime


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    try:
        yield
    finally:
        await app.state.db.close()


app = FastAPI(title="SentinelOps Payment Testbed", version="0.2.1", lifespan=lifespan)
install_request_context(app, SERVICE_NAME)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/ready")
async def ready(request: Request) -> dict[str, str]:
    try:
        async with request.app.state.db.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ready", "service": SERVICE_NAME}


@app.post("/v1/payments/charge", response_model=ChargeResponse)
async def charge(payload: ChargeRequest, request: Request) -> ChargeResponse:
    await fault.apply(SERVICE_NAME)
    payment_id = f"PAY-{uuid4().hex[:12].upper()}"
    created_at = datetime.now(timezone.utc)

    async with request.app.state.db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO payments(payment_id, order_id, amount, currency, status, created_at)
            VALUES($1, $2, $3, $4, $5, $6)
            """,
            payment_id,
            payload.order_id,
            payload.amount,
            payload.currency.upper(),
            "approved",
            created_at,
        )

    log_event(SERVICE_NAME, "payment_approved", payment_id=payment_id, order_id=payload.order_id)
    return ChargeResponse(
        payment_id=payment_id,
        order_id=payload.order_id,
        status="approved",
        amount=payload.amount,
        currency=payload.currency.upper(),
        created_at=created_at,
    )


@app.post("/internal/faults")
async def set_fault(payload: FaultRequest) -> dict[str, object]:
    fault.update(payload)
    log_event(SERVICE_NAME, "fault_configuration_changed", **fault.snapshot())
    return {"service": SERVICE_NAME, **fault.snapshot()}


@app.get("/internal/faults")
async def get_fault() -> dict[str, object]:
    return {"service": SERVICE_NAME, **fault.snapshot()}
