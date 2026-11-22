"""Service-layer errors; the router maps them to HTTP status codes."""

from __future__ import annotations


class ForbiddenError(Exception):
    """The actor is not allowed to do this (HTTP 403)."""


class NotFoundError(Exception):
    """A user, listing or application does not exist (HTTP 404)."""
