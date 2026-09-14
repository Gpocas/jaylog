"""
Identidade da máquina: hostname, username e IPv4.

São os três campos que o jaylog já enviava desde sempre. A diferença aqui é
serem **lazy**: antes, ``formatters.py`` calculava os três no import do módulo,
o que fazia um ``socket.connect(("8.8.8.8", 80))`` rodar na thread do chamador
antes de qualquer ``configure()`` — um `import jaylog` podia bloquear numa rede
lenta. Agora só quem chama ``ipv4()`` paga por isso.
"""

import getpass
import socket
from functools import lru_cache

from jaylog.host._safe import safe

UNKNOWN = "unknown"


@lru_cache(maxsize=1)
@safe(default=UNKNOWN)
def hostname() -> str:
    return socket.gethostname()


@lru_cache(maxsize=1)
@safe(default=UNKNOWN)
def username() -> str:
    return getpass.getuser()


@lru_cache(maxsize=1)
@safe(default=UNKNOWN)
def ipv4() -> str:
    """
    IP da interface usada para sair da máquina.

    O socket é UDP e ``connect()`` em UDP não gera tráfego: só escolhe a rota.
    Nada é enviado para 8.8.8.8.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]


def reset_cache() -> None:
    """Limpa os caches. Existe para os testes; não use em produção."""
    hostname.cache_clear()
    username.cache_clear()
    ipv4.cache_clear()


__all__ = ["hostname", "username", "ipv4", "reset_cache", "UNKNOWN"]
