Feature: Realtime collaborative card operations

Scenario: Soft-delete without losing the database record
  Given  Active card C is visible in two connected clients for session S
  When  One participant deletes C
  Then  C is absent from both canvases after confirmation
  And  C's database row still exists with a non-null deleted_at
  And  A fresh active-card query for S excludes C
