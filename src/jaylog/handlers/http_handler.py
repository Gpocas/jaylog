import json
import logging
import warnings

import requests
import urllib3

from jaylog.formatters import build_log_entry_dict


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
    ) -> None:
        super().__init__()
        self.endpoint = endpoint
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers["x-api-key"] = api_key

    def mapLogRecord(self, record: logging.LogRecord) -> dict:
        return build_log_entry_dict(record)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
                self._session.post(
                    self.endpoint,
                    files=_to_multipart(self.mapLogRecord(record)),
                    timeout=self.timeout,
                    verify=False,
                )
        except Exception:
            pass
