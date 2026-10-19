Feature: Realtime collaborative card operations

Scenario: Relocate a card and persist ordering
  Given  Active card C is in Q1 at index 0 in session S
  And  Q2 belongs to S
  When  A participant moves C to Q2 with target index 3
  Then  C's persisted quadrant is Q2 and index is 3
  And  A fresh page load places C in Q2 according to index then card-id ordering
  And  C's original creator and creation timestamp are unchanged
