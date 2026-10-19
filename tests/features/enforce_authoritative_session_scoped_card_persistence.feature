Feature: Realtime collaborative card operations

Scenario: Enforce authoritative session-scoped card persistence
  Given  The migrated card schema and card-service integration fixtures are implemented
  When  The test tests/integration/test_cards_supabase.py::test_session_scope_and_soft_delete runs
  Then  It passes
