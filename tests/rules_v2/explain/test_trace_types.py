"""Tests for C11.1 explain trace contract types."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.rules_v2.explain.types import (
    OP_RESOLVE_LIMIT_RULE,
    OP_RESOLVE_PARTNER,
    PHASE_INDEX_LOOKUP,
    PHASE_INPUT_NORMALIZED,
    PHASE_RESULT,
    ResolutionTraceStep,
    TraceSink,
    trace_step_to_jsonable,
    trace_steps_to_jsonable,
)


def _sample_step(*, result_ref: str | None = "pk_108") -> ResolutionTraceStep:
    return ResolutionTraceStep(
        op=OP_RESOLVE_PARTNER,
        phase=PHASE_INDEX_LOOKUP,
        outcome="hit",
        inputs={"raw": "X (108)", "expect_raw_label": True},
        candidates=(
            {"scope_type": "partner", "scope_key": "a"},
            {"scope_type": "partner", "scope_key": "b"},
        ),
        index_key=("wallet", "partner", "pk", "daily_max_amount", "uni"),
        result_ref=result_ref,
    )


def test_trace_sink_emit_collects() -> None:
    class _ListSink:
        __slots__ = ("items",)

        def __init__(self) -> None:
            self.items: list[ResolutionTraceStep] = []

        def emit(self, step: ResolutionTraceStep, /) -> None:
            self.items.append(step)

    sink: TraceSink = _ListSink()
    sink.emit(_sample_step())
    assert len(sink.items) == 1  # type: ignore[attr-defined]


def test_trace_step_to_jsonable_fully_json_dumps_serializable() -> None:
    d = trace_step_to_jsonable(_sample_step())
    json.dumps(d, ensure_ascii=False)
    assert json.loads(json.dumps(d)) == d


def test_resolution_trace_step_serializes_json_safe() -> None:
    step = _sample_step()
    d = trace_step_to_jsonable(step)
    json.dumps(d)
    assert d["op"] == OP_RESOLVE_PARTNER
    assert d["phase"] == PHASE_INDEX_LOOKUP
    assert d["outcome"] == "hit"
    assert d["inputs"]["raw"] == "X (108)"
    assert d["result_ref"] == "pk_108"


def test_trace_steps_to_jsonable_fully_json_dumps_serializable() -> None:
    out = trace_steps_to_jsonable([_sample_step(), _sample_step(result_ref=None)])
    json.dumps(out, ensure_ascii=False)
    assert json.loads(json.dumps(out)) == out


def test_index_key_tuple_becomes_list() -> None:
    step = _sample_step()
    d = trace_step_to_jsonable(step)
    assert d["index_key"] == ["wallet", "partner", "pk", "daily_max_amount", "uni"]
    assert isinstance(d["index_key"], list)


def test_candidates_order_preserved() -> None:
    step = _sample_step()
    d = trace_step_to_jsonable(step)
    assert [c["scope_key"] for c in d["candidates"]] == ["a", "b"]


def test_result_ref_can_be_none() -> None:
    step = _sample_step(result_ref=None)
    d = trace_step_to_jsonable(step)
    assert d["result_ref"] is None


def test_no_datetime_in_constructor_path() -> None:
    step = ResolutionTraceStep(
        op=OP_RESOLVE_LIMIT_RULE,
        phase=PHASE_RESULT,
        outcome="miss",
        inputs={"job_key": "hourly"},
        candidates=(),
        index_key=None,
        result_ref=None,
    )
    d = trace_step_to_jsonable(step)
    assert "datetime" not in json.dumps(d)


def test_dataclass_is_frozen() -> None:
    step = _sample_step()
    with pytest.raises(AttributeError):
        step.outcome = "miss"  # type: ignore[misc]


def test_trace_steps_to_jsonable_order() -> None:
    a = ResolutionTraceStep(
        op=OP_RESOLVE_PARTNER,
        phase=PHASE_INPUT_NORMALIZED,
        outcome="skip",
        inputs={},
        candidates=(),
        index_key=None,
        result_ref=None,
    )
    b = ResolutionTraceStep(
        op=OP_RESOLVE_LIMIT_RULE,
        phase=PHASE_RESULT,
        outcome="miss",
        inputs={},
        candidates=(),
        index_key=("a", "b"),
        result_ref=None,
    )
    out = trace_steps_to_jsonable((a, b))
    assert len(out) == 2
    assert out[0]["op"] != out[1]["op"]


def test_types_module_import_does_not_load_pandas_openpyxl() -> None:
    repo = Path(__file__).resolve().parents[3]
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(repo)!r})\n"
        "import core.rules_v2.explain.types as t\n"
        "assert 'pandas' not in sys.modules\n"
        "assert 'openpyxl' not in sys.modules\n"
        "print('ok')\n"
    )
    subprocess.check_call([sys.executable, "-c", code], cwd=str(repo))


def test_package_import_does_not_load_rules_provider() -> None:
    repo = Path(__file__).resolve().parents[3]
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(repo)!r})\n"
        "import core.rules_v2.explain as ex\n"
        "assert 'core.rules_provider' not in sys.modules\n"
        "assert 'core.rules_v2.ops_rules_validate_summary' not in sys.modules\n"
        "print('ok')\n"
    )
    subprocess.check_call([sys.executable, "-c", code], cwd=str(repo))
