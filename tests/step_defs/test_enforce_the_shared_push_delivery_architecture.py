import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
TEST = "tests/integration/test_realtime.py::test_push_delivery_without_polling"


@scenario(
    "../features/enforce_the_shared_push_delivery_architecture.feature",
    "Enforce the shared push-delivery architecture",
)
def test_enforce_the_shared_push_delivery_architecture():
    pass


@pytest.fixture
def context():
    return {}


@given("The Realtime bridge and its instrumented integration fixtures are implemented")
def _implemented():
    assert (ROOT / "src" / "quadra" / "realtime.py").is_file()
    assert (ROOT / "supabase" / "migrations" / "0002_realtime_publication.sql").is_file()
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
