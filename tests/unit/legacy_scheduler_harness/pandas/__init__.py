"""Import-only pandas stub for scheduler.py subprocess tests."""

NaT = None
NA = None
NAType = type("NAType", (), {})


class Timestamp:
    pass


class Timedelta:
    pass


class Series:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("pandas.Series blocked in legacy scheduler test")


class DataFrame:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("pandas.DataFrame blocked in legacy scheduler test")


def to_datetime(*args, **kwargs):  # noqa: ANN002, ANN003
    raise RuntimeError("pandas.to_datetime blocked in legacy scheduler test")


def isna(value):  # noqa: ANN001
    return value is None


def notna(value):  # noqa: ANN001
    return value is not None


def concat(*args, **kwargs):  # noqa: ANN002, ANN003
    raise RuntimeError("pandas.concat blocked in legacy scheduler test")


def read_excel(*args, **kwargs):  # noqa: ANN002, ANN003
    raise RuntimeError("pandas.read_excel blocked in legacy scheduler test")


def read_csv(*args, **kwargs):  # noqa: ANN002, ANN003
    raise RuntimeError("pandas.read_csv blocked in legacy scheduler test")
