Feature: Realtime collaborative card operations

Scenario: Load persisted active cards deterministically
  Given  Session S contains active cards A and B in Q1 with indexes 2 and 1
  And  Session S contains soft-deleted card C
  And  Another session contains active card D
  When  A client opens /sessions/{S}
  Then  Q1 renders B before A
  And  Neither C nor D is rendered
  And  Reloading the page produces the same ordering
