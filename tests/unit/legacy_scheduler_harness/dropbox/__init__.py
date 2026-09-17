"""dropbox SDK stub — no network."""

from __future__ import annotations


class ApiError(Exception):
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        super().__init__(*args)
        self.error = kwargs.get("error")


class Dropbox:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("dropbox client blocked in legacy scheduler test")


class files:
    class DownloadError:
        def is_path(self) -> bool:
            return False
