import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_pending_move.py"
TEST = (
    f"{HARNESS.relative_to(ROOT)}"
    "::test_moved_card_shows_pending_in_target_quadrant_until_the_move_resolves"
)


@scenario(
    "../features/show_pending_movement_before_confirmation.feature",
    "Show pending movement before confirmation",
)
def test_show_pending_movement_before_confirmation():
    pass


@pytest.fixture
def context():
    return {}


@given("Card C is visible in Q1")
def _visible():
    # The harness inserts C into the first quadrant and waits for the real page to show it.
    assert 'cards.insert_card(session_id, q1, 0, "Mover", "Alice")' in HARNESS.read_text()


@given("The relocation request is held unresolved by a controllable test barrier")
def _barrier():
    assert "class _Barrier" in HARNESS.read_text()


@when("The participant moves C to Q2")
def _move(context):
    context["result"] = subprocess.run(  # nosec B603
        [sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@then("C appears in Q2 with pending status while the request remains unresolved")
def _pending(context):
    result = context["result"]
    assert result.returncode == 0, result.stdout + result.stderr
    assert " passed" in result.stdout and " skipped" not in result.stdout
