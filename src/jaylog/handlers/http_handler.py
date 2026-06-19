import os
import json
import logging
import warnings
from importlib.metadata import PackageNotFoundError, version

import requests
import urllib3

from jaylog.formatters import build_log_entry_dict

try:
    _JAYLOG_VERSION = version("jaylog")
except PackageNotFoundError:
    _JAYLOG_VERSION = "unknown"



def _to_multipart(fields: dict) -> dict:
    result = {}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bytes):
            result[key] = ("screenshot.jpg", value, "image/jpeg")
        elif isinstance(value, bool):
            result[key] = (None, "true" if value else "false")
        elif not isinstance(value, str):
            result[key] = (None, json.dumps(value))
        else:
            result[key] = (None, value)
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
    ) -> None:
        super().__init__()
        self.endpoint = endpoint
        self.timeout = timeout
        self.proxy = {'http': proxy, 'https': proxy}
        self._session = requests.Session()
        self._session.headers["x-api-key"] = api_key
        self._session.headers["x-jaylog-version"] = _JAYLOG_VERSION

    def mapLogRecord(self, record: logging.LogRecord) -> dict:
        return build_log_entry_dict(record)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self.proxy:
                self._session.proxies.update(self.proxy)
            
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
                response = self._session.post(
                    self.endpoint,
                    files=_to_multipart(self.mapLogRecord(record)),
                    timeout=self.timeout,
                    verify=False,
                )
                if os.getenv('JAYLOG_HTTP_DEBUG') == 1:
                    print(response.status_code)
                    print(response.content)
        except Exception:
            pass
