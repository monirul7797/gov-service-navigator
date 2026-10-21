"""Engine HTTP app. In M0 it only answers the shared health contract."""
from __future__ import annotations

import os

from fastapi import FastAPI

SERVICE_NAME = "engine"


def create_app(version: str) -> FastAPI:
    app = FastAPI(title="GSN engine", version=version)

    # async on purpose: the health check must not wait for a free worker thread.
    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": SERVICE_NAME, "version": version}

    return app


# uvicorn entry point: ``uvicorn gsn.engine.app:app``
app = create_app(os.environ.get("GSN_VERSION") or "dev")
