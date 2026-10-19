Feature: Irreversible session closure and finalized quadrant retrieval

Scenario: Close a session from its link without credentials
  Given  Session S is open
  And  A participant has opened /sessions/S
  And  The participant has no authentication credentials or role
  When  The participant activates the Close session control
  Then  The stored status of S is closed
  And  The participant's canvas shows that S is closed
