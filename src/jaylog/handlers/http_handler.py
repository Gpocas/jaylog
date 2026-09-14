import json
import logging
import os
import warnings

import requests
import urllib3

from jaylog._version import PROTOCOL_VERSION, __version__
from jaylog.context import record_entry, record_screenshot
from jaylog.host import reporter
from jaylog.runtime import RUN_ID

#: O backend sinaliza por header que não conhece este `run_id` — normalmente
#: porque o log correu na frente do POST de host. Ver `reporter.request_resend`.
HOST_REQUIRED_HEADER = "x-jaylog-host-required"


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

    O **corpo** do POST é byte-a-byte o mesmo da 0.2.x: ``logs`` mantém
    ``hostname``/``username``/``ipv4`` desnormalizados de forma permanente, então
    não há campo a remover. O que muda são dois headers novos
    (``x-jaylog-protocol`` e ``x-jaylog-run-id``); um backend antigo simplesmente
    os ignora.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        timeout: float = 5.0,
        proxy: str | None = None,
        verify: bool | str = False,
    ) -> None:
        super().__init__()
        self.endpoint = endpoint
        self.timeout = timeout
        self.verify = verify
        self.proxy = {"http": proxy, "https": proxy} if proxy else None
        self._session = requests.Session()
        self._session.headers["x-api-key"] = api_key
        self._session.headers["x-jaylog-version"] = __version__
        self._session.headers["x-jaylog-protocol"] = str(PROTOCOL_VERSION)
        self._session.headers["x-jaylog-run-id"] = RUN_ID

    def mapLogRecord(self, record: logging.LogRecord) -> dict:
        # Único ponto do programa que pede screenshot: é o único com para onde
        # mandá-la. Console e arquivo não capturam mais nada.
        entry = dict(record_entry(record))
        entry["log_img"] = record_screenshot(record)
        return entry

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
                    verify=self.verify,
                )
                if os.getenv("JAYLOG_HTTP_DEBUG") == "1":
                    print(response.status_code)
                    print(response.content)

            if response.headers.get(HOST_REQUIRED_HEADER) == "1":
                # O log foi aceito (202) — não há nada a reenviar aqui. O que
                # falta é o registro de ambiente, que o reporter reenvia (com
                # debounce, porque o header vem em toda linha afetada).
                reporter.request_resend(record.name)
        except Exception:
            # `handleError` respeita `logging.raiseExceptions`: barulho em dev,
            # silêncio em produção. O `except: pass` anterior era um buraco
            # negro — era por causa dele que ninguém descobria que o caminho
            # HTTP estava falhando.
            self.handleError(record)
