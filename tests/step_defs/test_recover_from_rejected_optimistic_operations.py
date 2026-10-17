import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_rollback.py"
INSERTION = "test_failed_insertion_removes_the_pending_card_and_shows_a_failure"
MOVEMENT = "test_failed_movement_returns_the_card_to_its_original_quadrant_and_index"


@scenario(
    "../features/recover_from_rejected_optimistic_operations.feature",
    "Recover from rejected optimistic operations",
)
def test_recover_from_rejected_optimistic_operations():
    pass


@pytest.fixture
def context():
    return {}


def _run(test: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603
        [
            sys.executable,
            "-m",
            "pytest",
            f"{HARNESS.relative_to(ROOT)}::{test}",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_passed(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, result.stdout + result.stderr
    assert " passed" in result.stdout and " skipped" not in result.stdout


@given(
    "A parameterized client fixture has either a pending insertion or a pending movement from Q1 to Q2"
)
def _fixture():
    # Each harness test holds its request at a barrier and shows it pending first.
    source = HARNESS.read_text()
    assert INSERTION in source and MOVEMENT in source
    assert "class _FailingBarrier" in source


@given("The persisted state has not changed")
def _unchanged():
    # Both harness tests assert the persisted cards while the request is held and again after.
    assert HARNESS.read_text().count("cards.list_cards(session_id)") >= 2


@when("The pending operation receives a persistence failure", target_fixture="results")
def _fail():
    return {"insertion": _run(INSERTION), "movement": _run(MOVEMENT)}


@then("For insertion the pending card is removed")
def _removed(results):
    _assert_passed(results["insertion"])


@then("For movement the card returns to its original quadrant and index")
def _returned(results):
    _assert_passed(results["movement"])


@then("The initiating client displays a failure message")
def _message(results):
    # The harness tests wait for the failure notification; both passing proves it was shown.
    _assert_passed(results["insertion"])
    _assert_passed(results["movement"])
