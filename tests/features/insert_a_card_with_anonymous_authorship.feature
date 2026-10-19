Feature: Realtime collaborative card operations

Scenario: Insert a card with anonymous authorship
  Given  A client displays session S with quadrant Q1
  And  The participant has entered display name Alice
  And  The card composer contains plain text Compare options
  When  The participant submits the card composer for Q1
  Then  One active card with content Compare options is persisted in S and Q1
  And  Its creator_display_name is Alice
  And  Its created_at and updated_at are server-generated timestamps
  And  The confirmed card displays Alice and its creation timestamp
