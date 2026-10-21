import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_closure_push.py"
TEST = (
    f"{HARNESS.relative_to(ROOT)}"
    "::test_closure_reaches_another_connected_view_read_only_without_polling"
)


@scenario(
    "../features/push_closure_to_another_connected_participant.feature",
    "Push closure to another connected participant",
)
def test_push_closure_to_another_connected_participant():
    pass


@pytest.fixture
def context():
    return {}


@given(
    "Two views A and B are registered for open session S through the existing Realtime bridge"
)
def _registered():
    # The harness opens the production page for two users and waits for Realtime readiness.
    source = HARNESS.read_text()
    assert "user_b = create_user()" in source and "_open_ready(user_b, session_id)" in source


@given("B currently exposes insert, edit, move and soft-delete controls")
def _controls():
    source = HARNESS.read_text()
    assert 'should_see(marker=f"edit-{card.id}"' in source
    assert "all(button.enabled for button in _submit_buttons(user_b))" in source


@when("A closes S successfully", target_fixture="result")
def _close():
    return subprocess.run(  # nosec B603
        [sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_passed(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, result.stdout + result.stderr
    assert " passed" in result.stdout and " skipped" not in result.stdout


@then("B shows S as closed in less than 2000ms from the closure database commit")
def _fast(result):
    _assert_passed(result)
    source = HARNESS.read_text()
    assert "page - commit < LATENCY_LIMIT" in source
    assert "socket - commit < LATENCY_LIMIT" in source


@then("B cannot initiate insert, edit, move or soft-delete actions")
def _read_only(result):
    _assert_passed(result)
    assert "_try_every_mutation(canvas_b, card_id)" in HARNESS.read_text()


@then("The update uses the existing Supabase-to-NiceGUI push path without polling")
def _pushed(result):
    _assert_passed(result)
    source = HARNESS.read_text()
    assert "_no_timer" in source and "status_reads == []" in source
