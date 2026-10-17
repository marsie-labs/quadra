import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_delete.py"
TEST = (
    f"{HARNESS.relative_to(ROOT)}"
    "::test_deleted_card_leaves_every_canvas_and_keeps_its_row"
)


@scenario(
    "../features/soft_delete_without_losing_the_database_record.feature",
    "Soft-delete without losing the database record",
)
def test_soft_delete_without_losing_the_database_record():
    pass


@pytest.fixture
def context():
    return {}


@given("Active card C is visible in two connected clients for session S")
def _card_visible():
    # The harness opens the real session page as two simulated users and asserts the
    # card is rendered in both before anything is deleted.
    text = HARNESS.read_text()
    assert "await _open_with_card([user, other], session_id)" in text
    assert '"Obsolete" in _texts(user) and "Obsolete" in _texts(other)' in text


@when("One participant deletes C")
def _delete(context):
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


@then("C is absent from both canvases after confirmation")
def _absent(context):
    _assert_harness_passed(context)


@then("C's database row still exists with a non-null deleted_at")
def _row_kept(context):
    _assert_harness_passed(context)


@then("A fresh active-card query for S excludes C")
def _query_excludes(context):
    _assert_harness_passed(context)
