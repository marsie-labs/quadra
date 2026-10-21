Feature: Irreversible session closure and finalized quadrant retrieval

Scenario: Enforce monotonic closed-canvas reconciliation
  Given  The canvas test harness can pause mutation responses, paginate loads and reorder session/card notifications
  And  Fixtures include pre-closure commits, rejected pending operations and closure during initial loading
  When  The test tests/integration/test_canvas_closure.py::test_closure_dominates_stale_async_results runs
  Then  It passes
