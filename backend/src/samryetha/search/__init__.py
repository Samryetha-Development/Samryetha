"""Public discussion-search domain API."""

from .service import SearchService, escape_like

__all__ = ["SearchService", "escape_like"]
