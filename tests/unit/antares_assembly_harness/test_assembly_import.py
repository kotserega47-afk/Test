from __future__ import annotations

import sys

_FORBIDDEN = (
    "integrations.tg_commands",
    "integrations.raccoon_jobs",
    "integrations.raccoon_wallet_downloader",
    "integrations.raccoon_hourly_downloader",
)


def test_import_assembly_does_not_load_forbidden_modules():
    import modules.antares.assembly as assembly

    assert assembly.assemble_antares is not None
    loaded = [name for name in _FORBIDDEN if name in sys.modules]
    assert loaded == []
    from core.job_runner import JOB_REGISTRY

    assert "script_job:hello_world" not in JOB_REGISTRY
