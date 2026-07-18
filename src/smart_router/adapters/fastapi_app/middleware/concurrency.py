"""Concurrency control middleware."""

from __future__ import annotations

import asyncio
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class ConcurrencyMiddleware(BaseHTTPMiddleware):
    """Limit concurrent requests to prevent overload."""

    def __init__(self, app, max_concurrent: int = 100):
        super().__init__(app)
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            async with self._semaphore:
                return await call_next(request)
        except asyncio.CancelledError:
            return JSONResponse(
                status_code=503,
                content={"error": "Server is overloaded, please retry later"},
            )
