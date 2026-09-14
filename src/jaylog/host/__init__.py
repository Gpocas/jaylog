# Intencionalmente vazio.
#
# `settings.py` precisa de `host.identity` sem arrastar `requests` (via
# `reporter`) nem `subprocess` (via `detect_git`). Re-exportar os submódulos
# aqui recriaria esse acoplamento no import. Importe sempre o submódulo
# explícito: `from jaylog.host import identity`.
