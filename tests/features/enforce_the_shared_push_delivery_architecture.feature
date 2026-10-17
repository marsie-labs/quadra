Feature: Realtime collaborative card operations

Scenario: Enforce the shared push-delivery architecture
  Given  The Realtime bridge and its instrumented integration fixtures are implemented
  When  The test tests/integration/test_realtime.py::test_push_delivery_without_polling runs
  Then  It passes
