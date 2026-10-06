from __future__ import annotations

import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from services.common.faults import FaultRequest, FaultState
from services.common.runtime import install_request_context, log_event

SERVICE_NAME = os.getenv("SERVICE_NAME", "inventory-service")
DATABASE_URL = os.environ["DATABASE_URL"]
fault = FaultState()


class InventoryResponse(BaseModel):
    sku: str
    name: str
    quantity: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    try:
        yield
    finally:
        await app.state.db.close()


app = FastAPI(title="SentinelOps Inventory Testbed", version="0.1.0", lifespan=lifespan)
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


@app.get("/v1/inventory/{sku}", response_model=InventoryResponse)
async def get_inventory(sku: str, request: Request) -> InventoryResponse:
    await fault.apply(SERVICE_NAME)
    async with request.app.state.db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT sku, name, quantity FROM inventory WHERE sku = $1",
            sku,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="sku not found")

    log_event(SERVICE_NAME, "inventory_read", sku=sku, quantity=row["quantity"])
    return InventoryResponse(sku=row["sku"], name=row["name"], quantity=row["quantity"])


@app.post("/internal/faults")
async def set_fault(payload: FaultRequest) -> dict[str, object]:
    fault.update(payload)
    log_event(SERVICE_NAME, "fault_configuration_changed", **fault.snapshot())
    return {"service": SERVICE_NAME, **fault.snapshot()}


@app.get("/internal/faults")
async def get_fault() -> dict[str, object]:
    return {"service": SERVICE_NAME, **fault.snapshot()}
