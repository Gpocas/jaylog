"""
Identidade desta execução do processo.

Um ``RUN_ID`` por processo — não por logger. Ele correlaciona o registro de
host (``POST /logs/host``) com todos os logs emitidos pela mesma execução
(header ``x-jaylog-run-id``).

Zero dependências e zero I/O: este módulo é importado por qualquer caminho do
jaylog e não pode custar nada nem falhar.
"""

import uuid
from datetime import datetime, timezone

#: UUID4 gerado no import do jaylog.
RUN_ID: str = str(uuid.uuid4())

#: Momento (UTC, ISO-8601) em que o processo carregou o jaylog.
STARTED_AT: str = datetime.now(timezone.utc).isoformat()

__all__ = ["RUN_ID", "STARTED_AT"]
