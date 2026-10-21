"""FastAPI entrypoint for Vercel Services."""

from app.presentation.server import app

__all__ = ["app"]
