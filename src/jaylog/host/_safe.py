"""
``@safe``: nenhum coletor de host pode derrubar a aplicação.

A coleta é observabilidade — se a leitura do Toolhelp falhar, se o antivírus
bloquear o ``git``, se o ``pyvenv.cfg`` estiver corrompido, o campo vira
``None`` e o resto do payload segue. O contrato é que um coletor **nunca**
levanta.
"""

import copy
import functools
import os
import sys
import traceback
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def _debug_enabled() -> bool:
    return os.environ.get("JAYLOG_DEBUG") == "1"


def safe(default: Any = None) -> Callable[[F], F]:
    """
    Devolve ``default`` quando a função levanta.

    Captura ``Exception``, **não** ``BaseException``: engolir ``KeyboardInterrupt``
    numa thread de coleta esconderia o Ctrl-C do usuário, e ``SystemExit`` tem
    que continuar subindo.

    O traceback só é impresso em stderr com ``JAYLOG_DEBUG=1`` — em produção a
    falha é silenciosa por desenho, já que o payload carrega a evidência
    (o campo fica ``None``).
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception:
                if _debug_enabled():
                    print(f"[jaylog] host: falha em {func.__name__}", file=sys.stderr)
                    traceback.print_exc(file=sys.stderr)
                # copy para que um default mutável (`{}`, `[]`) não seja
                # compartilhado entre chamadas.
                return copy.copy(default)

        return wrapper  # type: ignore[return-value]

    return decorator


__all__ = ["safe"]
