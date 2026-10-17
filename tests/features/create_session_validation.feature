Feature: Workspace and Realtime Canvas Initialization

Scenario: create session validation fails on empty input
  Given  the API route exists
  When  a POST request is sent to /sessions with feature_name ''
  Then  the response is 422
  And  no session record is created in Supabase
  And  tests/api/test_sessions.py::test_create_session_validation passes
