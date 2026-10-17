Feature: Realtime collaborative card operations

Scenario: Edit content without replacing authorship
  Given  Card C in session S has content Draft, creator_display_name Alice and creation timestamp T
  And  A participant using display name Bob has opened C's editor
  When  Bob submits Revised as C's content
  Then  C's persisted and rendered content is Revised
  And  C's creator_display_name remains Alice
  And  C's created_at remains T
  And  C's updated_at advances to the server update time
