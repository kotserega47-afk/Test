"""telegram.ext stub for registration dump. Does not poll."""

from __future__ import annotations

from types import SimpleNamespace


class CommandHandler:
    def __init__(self, command, callback, **kwargs):  # noqa: ANN001, ANN003
        if isinstance(command, str):
            self.commands = frozenset({command})
        else:
            self.commands = frozenset(str(c) for c in command)
        self.callback = callback
        self.kwargs = kwargs


class MessageHandler:
    def __init__(self, filters, callback, **kwargs):  # noqa: ANN001, ANN003
        self.filters = filters
        self.callback = callback
        self.kwargs = kwargs


class _DocumentFilters:
    ALL = object()


class filters:
    Document = _DocumentFilters()
    ALL = object()
    TEXT = object()
    COMMAND = object()


class ContextTypes:
    DEFAULT_TYPE = object()


class Application:
    def __init__(self) -> None:
        self.handlers = []

    def add_handler(self, handler, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        self.handlers.append(handler)
        return None

    def run_polling(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None

    @staticmethod
    def builder() -> "_Builder":
        return _Builder()


class _Builder:
    def token(self, token: str) -> "_Builder":
        self.token_value = token
        return self

    def concurrent_updates(self, enabled: bool) -> "_Builder":
        self.concurrent = enabled
        return self

    def build(self) -> Application:
        return Application()


Application.builder = staticmethod(lambda: _Builder())  # type: ignore[method-assign]

ext = SimpleNamespace(
    Application=Application,
    CommandHandler=CommandHandler,
    MessageHandler=MessageHandler,
    filters=filters,
    ContextTypes=ContextTypes,
)
