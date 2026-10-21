Feature: Irreversible session closure and finalized quadrant retrieval

Scenario: Push closure to another connected participant
  Given  Two views A and B are registered for open session S through the existing Realtime bridge
  And  B currently exposes insert, edit, move and soft-delete controls
  When  A closes S successfully
  Then  B shows S as closed in less than 2000ms from the closure database commit
  And  B cannot initiate insert, edit, move or soft-delete actions
  And  The update uses the existing Supabase-to-NiceGUI push path without polling
