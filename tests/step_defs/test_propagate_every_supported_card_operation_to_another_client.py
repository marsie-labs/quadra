import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_propagation.py"
PROPAGATION = "test_card_operation_reaches_the_other_client_without_duplicates"
PUSH_FIRST = "test_confirmed_insert_is_reconciled_when_its_push_arrives_first"


@scenario(
    "../features/propagate_every_supported_card_operation_to_another_client.feature",
    "Propagate every supported card operation to another client",
)
def test_propagate_every_supported_card_operation_to_another_client():
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


@given("Clients A and B display the same session with Realtime readiness confirmed")
def _clients():
    # The harness opens the production page for two users and waits for loaded cards.
    source = HARNESS.read_text()
    assert "user_b = create_user()" in source and "_open_ready(user_b, session_id)" in source


@given("A parameterized fixture selects insert, content edit, soft-delete or relocation")
def _parameterized():
    source = HARNESS.read_text()
    assert '@pytest.mark.parametrize("name", OPERATIONS)' in source
    for operation in ('"insert"', '"edit"', '"soft-delete"', '"relocate"'):
        assert operation in source


@given("The fixture supplies the existing card required for the selected operation")
def _existing():
    assert "cards.insert_card(session_id, quadrant_id, 0," in HARNESS.read_text()


@given("Polling is disabled and database commit and other-client update timestamps are captured")
def _instrumented():
    source = HARNESS.read_text()
    assert "_forbid_polling(monkeypatch)" in source and "class _Timed" in source
    assert "rendered_at - timed.started" in source and "elapsed < LATENCY_LIMIT" in source


@when(
    "A performs the selected card operation through the production canvas",
    target_fixture="results",
)
def _perform():
    return {"propagation": _run(PROPAGATION), "push_first": _run(PUSH_FIRST)}


@then(
    "B renders the inserted card, edited content, card removal or relocated card, respectively"
)
def _rendered(results):
    _assert_passed(results["propagation"])


@then("The elapsed time from database commit to B's matching render is less than 2000ms")
def _fast(results):
    # The same parameterized runs assert the elapsed time before they can pass.
    _assert_passed(results["propagation"])


@then("The initiating client shows the confirmed persisted state without a duplicate card")
def _confirmed(results):
    _assert_passed(results["propagation"])
    _assert_passed(results["push_first"])
