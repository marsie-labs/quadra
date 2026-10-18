Feature: Realtime collaborative card operations

Scenario: Propagate every supported card operation to another client
  Given  Clients A and B display the same session with Realtime readiness confirmed
  And  A parameterized fixture selects insert, content edit, soft-delete or relocation
  And  The fixture supplies the existing card required for the selected operation
  And  Polling is disabled and database commit and other-client update timestamps are captured
  When  A performs the selected card operation through the production canvas
  Then  B renders the inserted card, edited content, card removal or relocated card, respectively
  And  The elapsed time from database commit to B's matching render is less than 2000ms
  And  The initiating client shows the confirmed persisted state without a duplicate card
