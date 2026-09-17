"""psycopg stub — connect always fails."""


def connect(*args, **kwargs):  # noqa: ANN002, ANN003
    raise RuntimeError("psycopg.connect blocked in legacy scheduler test")
