Feature: Workspace and Realtime Canvas Initialization

Scenario: create session success
  Given  the system is initialized with schema 0001_workspace.sql
  When  a POST request is sent to /sessions with feature_name 'Test Feature'
  Then  the response is 201
  And  a session record exists in Supabase with status 'open'
  And  the response body contains a valid session_id and link
  And  tests/api/test_sessions.py::test_create_session_success passes
