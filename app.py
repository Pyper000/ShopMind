"""Vercel entrypoint for the ShopMind FastAPI application."""

from app.presentation.server import app

__all__ = ["app"]
