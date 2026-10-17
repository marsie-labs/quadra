Feature: Workspace and Realtime Canvas Initialization

Scenario: access non-existent session link
  Given  no session with id '00000000-0000-4000-8000-000000000000' exists in Supabase
  When  a GET request is made to /sessions/00000000-0000-4000-8000-000000000000
  Then  the /sessions/00000000-0000-4000-8000-000000000000 page displays 'Session not found'
