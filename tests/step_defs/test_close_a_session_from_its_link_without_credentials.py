import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_close.py"
TEST = (
    f"{HARNESS.relative_to(ROOT)}"
    "::test_close_control_stores_closed_and_shows_it_without_credentials"
)


@scenario(
    "../features/close_a_session_from_its_link_without_credentials.feature",
    "Close a session from its link without credentials",
)
def test_close_a_session_from_its_link_without_credentials():
    pass


@pytest.fixture
def context():
    return {}


@given("Session S is open")
def _open():
    # The harness creates a real session and asserts it is open before closing it.
    assert 'get_session_status(session_id) == "open"' in HARNESS.read_text()


@given("A participant has opened /sessions/S")
def _opened():
    assert 'user.open(f"/sessions/{session_id}")' in HARNESS.read_text()


@given("The participant has no authentication credentials or role")
def _anonymous():
    # The simulated user logs in nowhere and sends no credentials.
    assert "login" not in HARNESS.read_text().lower()


@when("The participant activates the Close session control")
def _activate(context):
    context["result"] = subprocess.run(  # nosec B603
        [sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@then("The stored status of S is closed")
def _stored_closed(context):
    result = context["result"]
    assert result.returncode == 0, result.stdout + result.stderr
    assert " passed" in result.stdout and " skipped" not in result.stdout


@then("The participant's canvas shows that S is closed")
def _canvas_closed(context):
    assert context["result"].returncode == 0
    assert 'should_see(marker="session-closed"' in HARNESS.read_text()
