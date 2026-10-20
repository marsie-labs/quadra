import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
TEST = "tests/integration/test_canvas_closure.py::test_closure_dominates_stale_async_results"
HARNESS = ROOT / TEST.split("::")[0]


@scenario(
    "../features/enforce_monotonic_closed_canvas_reconciliation.feature",
    "Enforce monotonic closed-canvas reconciliation",
)
def test_enforce_monotonic_closed_canvas_reconciliation():
    pass


@pytest.fixture
def context():
    return {}


@given(
    "The canvas test harness can pause mutation responses, paginate loads and reorder "
    "session/card notifications"
)
def _harness():
    text = HARNESS.read_text()
    assert "async def test_closure_dominates_stale_async_results" in text
    assert 'monkeypatch.setattr(cards, "_PAGE_SIZE", 1)' in text  # paginated loads
    assert "held.release.set()" in text  # paused mutation responses
    assert "def _replay_reordered(" in text  # reordered notifications


@given(
    "Fixtures include pre-closure commits, rejected pending operations and closure during "
    "initial loading"
)
def _fixtures():
    text = HARNESS.read_text()
    for situation in (
        "_pre_closure_commit",
        "_rejected_pending_operation",
        "_closure_during_initial_load",
    ):
        assert f"async def {situation}(" in text
        assert f"        {situation},\n" in text


@when(f"The test {TEST} runs")
def _run(context):
    context["result"] = subprocess.run(  # nosec B603
        [sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@then("It passes")
def _passes(context):
    result = context["result"]
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout and " skipped" not in result.stdout
