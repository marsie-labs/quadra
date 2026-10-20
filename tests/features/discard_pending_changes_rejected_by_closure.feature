Feature: Irreversible session closure and finalized quadrant retrieval

Scenario: Discard pending changes rejected by closure
  Given  View B has a pending card operation in open session S
  And  The test is parameterized over insert, edit, move and soft-delete
  And  The operation has not committed before S closes
  And  B receives S's committed closure through Realtime
  And  The operation response is still delayed
  When  B applies the closure notification
  Then  B removes pending optimistic overlays and renders the complete authoritative closed-session cards
  And  An uncommitted insertion is absent and an uncommitted edit, move or deletion is not displayed as persisted
  And  B displays that the pending change was not saved because the session closed
  And  B remains read-only
