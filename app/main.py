"""
Backward-compatible entry for fabric-only runs.

Prefer the unified app:  uvicorn main:app --reload --port 8000
"""
from main import app  # noqa: F401

__all__ = ["app"]
