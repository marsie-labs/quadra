Feature: Realtime collaborative card operations

Scenario: Recover from rejected optimistic operations
  Given  A parameterized client fixture has either a pending insertion or a pending movement from Q1 to Q2
  And  The persisted state has not changed
  When  The pending operation receives a persistence failure
  Then  For insertion the pending card is removed
  And  For movement the card returns to its original quadrant and index
  And  The initiating client displays a failure message
