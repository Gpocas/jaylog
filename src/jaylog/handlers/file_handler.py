import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from jaylog.formatters import PlainTextFormatter


class JaylogFileHandler(RotatingFileHandler):
    """
    Rotating file handler that:
    - rotates when the file reaches `maxBytes` (default 5 MB)
    - deletes backup files older than `retention_days` (default 7) after each rollover
    """

    def __init__(
        self,
        filename: Path,
        max_bytes: int = 5 * 1024 * 1024,
        backup_count: int = 20,
        retention_days: int = 7,
        encoding: str = "utf-8",
        show_service: bool = True,
    ) -> None:
        filename.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(
            filename,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding=encoding,
        )
        self.retention_days = retention_days
        self.setFormatter(PlainTextFormatter(show_service=show_service))

    def doRollover(self) -> None:
        super().doRollover()
        self._purge_old_logs()

    def _purge_old_logs(self) -> None:
        base = Path(self.baseFilename)
        cutoff = time.time() - self.retention_days * 86400

        for entry in base.parent.iterdir():
            if not entry.is_file():
                continue
            # Match rotated backups: app.log.1, app.log.2, ...
            if entry.name == base.name or not entry.name.startswith(base.name):
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError:
                pass
