import base64
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

    def __init__(self, endpoint: str, api_key: str, timeout: float = 5.0, proxy: str | None = None) -> None:
        parsed = urllib.parse.urlparse(endpoint)
        secure = parsed.scheme == "https"
        host = parsed.netloc
        path = parsed.path or "/"

        super().__init__(host=host, url=path, method="POST", secure=secure)

        self.api_key = api_key
        self.timeout = timeout
        self._proxy = urllib.parse.urlparse(proxy) if proxy else None

    def mapLogRecord(self, record: logging.LogRecord) -> dict:
        return build_log_entry_dict(record)

    def _proxy_auth_header(self) -> str | None:
        if not self._proxy or not self._proxy.username:
            return None
        credentials = f"{self._proxy.username}:{self._proxy.password or ''}"
        return "Basic " + base64.b64encode(credentials.encode()).decode()

    def _build_connection(self) -> http.client.HTTPConnection:
        if self._proxy:
            proxy_host = self._proxy.hostname
            proxy_port = self._proxy.port
            if self.secure:
                conn = http.client.HTTPSConnection(proxy_host, proxy_port, timeout=self.timeout)
                tunnel_headers = {}
                auth = self._proxy_auth_header()
                if auth:
                    tunnel_headers["Proxy-Authorization"] = auth
                conn.set_tunnel(self.host, headers=tunnel_headers)
            else:
                conn = http.client.HTTPConnection(proxy_host, proxy_port, timeout=self.timeout)
            return conn

        if self.secure:
            return http.client.HTTPSConnection(self.host, timeout=self.timeout)
        return http.client.HTTPConnection(self.host, timeout=self.timeout)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            conn = self._build_connection()
            data, content_type = _encode_multipart(self.mapLogRecord(record))

            # For plain-HTTP through proxy, use the absolute URI as request target
            url = f"http://{self.host}{self.url}" if self._proxy and not self.secure else self.url

            conn.putrequest(self.method, url)
            if self._proxy and not self.secure:
                auth = self._proxy_auth_header()
                if auth:
                    conn.putheader("Proxy-Authorization", auth)
            conn.putheader("Content-Type", content_type)
            conn.putheader("Content-Length", str(len(data)))
            conn.putheader("x-api-key", self.api_key)
            conn.endheaders()
            conn.send(data)
            conn.getresponse()
        except Exception:
            self.handleError(record)
