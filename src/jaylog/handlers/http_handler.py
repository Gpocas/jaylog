import http.client
import json
import logging
import urllib.parse
from logging.handlers import HTTPHandler

from jaylog.formatters import build_log_entry_dict


class JaylogHttpHandler(HTTPHandler):
    """
    HTTP handler that POSTs log records as JSON to a remote endpoint.

    Extends the stdlib HTTPHandler replacing Basic Auth with x-api-key header
    authentication and sending JSON instead of form-encoded data.

    Non-blocking behaviour is guaranteed by the QueueListener that drives this
    handler — emit() runs in the listener's background thread.
    """

    def __init__(self, endpoint: str, api_key: str, timeout: float = 5.0) -> None:
        parsed = urllib.parse.urlparse(endpoint)
        secure = parsed.scheme == "https"
        host = parsed.netloc
        path = parsed.path or "/"

        super().__init__(host=host, url=path, method="POST", secure=secure)

        self.api_key = api_key
        self.timeout = timeout

    def mapLogRecord(self, record: logging.LogRecord) -> dict:
        return build_log_entry_dict(record)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self.secure:
                conn = http.client.HTTPSConnection(self.host, timeout=self.timeout)
            else:
                conn = http.client.HTTPConnection(self.host, timeout=self.timeout)

            data = json.dumps(self.mapLogRecord(record)).encode("utf-8")

            conn.putrequest(self.method, self.url)
            conn.putheader("Host", self.host.split(":")[0])
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(data)))
            conn.putheader("x-api-key", self.api_key)
            conn.endheaders()
            conn.send(data)
            conn.getresponse()
        except Exception:
            self.handleError(record)
