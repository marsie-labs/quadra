Feature: Irreversible session closure and finalized quadrant retrieval

Scenario: Receive closure during the initial session load
  Given  B's initial load of open session S is paused with an older partial card result
  And  B's Realtime registration is active
  And  S closes and B receives the closure notification
  When  The initial load response is released
  Then  B settles on the complete authoritative closed-session card set
  And  The older response neither omits cards nor restores editing
  And  B displays S as closed
