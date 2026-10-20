import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "integration" / "test_canvas_closed_load.py"
TEST = (
    f"{HARNESS.relative_to(ROOT)}"
    "::test_closed_session_loads_every_active_card_once_and_read_only"
)


@scenario(
    "../features/load_a_closed_session_completely_and_read_only.feature",
    "Load a closed session completely and read-only",
)
def test_load_a_closed_session_completely_and_read_only():
    pass


@pytest.fixture
def context():
    return {}


@given(
    "Closed session S contains more active cards than one configured Supabase page can return"
)
def _more_than_a_page():
    # The harness shrinks the page to 2 rows and seeds 6 active cards, then closes S.
    text = HARNESS.read_text()
    assert 'monkeypatch.setattr(cards, "_PAGE_SIZE", 2)' in text
    assert "close_session(session_id) == \"closed\"" in text


@given("Its active cards span all four quadrants and it also contains soft-deleted cards")
def _all_quadrants_and_deleted():
    text = HARNESS.read_text()
    assert all(f"({position}, " in text for position in range(4))
    assert "cards.soft_delete_card" in text


@when("A participant opens /sessions/S")
def _open(context):
    context["result"] = subprocess.run(  # nosec B603
        [sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@then("Every active card is rendered exactly once in existing deterministic order")
def _every_card_once(context):
    result = context["result"]
    assert result.returncode == 0, result.stdout + result.stderr
    assert " passed" in result.stdout and " skipped" not in result.stdout


@then("No soft-deleted card is rendered")
def _no_deleted(context):
    assert context["result"].returncode == 0
    assert 'startswith("gone-")' in HARNESS.read_text()


@then("The session is marked closed and all card mutation controls are disabled or absent")
def _read_only(context):
    assert context["result"].returncode == 0
    text = HARNESS.read_text()
    assert 'should_see(marker="session-closed")' in text
    assert "_MUTATION_MARKS" in text
