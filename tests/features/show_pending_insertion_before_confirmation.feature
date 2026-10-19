Feature: Realtime collaborative card operations

Scenario: Show pending insertion before confirmation
  Given  A participant has composed a card in Q1
  And  The insert request is held unresolved by a controllable test barrier
  When  The participant submits the card
  Then  A visibly pending card appears in Q1 while the request remains unresolved
