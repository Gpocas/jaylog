"""
Versão do pacote e versão do protocolo de ingestão.

Lookup único de ``importlib.metadata``: o valor era recalculado em
``handlers/http_handler.py`` e voltaria a ser recalculado no reporter de host.
"""

from importlib.metadata import PackageNotFoundError, version

#: Versão do contrato cliente <-> backend. Vai no header ``x-jaylog-protocol``
#: e no payload de host. Mede o rollout; não dirige lógica no backend.
#: 3: limites da máquina no payload de host + ``POST /logs/host-metrics``.
#: 4: ``POST /logs/host-schedules`` (agendas do Task Scheduler do Windows).
PROTOCOL_VERSION = 4

try:
    __version__ = version("jaylog")
except PackageNotFoundError:  # rodando do fonte, sem instalação
    __version__ = "unknown"

__all__ = ["__version__", "PROTOCOL_VERSION"]
