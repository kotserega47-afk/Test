"""Explicit Telegram stub for registration subprocess tests."""

from __future__ import annotations


class Update:
    pass


class InputFile:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.args = args
        self.kwargs = kwargs


class InlineKeyboardButton:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.args = args
        self.kwargs = kwargs


class InlineKeyboardMarkup:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.args = args
        self.kwargs = kwargs


class Bot:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.args = args
        self.kwargs = kwargs

    async def send_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("telegram.Bot.send_message blocked in registration test")
