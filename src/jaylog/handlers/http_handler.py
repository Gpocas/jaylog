from enum import verify
import json
import logging
from urllib.parse import quote_plus, urlparse, urlunparse

import requests

from jaylog.formatters import build_log_entry_dict


def _encode_proxy(proxy: str, user: str | None, password: str | None) -> str:
    if not user:
        return proxy
    parsed = urlparse(proxy)
    credentials = f"{quote_plus(user)}:{quote_plus(password or '')}"
    netloc = f"{credentials}@{parsed.netloc}"
    return urlunparse(parsed._replace(netloc=netloc))


def _to_multipart(fields: dict) -> dict:
    result = {}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            str_value = "true" if value else "false"
        elif not isinstance(value, str):
            str_value = json.dumps(value)
        else:
            str_value = value
        result[key] = (None, str_value)
    return result


class JaylogHttpHandler(logging.Handler):
    """
    HTTP handler that POSTs log records as multipart/form-data to a remote endpoint.

    Non-blocking behaviour is guaranteed by the QueueListener that drives this
    handler — emit() runs in the listener's background thread.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        timeout: float = 5.0,
        proxy: str | None = None,
        proxy_user: str | None = None,
        proxy_password: str | None = None,
    ) -> None:
        super().__init__()
        self.endpoint = endpoint
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers["x-api-key"] = api_key
        if proxy:
            encoded = _encode_proxy(proxy, proxy_user, proxy_password)
            self._session.proxies = {"http": encoded, "https": encoded}

    def mapLogRecord(self, record: logging.LogRecord) -> dict:
        return build_log_entry_dict(record)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._session.post(
                self.endpoint,
                files=_to_multipart(self.mapLogRecord(record)),
                timeout=self.timeout,
                verify=False
            )
        except Exception:
            self.handleError(record)
