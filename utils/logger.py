import logging
import os
import re

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "app.log")

# Matches Telegram Bot API path segments: /bot<token>/
_TELEGRAM_BOT_PATH_RE = re.compile(r"/bot[^/\s\"']+/")


class TelegramTokenRedactionFilter(logging.Filter):
    """Redact Telegram bot tokens from log records before any handler emits them."""

    def _redact_text(self, value: object) -> object:
        if not isinstance(value, str):
            return value
        return _TELEGRAM_BOT_PATH_RE.sub("/bot***REDACTED***/", value)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = self._redact_text(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: self._redact_text(v) for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(self._redact_text(a) for a in record.args)
            # Also scrub already-formatted extras if present.
            if hasattr(record, "message") and isinstance(record.message, str):
                record.message = self._redact_text(record.message)
        except Exception:
            # Never block logging because of redaction failures.
            pass
        return True


def _install_telegram_token_redaction(root: logging.Logger) -> None:
    filt = TelegramTokenRedactionFilter()
    # Root filter covers child loggers that propagate.
    already = any(isinstance(f, TelegramTokenRedactionFilter) for f in root.filters)
    if not already:
        root.addFilter(filt)
    for handler in root.handlers:
        if not any(isinstance(f, TelegramTokenRedactionFilter) for f in handler.filters):
            handler.addFilter(filt)


def _quiet_http_loggers() -> None:
    """Prevent httpx/httpcore from logging full request URLs (incl. bot tokens)."""
    for name in (
        "httpx",
        "httpcore",
        "httpx._client",
        "httpcore.http11",
        "httpcore.connection",
        "telegram.vendor.ptb_urllib3",
        "urllib3",
        "urllib3.connectionpool",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

_quiet_http_loggers()
_install_telegram_token_redaction(logging.getLogger())

logger = logging.getLogger(__name__)
