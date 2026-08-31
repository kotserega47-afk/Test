"""Child process helper for OS lock tests. Invoked via subprocess."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 5:
        raise SystemExit(2)
    os.environ["STATE_DIR"] = sys.argv[1]
    action = sys.argv[2]
    job_type = sys.argv[3]
    flag = Path(sys.argv[4])
    from core.job_runner import _try_lock, _unlock

    if action == "hold":
        ok = _try_lock(job_type)
        flag.write_text("1" if ok else "0", encoding="utf-8")
        sys.stdin.read(1)
        if ok:
            _unlock(job_type)
        return
    if action == "try":
        ok = _try_lock(job_type)
        flag.write_text("1" if ok else "0", encoding="utf-8")
        if ok:
            _unlock(job_type)
        return
    if action == "crash":
        ok = _try_lock(job_type)
        flag.write_text("1" if ok else "0", encoding="utf-8")
        os._exit(1)
    if action == "unlock":
        _unlock(job_type)
        flag.write_text("called", encoding="utf-8")
        return
    raise SystemExit(3)


if __name__ == "__main__":
    main()
