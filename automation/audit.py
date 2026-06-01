from dataclasses import dataclass

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES


def _mk(profile_key: str):
    icon, name = LOG_PROFILES[profile_key]
    return get_logger(name, icon)


log = _mk("AUTOMATION")


@dataclass
class Stats:
    ok: int = 0
    fail: int = 0
    skip: int = 0

    def inc(self, result: str) -> None:
        result = (result or "").strip().lower()

        if result.startswith("skip"):
            self.skip += 1
        elif (
                result.startswith("set")
                or "set_status" in result
                or "removed" in result
                or "added" in result
                or result == "saved"
        ):
            self.ok += 1
        else:
            self.fail += 1

    def summary(self) -> str:
        return f"OK={self.ok} | FAIL={self.fail} | SKIP={self.skip}"
