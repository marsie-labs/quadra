import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
TEST = "tests/integration/test_session_closure.py::test_database_lifecycle_boundary"


@scenario(
    "../features/enforce_database_lifecycle_coordination.feature",
    "Enforce database lifecycle coordination",
)
def test_enforce_database_lifecycle_coordination():
    pass


@pytest.fixture
def context():
    return {}


@given("The migrated database and application database access role are available")
def _implemented():
    assert (ROOT / "supabase" / "migrations" / "0005_session_closure.sql").is_file()
    assert (ROOT / TEST.split("::")[0]).is_file()


@given(
    "The architecture test covers direct-write bypass attempts, irreversible closure and "
    "both session-lock acquisition orders for every supported card mutation"
)
def _covered():
    source = (ROOT / TEST.split("::")[0]).read_text()
    for part in ("_closure_wins_the_lock", "_mutation_wins_the_lock", "_closure_is_idempotent"):
        assert part in source
    assert "def test_database_lifecycle_boundary" in source


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
