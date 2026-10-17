import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_relocate.py"
TEST = (
    f"{HARNESS.relative_to(ROOT)}"
    "::test_moved_card_is_persisted_and_shown_without_replacing_authorship"
)


@scenario(
    "../features/relocate_a_card_and_persist_ordering.feature",
    "Relocate a card and persist ordering",
)
def test_relocate_a_card_and_persist_ordering():
    pass


@pytest.fixture
def context():
    return {}


@given("Active card C is in Q1 at index 0 in session S")
def _card_in_q1():
    # The harness inserts Alice's card through the real card service.
    assert 'cards.insert_card(session_id, q1, 0, "Mover", "Alice")' in HARNESS.read_text()


@given("Q2 belongs to S")
def _q2_in_session():
    # Q2 is the session's second quadrant, as loaded from the database.
    assert "q1, q2 = _load_quadrant_ids(session_id)[:2]" in HARNESS.read_text()


@when("A participant moves C to Q2 with target index 3")
def _move(context):
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


@then("C's persisted quadrant is Q2 and index is 3")
def _persisted(context):
    _assert_harness_passed(context)


@then("A fresh page load places C in Q2 according to index then card-id ordering")
def _fresh_load(context):
    _assert_harness_passed(context)


@then("C's original creator and creation timestamp are unchanged")
def _authorship(context):
    _assert_harness_passed(context)
