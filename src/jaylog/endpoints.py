"""
Derivação da URL do endpoint de host a partir da URL do endpoint de log.

Deploys existentes já configuram ``JAYLOG_LOG_HTTP_ENDPOINT``; trocar o último
segmento do path (``/logs/add`` -> ``/logs/host``) evita exigir uma variável
nova de todo mundo. Quem precisar de outro caminho usa
``JAYLOG_HOST_HTTP_ENDPOINT``.
"""

from urllib.parse import urlsplit, urlunsplit


def derive_host_endpoint(log_endpoint: str) -> str:
    """
    ``https://api/logs/add`` -> ``https://api/logs/host``.

    Query string e fragmento são preservados; barra final é ignorada. Uma URL
    sem path (``https://api``) vira ``https://api/host``.
    """
    parts = urlsplit(log_endpoint)
    head, sep, _last = parts.path.rstrip("/").rpartition("/")
    path = f"{head}/host" if sep else "/host"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


__all__ = ["derive_host_endpoint"]
