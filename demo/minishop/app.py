"""Five-role MiniShop: real HTTP calls, private expiring controls and OTLP spans."""

from __future__ import annotations

import asyncio
import hmac
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, Header, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from pydantic import BaseModel, Field

ROLES = {"storefront", "checkout", "payment", "catalog", "inventory"}
PRODUCTS = {"sku-01", "sku-02", "sku-03", "sku-04"}
ROLE = os.environ.get("SERVICE_ROLE", "")
if ROLE not in ROLES:
    raise RuntimeError("MiniShop role is invalid")
CONTROL_TOKEN_FILE = Path(
    os.environ.get("CONTROL_TOKEN_FILE", "/run/secrets/capture_control_token")
)
OTLP_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://collector:4318/v1/traces"
)
if OTLP_ENDPOINT != "http://collector:4318/v1/traces":
    raise RuntimeError("MiniShop exporter endpoint must be the private Collector")

provider = TracerProvider(
    resource=Resource.create(
        {
            "service.name": ROLE,
            "service.namespace": "minishop",
            "deployment.environment.name": "local-capture",
        }
    ),
    sampler=ALWAYS_ON,
)
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(endpoint=OTLP_ENDPOINT, timeout=5),
        schedule_delay_millis=250,
        max_queue_size=4096,
        max_export_batch_size=256,
    )
)
trace.set_tracer_provider(provider)
HTTPXClientInstrumentor().instrument(tracer_provider=provider)

app = FastAPI(title=f"MiniShop {ROLE}", docs_url=None, redoc_url=None, openapi_url=None)
FastAPIInstrumentor.instrument_app(
    app,
    tracer_provider=provider,
    excluded_urls=r".*/health,.*/__control,.*/__state,.*/__flush",
)


class Purchase(BaseModel):
    product_id: Literal["sku-01", "sku-02", "sku-03", "sku-04"]
    quantity: int = Field(ge=1, le=5)


class Control(BaseModel):
    mode: Literal["healthy", "payment_error", "inventory_delay", "catalog_error", "caller_timeout"]
    ttl_seconds: float = Field(ge=1, le=900)


@dataclass
class FaultState:
    mode: str = "healthy"
    expires_at: float = 0.0
    requests: int = 0

    def active(self) -> str:
        if self.expires_at and time.monotonic() >= self.expires_at:
            self.mode = "healthy"
            self.expires_at = 0.0
        return self.mode

    def set(self, mode: str, ttl: float) -> None:
        self.mode = mode
        self.expires_at = time.monotonic() + ttl if mode != "healthy" else 0.0
        self.requests = 0

    def alternate_failure(self) -> bool:
        self.requests += 1
        return self.requests % 2 == 0


state = FaultState()
randomizer = random.Random(int.from_bytes(ROLE.encode(), "big"))


def _authorize(token: str | None, *, allow_storefront: bool = False) -> None:
    if (ROLE == "storefront" and not allow_storefront) or not CONTROL_TOKEN_FILE.is_file():
        raise HTTPException(404)
    expected = CONTROL_TOKEN_FILE.read_text().strip()
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(403)


async def _work() -> None:
    await asyncio.sleep(randomizer.uniform(0.005, 0.03))


async def _request(
    method: str, url: str, *, payload: Purchase | None = None, timeout: float = 2.0
) -> httpx.Response:
    if not url.startswith(
        (
            "http://checkout:8000/",
            "http://payment:8000/",
            "http://catalog:8000/",
            "http://inventory:8000/",
        )
    ):
        raise RuntimeError("MiniShop dependency URL is not allowlisted")
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, trust_env=False
    ) as client:
        return await client.request(
            method,
            url,
            json=payload.model_dump() if payload is not None else None,
        )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "role": ROLE}


@app.post("/__control")
async def control(
    body: Control, x_capture_token: str | None = Header(default=None)
) -> dict[str, str]:
    _authorize(x_capture_token)
    allowed = {
        "checkout": {"healthy", "caller_timeout"},
        "payment": {"healthy", "payment_error"},
        "catalog": {"healthy", "catalog_error"},
        "inventory": {"healthy", "inventory_delay"},
    }
    if body.mode not in allowed[ROLE]:
        raise HTTPException(400)
    state.set(body.mode, body.ttl_seconds)
    return {"status": "set", "mode": state.active()}


@app.get("/__state")
async def control_state(x_capture_token: str | None = Header(default=None)) -> dict[str, str]:
    _authorize(x_capture_token)
    return {"mode": state.active()}


@app.post("/__flush")
async def flush(x_capture_token: str | None = Header(default=None)) -> dict[str, bool]:
    _authorize(x_capture_token, allow_storefront=True)
    return {"flushed": provider.force_flush(timeout_millis=5000)}


@app.post("/buy")
async def buy(purchase: Purchase) -> dict[str, object]:
    if ROLE != "storefront":
        raise HTTPException(404)
    await _work()
    try:
        result = await _request(
            "POST", "http://checkout:8000/checkout", payload=purchase, timeout=3.0
        )
    except httpx.TimeoutException:
        raise HTTPException(504) from None
    if result.status_code >= 400:
        raise HTTPException(502)
    return {"accepted": True, "product_id": purchase.product_id}


@app.post("/checkout")
async def checkout(purchase: Purchase) -> dict[str, bool]:
    if ROLE != "checkout":
        raise HTTPException(404)
    await _work()
    payment_timeout = 0.01 if state.active() == "caller_timeout" else 2.0
    payment, catalog = await asyncio.gather(
        _request(
            "POST", "http://payment:8000/authorize", payload=purchase, timeout=payment_timeout
        ),
        _request("GET", f"http://catalog:8000/quote?product_id={purchase.product_id}", timeout=2.0),
        return_exceptions=True,
    )
    if isinstance(payment, httpx.TimeoutException):
        raise HTTPException(504)
    if isinstance(payment, BaseException) or isinstance(catalog, BaseException):
        raise HTTPException(502)
    if payment.status_code >= 400 or catalog.status_code >= 400:
        raise HTTPException(502)
    return {"approved": True}


@app.post("/authorize")
async def authorize(purchase: Purchase) -> dict[str, bool]:
    if ROLE != "payment":
        raise HTTPException(404)
    await _work()
    if state.active() == "payment_error" and state.alternate_failure():
        raise HTTPException(500)
    return {"approved": True}


@app.get("/quote")
async def quote(product_id: str) -> dict[str, object]:
    if ROLE != "catalog" or product_id not in PRODUCTS:
        raise HTTPException(404)
    await _work()
    if state.active() == "catalog_error" and state.alternate_failure():
        raise HTTPException(500)
    try:
        result = await _request(
            "GET", f"http://inventory:8000/availability?product_id={product_id}", timeout=2.0
        )
    except httpx.TimeoutException:
        raise HTTPException(504) from None
    if result.status_code >= 400:
        raise HTTPException(502)
    return {"product_id": product_id, "unit_price": 100, "available": True}


@app.get("/availability")
async def availability(product_id: str) -> dict[str, bool]:
    if ROLE != "inventory" or product_id not in PRODUCTS:
        raise HTTPException(404)
    await _work()
    if state.active() == "inventory_delay":
        await asyncio.sleep(0.8)
    return {"available": True}
