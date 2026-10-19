import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
TEST = "tests/integration/test_cards_supabase.py::test_session_scope_and_soft_delete"


@scenario(
    "../features/enforce_authoritative_session_scoped_card_persistence.feature",
    "Enforce authoritative session-scoped card persistence",
)
def test_enforce_authoritative_session_scoped_card_persistence():
    pass


@pytest.fixture
def context():
    return {}


@given("The migrated card schema and card-service integration fixtures are implemented")
def _implemented():
    assert (ROOT / "supabase" / "migrations" / "0003_cards.sql").is_file()
    assert (ROOT / "src" / "quadra" / "cards.py").is_file()
    assert (ROOT / TEST.split("::")[0]).is_file()


@when(f"The test {TEST} runs")
def _run(context):
    context["result"] = subprocess.run(  # nosec B603
        [sys.executable, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@then("It passes")
def _passes(context):
    result = context["result"]
    assert result.returncode == 0, result.stdout + result.stderr
    assert " passed" in result.stdout and " skipped" not in result.stdout
