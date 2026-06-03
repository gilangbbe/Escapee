"""
ASGI entrypoint.

Run with:  uvicorn app.main:app --reload
"""

from __future__ import annotations

from app.web.server import app

__all__ = ["app"]
