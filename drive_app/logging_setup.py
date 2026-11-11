import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


def redact(text: str) -> str:
    text = re.sub(r"(?i)\bBearer\s+[^\s,\"]+", "Bearer [oculto]", text)
    text = re.sub(r'(?i)(access_token|refresh_token|client_secret|resourcekey|resource_key)(["\s:=]+)[^\s,}\"]+', r'\1\2[oculto]', text)
    text = re.sub(r'https?://[^\s\"]+', '[URL oculta]', text)
    return re.sub(r"ya29\.[\w.-]+", "[token oculto]", text)


class SecretFilter(logging.Filter):
    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        return True


def configure_logging(root: Path) -> None:
    logger = logging.getLogger("drive_app")
    if logger.handlers:
        return
    handler = RotatingFileHandler(root / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler.addFilter(SecretFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
