Feature: Irreversible session closure and finalized quadrant retrieval

Scenario: Enforce the accepted unauthenticated lifecycle surface
  Given  The API and canvas test fixtures provide clients without credentials or roles
  And  Fixtures include an open session, a closed session and an unknown session UUID
  When  The test tests/integration/test_session_closure.py::test_anonymous_close_and_finalized_retrieval runs
  Then  It passes
