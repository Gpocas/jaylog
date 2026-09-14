from jaylog._version import PROTOCOL_VERSION, __version__
from jaylog.logger import configure, get_logger, shutdown
from jaylog.settings import JaylogSettings

__all__ = [
    "configure",
    "get_logger",
    "shutdown",
    "JaylogSettings",
    "__version__",
    "PROTOCOL_VERSION",
]

_DEPRECATED = {"LogEntry"}


def __getattr__(name: str):
    # `jaylog.models.LogEntry` era código morto — nunca foi construído, e o tipo
    # declarado nem batia com o que o handler HTTP envia. Fica acessível por um
    # release para não quebrar `from jaylog import LogEntry` em código de bot.
    if name in _DEPRECATED:
        import warnings

        warnings.warn(
            f"jaylog.{name} está depreciado e será removido na 0.4.0; "
            "o modelo nunca foi usado pela biblioteca.",
            DeprecationWarning,
            stacklevel=2,
        )
        from jaylog import _compat

        return getattr(_compat, name)
    raise AttributeError(f"module 'jaylog' has no attribute '{name}'")


def __dir__() -> list[str]:
    return sorted([*__all__, *_DEPRECATED])
