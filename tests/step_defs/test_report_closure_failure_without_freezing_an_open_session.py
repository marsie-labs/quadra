import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_close_failure.py"
TEST = (
    f"{HARNESS.relative_to(ROOT)}"
    "::test_failed_closure_is_reported_and_open_session_stays_editable"
)


@scenario(
    "../features/report_closure_failure_without_freezing_an_open_session.feature",
    "Report closure failure without freezing an open session",
)
def test_report_closure_failure_without_freezing_an_open_session():
    pass


@pytest.fixture
def context():
    return {}


@given("S is open and its participant can edit cards")
def _editable():
    # The harness opens a real open session and waits for its composer to be enabled.
    assert "_open_editable(user, session_id)" in HARNESS.read_text()


@given("The closure request will fail before committing")
def _closure_fails():
    assert 'monkeypatch.setattr(canvas, "close_session", _failing_close)' in HARNESS.read_text()


@given("An authoritative state read after the failure returns open")
def _read_open():
    # The canvas reads the real database, which still holds the session as open.
    assert 'get_session_status(session_id) == "open"' in HARNESS.read_text()


@when("The participant activates Close session")
def _activate(context):
    context["result"] = subprocess.run(  # nosec B603
        [sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@then("A visible closure failure indication is shown")
def _failure_shown(context):
    result = context["result"]
    assert result.returncode == 0, result.stdout + result.stderr
    assert " passed" in result.stdout and " skipped" not in result.stdout
    assert 'should_see(marker="closure-failed"' in HARNESS.read_text()


@then("S remains open in the database")
def _stays_open(context):
    assert context["result"].returncode == 0
    assert 'get_session_status(session_id) == "open"' in HARNESS.read_text()


@then("The participant can continue card operations")
def _can_continue(context):
    assert context["result"].returncode == 0
    assert 'should_see("Still editable"' in HARNESS.read_text()
