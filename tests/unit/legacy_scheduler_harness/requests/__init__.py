"""requests stub — every HTTP call fails."""


def request(*args, **kwargs):  # noqa: ANN002, ANN003
    raise RuntimeError("requests blocked in legacy scheduler test")


get = post = put = patch = delete = head = request


class Session:
    def request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("requests.Session blocked in legacy scheduler test")

    get = post = put = patch = delete = head = request

    def close(self) -> None:
        return None
