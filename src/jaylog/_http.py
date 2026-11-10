"""
Transporte HTTP compartilhado pelos clientes do jaylog (log, host, métricas,
heartbeat e agendas): verificação TLS, proxy, headers de protocolo e o aviso do
urllib3 sobre requisições não verificadas.

Tudo que diz respeito a ``verify`` mora aqui para que a troca do padrão (0.4.0)
aconteça num único lugar.
"""

import re
import threading
import warnings
from pathlib import Path
from urllib.parse import urlsplit

import requests
import urllib3

from jaylog._version import PROTOCOL_VERSION, __version__
from jaylog.runtime import RUN_ID

#: ``False`` na 0.3.x por compatibilidade (ver ``JaylogSettings.log_http_verify``);
#: passa a ser ``True`` na 0.4.0. É o padrão de *todos* os clientes HTTP.
DEFAULT_VERIFY: bool = False

# Os mesmos conjuntos que o pydantic aceita para ``bool``.
_TRUE_VALUES = frozenset({"1", "on", "t", "true", "y", "yes"})
_FALSE_VALUES = frozenset({"0", "off", "f", "false", "n", "no"})


def parse_verify(value: object) -> bool | str:
    """
    Normaliza o valor de ``log_http_verify``.

    Vindo de variável de ambiente tudo é texto, e o ``requests`` trata qualquer
    ``str`` como caminho de CA bundle: ``"true"`` virava
    ``OSError: invalid path: true`` em toda requisição. Aqui os textos booleanos
    viram ``bool`` e o resto precisa ser um caminho existente (arquivo PEM ou
    diretório preparado com ``c_rehash``), para que o erro apareça na
    configuração e não em silêncio no envio.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, Path):
        value = str(value)
    if not isinstance(value, str):
        raise ValueError(
            f"log_http_verify deve ser true, false ou o caminho de um CA bundle; "
            f"recebido {type(value).__name__}"
        )

    text = value.strip()
    if text.lower() in _TRUE_VALUES:
        return True
    if text.lower() in _FALSE_VALUES:
        return False
    if not text:
        raise ValueError("log_http_verify vazio; use true, false ou o caminho de um CA bundle")

    path = Path(text).expanduser()
    if not path.exists():
        raise ValueError(f"log_http_verify: CA bundle não encontrado: {path}")
    return str(path)


def jaylog_headers(api_key: str) -> dict[str, str]:
    """Headers de autenticação e protocolo enviados em todo POST do jaylog."""
    return {
        "x-api-key": api_key,
        "x-jaylog-version": __version__,
        "x-jaylog-protocol": str(PROTOCOL_VERSION),
        "x-jaylog-run-id": RUN_ID,
    }


def proxies_for(proxy: str | None) -> dict[str, str] | None:
    """Mapa de proxies do ``requests`` para um único proxy (ou ``None``)."""
    return {"http": proxy, "https": proxy} if proxy else None


def prepare_session(api_key: str, proxy: str | None = None, session=None):
    """
    Sessão com os headers do jaylog e o proxy já aplicados.

    ``verify`` **não** é fixado na sessão de propósito: com ``session.verify``
    o ``requests`` deixa ``REQUESTS_CA_BUNDLE`` sobrepor um ``False`` explícito.
    Os clientes passam ``verify=`` em cada ``post()``.
    """
    if session is None:
        session = requests.Session()
    session.headers.update(jaylog_headers(api_key))
    proxies = proxies_for(proxy)
    if proxies:
        session.proxies.update(proxies)
    return session


_silenced_hosts: set[str] = set()
_silence_lock = threading.Lock()


def silence_insecure_warning(endpoint: str | None, verify: bool | str) -> None:
    """
    Silencia o ``InsecureRequestWarning`` do urllib3 só para o host do endpoint,
    e só quando a verificação está desligada.

    Substitui o ``warnings.catch_warnings()`` que envolvia cada POST: ele troca
    ``warnings.filters`` do processo inteiro e não é thread-safe — com cinco
    threads do jaylog fazendo isso ao mesmo tempo, os filtros da aplicação
    podiam ser perdidos ou ficar com o "ignore" vazado. Aqui é um filtro
    permanente, instalado uma vez, restrito à mensagem do host do jaylog: o
    aviso continua valendo para qualquer outro destino da aplicação.

    Chamado na construção dos clientes, não no envio.
    """
    if verify is not False or not endpoint:
        return
    host = urlsplit(endpoint).hostname
    if not host:
        return
    with _silence_lock:
        if host in _silenced_hosts:
            return
        _silenced_hosts.add(host)
        warnings.filterwarnings(
            "ignore",
            message=re.escape(f"Unverified HTTPS request is being made to host '{host}'"),
            category=urllib3.exceptions.InsecureRequestWarning,
        )


__all__ = [
    "DEFAULT_VERIFY",
    "jaylog_headers",
    "parse_verify",
    "prepare_session",
    "proxies_for",
    "silence_insecure_warning",
]
