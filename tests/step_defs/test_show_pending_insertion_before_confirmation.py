import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_pending_insert.py"
TEST = (
    f"{HARNESS.relative_to(ROOT)}"
    "::test_pending_card_shows_in_target_quadrant_until_the_insert_resolves"
)


@scenario(
    "../features/show_pending_insertion_before_confirmation.feature",
    "Show pending insertion before confirmation",
)
def test_show_pending_insertion_before_confirmation():
    pass


@pytest.fixture
def context():
    return {}


@given("A participant has composed a card in Q1")
def _composed():
    # The harness types a name and text into the real page's first composer.
    assert 'user.find(marker="content-0").type("Compare options")' in HARNESS.read_text()


@given("The insert request is held unresolved by a controllable test barrier")
def _barrier():
    assert "class _Barrier" in HARNESS.read_text()


@when("The participant submits the card")
def _submit(context):
    context["result"] = subprocess.run(  # nosec B603
        [sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@then("A visibly pending card appears in Q1 while the request remains unresolved")
def _pending(context):
    result = context["result"]
    assert result.returncode == 0, result.stdout + result.stderr
    assert " passed" in result.stdout and " skipped" not in result.stdout
