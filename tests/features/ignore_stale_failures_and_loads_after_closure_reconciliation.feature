Feature: Irreversible session closure and finalized quadrant retrieval

Scenario: Ignore stale failures and loads after closure reconciliation
  Given  B has reconciled closed session S to its complete authoritative card snapshot
  And  The test is parameterized over a delayed mutation failure, a pre-closure load response and an overlapping older reconciliation response
  And  The selected response would restore an older card position or omit cards if applied
  When  The selected stale response reaches B
  Then  B's card snapshot is unchanged
  And  No pending overlay returns
  And  B remains read-only
