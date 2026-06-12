import logging


class ExceptionFlagFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.is_exception = bool(record.exc_info and record.exc_info[0] is not None)
        return True
