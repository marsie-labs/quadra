import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_edit.py"
TEST = (
    f"{HARNESS.relative_to(ROOT)}"
    "::test_edited_content_is_persisted_and_shown_without_replacing_authorship"
)


@scenario(
    "../features/edit_content_without_replacing_authorship.feature",
    "Edit content without replacing authorship",
)
def test_edit_content_without_replacing_authorship():
    pass


@pytest.fixture
def context():
    return {}


@given(
    "Card C in session S has content Draft, creator_display_name Alice and creation timestamp T"
)
def _card_exists():
    # The harness inserts Alice's card through the real card service.
    assert 'cards.insert_card(session_id, quadrant_id, 0, "Draft", "Alice")' in HARNESS.read_text()


@given("A participant using display name Bob has opened C's editor")
def _editor_opened():
    text = HARNESS.read_text()
    assert 'user.find(marker="display-name").type("Bob")' in text
    assert 'user.find(marker=f"edit-{card.id}").click()' in text


@when("Bob submits Revised as C's content")
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


@then("C's persisted and rendered content is Revised")
def _content(context):
    _assert_harness_passed(context)


@then("C's creator_display_name remains Alice")
def _creator(context):
    _assert_harness_passed(context)


@then("C's created_at remains T")
def _created(context):
    _assert_harness_passed(context)


@then("C's updated_at advances to the server update time")
def _updated(context):
    _assert_harness_passed(context)
