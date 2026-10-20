import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

ROOT = Path(__file__).resolve().parents[2]
TEST = "tests/integration/test_session_closure.py::test_anonymous_close_and_finalized_retrieval"


@scenario(
    "../features/enforce_the_accepted_unauthenticated_lifecycle_surface.feature",
    "Enforce the accepted unauthenticated lifecycle surface",
)
def test_enforce_the_accepted_unauthenticated_lifecycle_surface():
    pass


@pytest.fixture
def context():
    return {}


@given("The API and canvas test fixtures provide clients without credentials or roles")
def _fixtures():
    source = (ROOT / TEST.split("::")[0]).read_text()
    body = source.split("async def test_anonymous_close_and_finalized_retrieval", 1)[1]
    assert "(client, user: User, session_page)" in body
    for credential in ("Authorization", "headers=", "auth=", "set local role"):
        assert credential not in body


@given("Fixtures include an open session, a closed session and an unknown session UUID")
def _sessions():
    body = (ROOT / TEST.split("::")[0]).read_text().split(
        "async def test_anonymous_close_and_finalized_retrieval", 1
    )[1]
    for expected in ("== 409", "== 200", "== 404", "uuid.uuid4()"):
        assert expected in body


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
