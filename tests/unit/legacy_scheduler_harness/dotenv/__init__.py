"""python-dotenv stub: never load a real .env file."""


def load_dotenv(*args, **kwargs):  # noqa: ANN002, ANN003
    return False


def find_dotenv(*args, **kwargs):  # noqa: ANN002, ANN003
    return ""


def dotenv_values(*args, **kwargs):  # noqa: ANN002, ANN003
    return {}
