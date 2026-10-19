import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_insert.py"
TEST = (
    f"{HARNESS.relative_to(ROOT)}"
    "::test_submitted_card_is_persisted_and_shown_with_author_and_timestamp"
)


@scenario(
    "../features/insert_a_card_with_anonymous_authorship.feature",
    "Insert a card with anonymous authorship",
)
def test_insert_a_card_with_anonymous_authorship():
    pass


@pytest.fixture
def context():
    return {}


@given("A client displays session S with quadrant Q1")
def _client_displays_session():
    # The harness opens the real session page as a simulated user.
    assert "_open_ready(user, session_id)" in HARNESS.read_text()


@given("The participant has entered display name Alice")
def _display_name():
    assert 'user.find(marker="display-name").type("Alice")' in HARNESS.read_text()


@given("The card composer contains plain text Compare options")
def _composer_content():
    assert 'user.find(marker="content-0").type("Compare options")' in HARNESS.read_text()


@when("The participant submits the card composer for Q1")
def _submit(context):
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


@then("One active card with content Compare options is persisted in S and Q1")
def _persisted(context):
    _assert_harness_passed(context)


@then("Its creator_display_name is Alice")
def _creator(context):
    _assert_harness_passed(context)


@then("Its created_at and updated_at are server-generated timestamps")
def _timestamps(context):
    _assert_harness_passed(context)


@then("The confirmed card displays Alice and its creation timestamp")
def _displayed(context):
    _assert_harness_passed(context)
