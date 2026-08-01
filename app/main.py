"""Точка входа HTTP API."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import budget, imports, stats, transactions
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

settings = get_settings()

app = FastAPI(title="Finance Helper API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if not settings.is_production else [settings.webapp_url],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stats.router)
app.include_router(transactions.router)
app.include_router(budget.router)
app.include_router(imports.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
