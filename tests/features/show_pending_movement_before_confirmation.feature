Feature: Realtime collaborative card operations

Scenario: Show pending movement before confirmation
  Given  Card C is visible in Q1
  And  The relocation request is held unresolved by a controllable test barrier
  When  The participant moves C to Q2
  Then  C appears in Q2 with pending status while the request remains unresolved
