Feature: Irreversible session closure and finalized quadrant retrieval

Scenario: Retrieve complete finalized quadrant data
  Given  S is closed and has four persisted quadrants, including one empty quadrant
  And  S has more active cards than the configured Supabase single-page limit
  And  S also has soft-deleted cards and another session has unrelated cards
  And  Expected card values and deterministic ordering are recorded from persisted state
  When  An unauthenticated caller sends GET /sessions/S/data
  Then  The response status is 200
  And  The response contains session_id S, status closed and exactly S's four quadrant groups
  And  Each quadrant group contains its persisted id, name and position
  And  Each group contains every non-deleted card in that quadrant exactly once, with the empty quadrant represented by an empty cards list
  And  Each card contains its persisted id, content, author_display_name, index, created_at and updated_at
  And  Card values and ordering match persisted state
  And  No soft-deleted card or other session's card is included
