import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_closure_initial_load.py"
TESTS = [
    f"{HARNESS.relative_to(ROOT)}::{name}"
    for name in (
        "test_closure_during_the_initial_load_settles_on_the_complete_closed_set",
        "test_failed_reconciliation_during_the_initial_load_offers_retry",
    )
]


@scenario(
    "../features/receive_closure_during_the_initial_session_load.feature",
    "Receive closure during the initial session load",
)
def test_receive_closure_during_the_initial_session_load():
    pass


@pytest.fixture
def context():
    return {}


@given("B's initial load of open session S is paused with an older partial card result")
def _paused():
    text = HARNESS.read_text()
    assert "hold.result = ([first], list(quadrant_ids))" in text
    assert "_gated_canvases(monkeypatch, hold)" in text


@given("B's Realtime registration is active")
def _registered():
    assert "assert realtime.view_ids(session_id)" in HARNESS.read_text()


@given("S closes and B receives the closure notification")
def _closes():
    assert "await _close_elsewhere(session_id, canvas)" in HARNESS.read_text()


@when("The initial load response is released")
def _released(context):
    context["result"] = subprocess.run(  # nosec B603
        [sys.executable, "-m", "pytest", *TESTS, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@then("B settles on the complete authoritative closed-session card set")
def _complete(context):
    result = context["result"]
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout and " skipped" not in result.stdout
    assert "canvas._cards == {card.id: card for card in persisted}" in HARNESS.read_text()


@then("The older response neither omits cards nor restores editing")
def _not_restored(context):
    assert context["result"].returncode == 0
    text = HARNESS.read_text()
    assert '{"Older", "Newer"} <= set(_shown_texts(canvas))' in text
    assert "_assert_read_only(user, canvas)" in text


@then("B displays S as closed")
def _closed(context):
    assert context["result"].returncode == 0
    assert "assert _closed_label(canvas)" in HARNESS.read_text()
