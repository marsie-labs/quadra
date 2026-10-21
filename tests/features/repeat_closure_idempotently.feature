Feature: Irreversible session closure and finalized quadrant retrieval

Scenario: Repeat closure idempotently
  Given  Session S is already closed with a recorded card snapshot
  When  The session service is asked to close S again
  Then  The service returns the closed state without an error
  And  The stored status and card snapshot remain unchanged
