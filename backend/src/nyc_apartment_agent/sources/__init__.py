"""Adapters for external data sources. Each adapter validates what it receives."""

from .clients import geosearch_client, socrata_client

__all__ = ["geosearch_client", "socrata_client"]
