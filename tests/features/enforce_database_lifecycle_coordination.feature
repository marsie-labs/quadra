Feature: Irreversible session closure and finalized quadrant retrieval

Scenario: Enforce database lifecycle coordination
  Given  The migrated database and application database access role are available
  And  The architecture test covers direct-write bypass attempts, irreversible closure and both session-lock acquisition orders for every supported card mutation
  When  The test tests/integration/test_session_closure.py::test_database_lifecycle_boundary runs
  Then  It passes
