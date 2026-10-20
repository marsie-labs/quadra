Feature: Irreversible session closure and finalized quadrant retrieval

Scenario: Keep other sessions editable when one session closes
  Given  S and T are open and have connected views
  And  T has a recorded card snapshot
  When  S is closed
  Then  T remains open and its canvas remains editable
  And  T's persisted card snapshot is unchanged
