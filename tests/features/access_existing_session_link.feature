Feature: Workspace and Realtime Canvas Initialization

Scenario: access existing session link
  Given  a valid session with id '3f2b8c1e-6d4a-4b7e-9c2f-1a5d8e7b4c90' exists in Supabase
  When  a GET request is made to /sessions/3f2b8c1e-6d4a-4b7e-9c2f-1a5d8e7b4c90
  Then  the /sessions/3f2b8c1e-6d4a-4b7e-9c2f-1a5d8e7b4c90 page renders the canvas with quadrants IS, IS NOT, DOES, DOES NOT
