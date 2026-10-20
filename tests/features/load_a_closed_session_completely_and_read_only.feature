Feature: Irreversible session closure and finalized quadrant retrieval

Scenario: Load a closed session completely and read-only
  Given  Closed session S contains more active cards than one configured Supabase page can return
  And  Its active cards span all four quadrants and it also contains soft-deleted cards
  When  A participant opens /sessions/S
  Then  Every active card is rendered exactly once in existing deterministic order
  And  No soft-deleted card is rendered
  And  The session is marked closed and all card mutation controls are disabled or absent
