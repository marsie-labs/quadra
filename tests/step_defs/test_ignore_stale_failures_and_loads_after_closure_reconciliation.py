import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_closure_stale.py"
TESTS = [
    f"{HARNESS.relative_to(ROOT)}::{name}"
    for name in (
        "test_delayed_failure_after_closure_reconciliation_is_ignored",
        "test_pre_closure_load_after_closure_reconciliation_is_ignored",
        "test_older_reconciliation_response_after_a_newer_one_is_ignored",
    )
]


@scenario(
    "../features/ignore_stale_failures_and_loads_after_closure_reconciliation.feature",
    "Ignore stale failures and loads after closure reconciliation",
)
def test_ignore_stale_failures_and_loads_after_closure_reconciliation():
    pass


@pytest.fixture
def context():
    return {}


@given(
    "B has reconciled closed session S to its complete authoritative card snapshot"
)
def _reconciled():
    assert "await _close_elsewhere(session_id, canvas)" in HARNESS.read_text()


@given(
    "The test is parameterized over a delayed mutation failure, a pre-closure load "
    "response and an overlapping older reconciliation response"
)
def _parameterized():
    text = HARNESS.read_text()
    assert all(f"def {test.split('::')[1]}(" in text for test in TESTS)


@given("The selected response would restore an older card position or omit cards if applied")
def _would_restore():
    text = HARNESS.read_text()
    assert "hold.result = ([card], list(quadrant_ids))" in text
    assert "cards.relocate_card(session_id, card.id, quadrant_ids[1], 3)" in text


@when("The selected stale response reaches B")
def _reaches(context):
    context["result"] = subprocess.run(  # nosec B603
        [sys.executable, "-m", "pytest", *TESTS, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@then("B's card snapshot is unchanged")
def _unchanged(context):
    result = context["result"]
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passed" in result.stdout and " skipped" not in result.stdout
    assert "assert _shown(canvas) == before" in HARNESS.read_text()


@then("No pending overlay returns")
def _no_overlay(context):
    assert context["result"].returncode == 0
    assert "_assert_no_overlay(user, canvas)" in HARNESS.read_text()


@then("B remains read-only")
def _read_only(context):
    assert context["result"].returncode == 0
    assert "_assert_read_only(user, canvas)" in HARNESS.read_text()
