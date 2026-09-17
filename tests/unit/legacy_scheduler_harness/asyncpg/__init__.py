"""asyncpg stub — connect always fails."""


async def connect(*args, **kwargs):  # noqa: ANN002, ANN003
    raise RuntimeError("asyncpg.connect blocked in legacy scheduler test")
