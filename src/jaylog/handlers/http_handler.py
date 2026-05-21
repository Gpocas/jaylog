import json
import logging

import requests
from requests_ntlm import HttpNtlmAuth

from jaylog.formatters import build_log_entry_dict


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
            self._session.proxies = {"http": proxy, "https": proxy}
            if proxy_user:
                self._session.auth = HttpNtlmAuth(proxy_user, proxy_password or "")

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
