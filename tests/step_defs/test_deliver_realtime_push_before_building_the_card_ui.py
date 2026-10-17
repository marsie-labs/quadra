import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_realtime_cards.py"
TEST = f"{HARNESS.relative_to(ROOT)}::test_card_insert_reaches_second_user_without_polling"


@scenario(
    "../features/deliver_realtime_push_before_building_the_card_ui.feature",
    "Deliver Realtime push before building the card UI",
)
def test_deliver_realtime_push_before_building_the_card_ui():
    pass


@pytest.fixture
def context():
    return {}


@given("Two NiceGUI simulated users A and B are connected to session S")
def _users_connected():
    # The harness opens both simulated users on the session page itself.
    assert "create_user" in HARNESS.read_text()


@given(
    "B uses a test-only session-view callback that renders received card identifiers "
    "into a DOM event log"
)
def _dom_log():
    assert "def _mount_card_log_page" in HARNESS.read_text()


@given("The Realtime bridge is subscribed and ready")
def _bridge_ready():
    assert "realtime.is_ready(session_id)" in HARNESS.read_text()


@given("Periodic database reads and client polling are disabled and instrumented to fail the test")
def _polling_forbidden():
    assert "def _forbid_polling" in HARNESS.read_text()


@given("The production card-operation UI is not required by this fixture")
def _no_card_ui():
    assert "quadra.app" not in HARNESS.read_text()


@when("A inserts card C in S through an integration harness calling card-service")
def _insert_and_observe(context):
    context["result"] = subprocess.run(  # nosec B603
        [sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_harness_passed(context):
    result = context["result"]
    assert result.returncode == 0, result.stdout + result.stderr
    assert " passed" in result.stdout and " skipped" not in result.stdout


@then("B's DOM event log contains C less than 2000ms after the database commit")
def _dom_contains_card(context):
    _assert_harness_passed(context)


@then(
    "The recorded delivery path includes a Supabase Realtime notification "
    "and a NiceGUI WebSocket update"
)
def _path_recorded(context):
    _assert_harness_passed(context)


@then("No periodic database read or client polling request occurs")
def _no_polling(context):
    _assert_harness_passed(context)
