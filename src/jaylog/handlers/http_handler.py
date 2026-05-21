import http.client
import json
import logging
import urllib.parse
import uuid
from logging.handlers import HTTPHandler

from jaylog.formatters import build_log_entry_dict


def _encode_multipart(fields: dict) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = b""
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            encoded_value = b"true" if value else b"false"
        elif isinstance(value, str):
            # Normalize to CRLF — bare \n in tracebacks breaks MIME parsing
            normalized = value.replace("\r\n", "\n").replace("\n", "\r\n")
            encoded_value = normalized.encode("utf-8")
        else:
            encoded_value = json.dumps(value).encode("utf-8")

        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n'
            f"\r\n"
        ).encode("utf-8") + encoded_value + b"\r\n"

    body += f"--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


class JaylogHttpHandler(HTTPHandler):
    """
    HTTP handler that POSTs log records as multipart/form-data to a remote endpoint.

    Extends the stdlib HTTPHandler replacing Basic Auth with x-api-key header
    authentication and sending multipart form data instead of form-encoded data.

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

            data, content_type = _encode_multipart(self.mapLogRecord(record))            
            conn.putrequest(self.method, self.url)
            conn.putheader("Content-Type", content_type)
            conn.putheader("Content-Length", str(len(data)))
            conn.putheader("x-api-key", self.api_key)
            conn.endheaders()
            conn.send(data)
            conn.getresponse()
        except Exception:
            self.handleError(record)
