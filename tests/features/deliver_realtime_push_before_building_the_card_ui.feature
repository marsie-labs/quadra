Feature: Realtime collaborative card operations

Scenario: Deliver Realtime push before building the card UI
  Given  Two NiceGUI simulated users A and B are connected to session S
  And  B uses a test-only session-view callback that renders received card identifiers into a DOM event log
  And  The Realtime bridge is subscribed and ready
  And  Periodic database reads and client polling are disabled and instrumented to fail the test
  And  The production card-operation UI is not required by this fixture
  When  A inserts card C in S through an integration harness calling card-service
  Then  B's DOM event log contains C less than 2000ms after the database commit
  And  The recorded delivery path includes a Supabase Realtime notification and a NiceGUI WebSocket update
  And  No periodic database read or client polling request occurs
