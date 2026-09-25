"""
Derivação das URLs de host e de métricas a partir da URL do endpoint de log.

Deploys existentes já configuram ``JAYLOG_LOG_HTTP_ENDPOINT``; trocar o último
segmento do path (``/logs/add`` -> ``/logs/host``, ``/logs/host-metrics``) evita
exigir uma variável nova de todo mundo. Quem precisar de outro caminho usa
``JAYLOG_HOST_HTTP_ENDPOINT`` / ``JAYLOG_HOST_METRICS_HTTP_ENDPOINT``.
"""

from urllib.parse import urlsplit, urlunsplit


def derive_endpoint(log_endpoint: str, segment: str) -> str:
    """
    Troca o último segmento do path: ``(https://api/logs/add, "host")`` ->
    ``https://api/logs/host``.

    Query string e fragmento são preservados; barra final é ignorada. Uma URL
    sem path (``https://api``) vira ``https://api/<segment>``.
    """
    parts = urlsplit(log_endpoint)
    head, sep, _last = parts.path.rstrip("/").rpartition("/")
    path = f"{head}/{segment}" if sep else f"/{segment}"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def derive_host_endpoint(log_endpoint: str) -> str:
    """``https://api/logs/add`` -> ``https://api/logs/host``."""
    return derive_endpoint(log_endpoint, "host")


__all__ = ["derive_endpoint", "derive_host_endpoint"]
