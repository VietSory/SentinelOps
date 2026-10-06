from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field


FaultMode = Literal["healthy", "latency", "error"]


class FaultRequest(BaseModel):
    mode: FaultMode = "healthy"
    delay_ms: int = Field(default=0, ge=0, le=10_000)


@dataclass
class FaultState:
    mode: FaultMode = "healthy"
    delay_ms: int = 0

    def update(self, request: FaultRequest) -> None:
        self.mode = request.mode
        self.delay_ms = request.delay_ms

    def snapshot(self) -> dict[str, object]:
        return {"mode": self.mode, "delay_ms": self.delay_ms}

    async def apply(self, component: str) -> None:
        if self.mode == "latency":
            await asyncio.sleep(self.delay_ms / 1000)
        elif self.mode == "error":
            raise HTTPException(status_code=503, detail=f"injected fault in {component}")
