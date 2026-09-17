"""telegram.request stub — no HTTP client."""


class HTTPXRequest:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.args = args
        self.kwargs = kwargs
