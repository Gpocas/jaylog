"""``HostInfo`` -> dicionário de wire."""

from jaylog.host.models import HostInfo


def build_host_payload(service: str, info: HostInfo | None = None) -> dict:
    """
    Corpo do ``POST /logs/host``.

    ``service`` entra aqui, e não na coleta, porque o ``HostInfo`` é **um por
    processo** enquanto o POST é **um por ``app_name``**: dois loggers no mesmo
    processo mandam o mesmo retrato, com o mesmo ``run_id``, para serviços
    diferentes. (É por isso que a chave única do backend é ``(run_id, service)``
    e não ``run_id``.)

    ``mode="json"`` garante tipos JSON nativos — ``true``/``false``/``null`` de
    verdade, e não o teatro ``"true"``/``"false"`` do multipart.
    """
    if info is None:
        from jaylog.host.collectors import collect_host_info

        info = collect_host_info()

    payload = info.model_dump(mode="json")
    payload["service"] = service
    return payload


__all__ = ["build_host_payload"]
