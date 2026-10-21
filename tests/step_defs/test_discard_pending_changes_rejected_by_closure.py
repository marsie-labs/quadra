import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_closure_pending.py"
TEST = (
    f"{HARNESS.relative_to(ROOT)}"
    "::test_closure_discards_the_pending_operation_and_says_it_was_not_saved"
)


@scenario(
    "../features/discard_pending_changes_rejected_by_closure.feature",
    "Discard pending changes rejected by closure",
)
def test_discard_pending_changes_rejected_by_closure():
    pass


@pytest.fixture
def context():
    return {}


@given("View B has a pending card operation in open session S")
def _pending():
    assert "assert canvas._requests and not canvas._closed" in HARNESS.read_text()


@given("The test is parameterized over insert, edit, move and soft-delete")
def _parameterized():
    text = HARNESS.read_text()
    assert '@pytest.mark.parametrize("operation", list(_OPERATIONS))' in text
    assert all(f'"{name}": (' in text for name in ("insert", "edit", "move", "delete"))


@given("The operation has not committed before S closes")
def _uncommitted():
    assert "class _Held" in HARNESS.read_text()


@given("B receives S's committed closure through Realtime")
def _closure_push():
    assert "await _until(lambda: canvas._closed)" in HARNESS.read_text()


@given("The operation response is still delayed")
def _delayed():
    assert "assert canvas._requests  # the response is still delayed" in HARNESS.read_text()


@when("B applies the closure notification")
def _apply(context):
    context["result"] = subprocess.run(  # nosec B603
        [sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@then(
    "B removes pending optimistic overlays and renders the complete authoritative "
    "closed-session cards"
)
def _overlays_removed(context):
    result = context["result"]
    assert result.returncode == 0, result.stdout + result.stderr
    assert " passed" in result.stdout and " skipped" not in result.stdout


@then(
    "An uncommitted insertion is absent and an uncommitted edit, move or deletion "
    "is not displayed as persisted"
)
def _not_persisted(context):
    assert context["result"].returncode == 0
    assert "_assert_authoritative_closed_cards" in HARNESS.read_text()


@then(
    "B displays that the pending change was not saved because the session closed, "
    "since the authoritative closed-session cards do not reflect it"
)
def _not_saved(context):
    assert context["result"].returncode == 0
    assert "assert _notice(user).visible and _notice(user).text == _NOT_SAVED" in (
        HARNESS.read_text()
    )


@then("B remains read-only")
def _read_only(context):
    assert context["result"].returncode == 0
    assert "_assert_read_only(user, canvas)" in HARNESS.read_text()
