"""WSGI entrypoint under canonical package namespace."""

from invest_evolution.interfaces.web.server import app

__all__ = ["app"]
