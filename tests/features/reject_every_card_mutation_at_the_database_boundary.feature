Feature: Irreversible session closure and finalized quadrant retrieval

Scenario: Reject every card mutation at the database boundary
  Given  Session S is closed and contains card C
  And  The test is parameterized over insert, content edit, quadrant/index move and setting deleted_at
  And  The test is parameterized over the card-service path and direct database writes using the application's database access role
  When  The selected mutation is submitted through the selected path
  Then  The mutation is rejected with the closed-session error
  And  No card record in S differs from the pre-request snapshot
