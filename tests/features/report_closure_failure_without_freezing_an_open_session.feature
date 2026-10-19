Feature: Irreversible session closure and finalized quadrant retrieval

Scenario: Report closure failure without freezing an open session
  Given  S is open and its participant can edit cards
  And  The closure request will fail before committing
  And  An authoritative state read after the failure returns open
  When  The participant activates Close session
  Then  A visible closure failure indication is shown
  And  S remains open in the database
  And  The participant can continue card operations
