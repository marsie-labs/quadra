import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_closure_isolation.py"
TEST = (
    f"{HARNESS.relative_to(ROOT)}"
    "::test_closing_one_session_leaves_another_open_session_untouched"
)


@scenario(
    "../features/keep_other_sessions_editable_when_one_session_closes.feature",
    "Keep other sessions editable when one session closes",
)
def test_keep_other_sessions_editable_when_one_session_closes():
    pass


@pytest.fixture
def context():
    return {}


@given("S and T are open and have connected views")
def _connected():
    # The harness opens the production page of each session for its own user.
    source = HARNESS.read_text()
    assert "_open_ready(user, session_s)" in source and "_open_ready(user_t, session_t)" in source


@given("T has a recorded card snapshot")
def _snapshot():
    assert "before = _snapshot(session_t)" in HARNESS.read_text()


@when("S is closed", target_fixture="result")
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


@then("T remains open and its canvas remains editable")
def _editable(result):
    _assert_passed(result)
    source = HARNESS.read_text()
    assert "_assert_editable(user_t, canvas_t, card_t.id)" in source
    assert "canvas_t.changes == []" in source


@then("T's persisted card snapshot is unchanged")
def _unchanged(result):
    _assert_passed(result)
    assert "_snapshot(canvas_t._session_id) == before" in HARNESS.read_text()
