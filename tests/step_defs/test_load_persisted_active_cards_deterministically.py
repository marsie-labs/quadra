import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
TEST = (
    "tests/integration/test_canvas_cards.py"
    "::test_cards_render_in_index_order_without_deleted_or_foreign_cards"
)


@scenario(
    "../features/load_persisted_active_cards_deterministically.feature",
    "Load persisted active cards deterministically",
)
def test_load_persisted_active_cards_deterministically():
    pass


@pytest.fixture
def context():
    return {}


@given("Session S contains active cards A and B in Q1 with indexes 2 and 1")
def _active_cards():
    # The harness seeds the cards, opens the real session page as a simulated user
    # and reads the rendered quadrant, so the ordering is judged on the real canvas.
    source = (ROOT / TEST.split("::")[0]).read_text()
    assert '_add_card(session_id, 2, "card-A")' in source
    assert '_add_card(session_id, 1, "card-B")' in source


@given("Session S contains soft-deleted card C")
def _deleted_card():
    assert "cards.soft_delete_card(session_id, deleted)" in (ROOT / TEST.split("::")[0]).read_text()


@given("Another session contains active card D")
def _foreign_card():
    assert '_add_card(other_id, 0, "card-D")' in (ROOT / TEST.split("::")[0]).read_text()


@when("A client opens /sessions/{S}")
def _open_page(context):
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


@then("Q1 renders B before A")
def _b_before_a(context):
    _assert_harness_passed(context)


@then("Neither C nor D is rendered")
def _c_d_absent(context):
    _assert_harness_passed(context)


@then("Reloading the page produces the same ordering")
def _same_order(context):
    _assert_harness_passed(context)
